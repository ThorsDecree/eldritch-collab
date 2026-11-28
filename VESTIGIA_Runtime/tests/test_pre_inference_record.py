import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage, ProviderReply
from vestigia.runtime import CoreRuntime


class Provider:
    def __init__(self, home):
        self.home = home
        self.requests = []

    def admission_identity(self, route):
        return {"provider": "test", "model": "approved"}

    def complete(self, request):
        record = json.loads((self.home / "traces" / "pre-inference" /
                             f"{request.turn_id}.json").read_text(encoding="utf-8"))
        assert record["author"] == "runtime_configured"
        self.requests.append(request)
        return ProviderReply(text="hello", provider="test", model="approved",
                             reported_model="snapshot")


class PreInferenceRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = initialize_home(Path(self.temp.name) / "home", name="Synthetic")
        self.config_path = self.home / "home.yaml"
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.config["model_approval"] = {
            "enabled": True, "pre_dispatch": True,
            "routes": {"default": {"provider": "test", "model": "approved",
                                   "reported_model": "snapshot"}},
        }
        self.config["first_statement"] = {
            "enabled": True, "switch_phrase": None,
            "pre_inference_record": "A configured host record.",
        }
        self.config["curation"]["enabled"] = False
        self.save_config()

    def save_config(self):
        self.config_path.write_text(yaml.safe_dump(self.config), encoding="utf-8")

    def runtime(self):
        provider = Provider(self.home)
        return CoreRuntime.from_home(self.home, provider=provider), provider

    def test_record_precedes_middleware_and_provider_and_survives_reopen(self):
        runtime, provider = self.runtime()
        from vestigia.composition import run_chat_middleware as original

        def inspect_middleware(rt, message, route, core):
            paths = list((self.home / "traces" / "pre-inference").glob("*.json"))
            self.assertEqual(len(paths), 1)
            record = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(record["statement"], "A configured host record.")
            self.assertEqual(record["prior_reads_and_calls"], "unknown")
            self.assertEqual(record["configured_approved_identity"]["model"], "approved")
            self.assertNotIn("synthetic private message", paths[0].read_text())
            self.assertFalse(provider.requests)
            return original(rt, message, route, core)

        with patch("vestigia.composition.run_chat_middleware", inspect_middleware):
            result = runtime.chat(NormalizedMessage(content="synthetic private message"))
        self.assertFalse(result.suppressed)
        self.assertEqual(len(provider.requests), 2)
        path = self.home / "traces" / "pre-inference" / f"{result.turn_id}.json"
        self.assertEqual(json.loads(path.read_text())["turn_id"], result.turn_id)
        reopened, _ = self.runtime()
        result2 = reopened.chat(NormalizedMessage(content="second"))
        self.assertNotEqual(result.turn_id, result2.turn_id)
        self.assertEqual(len(list(path.parent.glob("*.json"))), 2)

    def test_failed_fsync_stops_before_middleware_and_provider(self):
        runtime, provider = self.runtime()
        with patch("vestigia.pre_inference_record.os.fsync", side_effect=OSError("synthetic")):
            with patch("vestigia.composition.run_chat_middleware", side_effect=AssertionError("middleware entered")):
                with self.assertRaises(OSError):
                    runtime.chat(NormalizedMessage(content="synthetic"))
        self.assertFalse(provider.requests)
        self.assertFalse(list((self.home / "traces" / "pre-inference").glob("*.json")))

    def test_publication_failure_stops_before_middleware_and_provider(self):
        runtime, provider = self.runtime()
        with patch("vestigia.pre_inference_record.os.link", side_effect=OSError("synthetic")):
            with patch("vestigia.composition.run_chat_middleware", side_effect=AssertionError("middleware entered")):
                with self.assertRaises(OSError):
                    runtime.chat(NormalizedMessage(content="synthetic"))
        self.assertFalse(provider.requests)
        self.assertFalse(list((self.home / "traces" / "pre-inference").glob("*.json")))

    def test_refused_route_still_has_host_record_but_no_inference(self):
        self.config["model_approval"]["routes"] = {}
        self.save_config()
        runtime, provider = self.runtime()
        result = runtime.chat(NormalizedMessage(content="synthetic"))
        self.assertTrue(result.suppressed)
        self.assertFalse(provider.requests)
        path = self.home / "traces" / "pre-inference" / f"{result.turn_id}.json"
        self.assertIsNone(json.loads(path.read_text())["configured_approved_identity"])

    def test_duplicate_turn_id_never_overwrites_existing_record(self):
        from vestigia.pre_inference_record import write_pre_inference_record
        kwargs = dict(turn_id="turn_synthetic", route="default", statement="first", approved=None)
        path = write_pre_inference_record(self.home, **kwargs)
        with self.assertRaises(FileExistsError):
            write_pre_inference_record(self.home, **{**kwargs, "statement": "replacement"})
        self.assertEqual(json.loads(path.read_text())["statement"], "first")

    def test_opt_in_validation_and_no_default_record(self):
        self.config["first_statement"]["pre_inference_record"] = "bad\nline"
        self.save_config()
        with self.assertRaises(ValueError):
            self.runtime()
        self.config["first_statement"] = {"enabled": False}
        self.config["model_approval"] = {"enabled": False, "routes": {}}
        self.save_config()
        runtime = CoreRuntime.from_home(self.home, fake=True)
        self.assertIsNone(runtime.pre_inference_record)
        result = runtime.chat(NormalizedMessage(content="ordinary synthetic turn"))
        self.assertFalse(result.suppressed)
        self.assertFalse((self.home / "traces" / "pre-inference").exists())


if __name__ == "__main__":
    unittest.main()
