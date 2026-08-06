from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime
from vestigia.workshop_sandbox import backend_descriptor, execute_source


class WorkshopSandboxCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Workshop Resident", glyph="W"
        )
        self.config = load_config(self.home)
        self.runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)

    def tearDown(self) -> None:
        self.temp.cleanup()


class WorkshopSandboxTests(WorkshopSandboxCase):
    def test_descriptor_is_explicit_about_unenforced_boundaries(self) -> None:
        descriptor = backend_descriptor(self.runtime.house)
        self.assertEqual("local.process", descriptor["backend_id"])
        self.assertEqual(["local_process"], descriptor["profiles"])
        self.assertTrue(descriptor["health"]["callable_now"])
        guarantees = descriptor["guarantees"]
        self.assertTrue(guarantees["environment_stripped"])
        self.assertTrue(guarantees["wall_limit_enforced"])
        self.assertTrue(guarantees["output_limit_enforced"])
        self.assertFalse(guarantees["network_deny_enforced"])
        self.assertFalse(guarantees["filesystem_mounts_enforced"])
        self.assertFalse(guarantees["hostile_code_approved"])

    def test_canonical_acceptance_run_produces_private_receipted_artifact(self) -> None:
        result = self.runtime.house.dispatch(
            {
                "action": "workshop.sandbox",
                "mode": "run_acceptance",
                "name": "Jeff",
                "wall_seconds": 3,
            },
            context={"interface": "cli"},
        )
        self.assertEqual("succeeded", result["status"])
        self.assertEqual(
            "I made this machine make a machine say hi to Jeff.",
            result["value"]["text"],
        )
        self.assertEqual("none", result["outward_effect"])
        self.assertFalse(result["follow_up_executed"])
        self.assertFalse(result["memory_adopted"])
        self.assertFalse(result["published"])
        self.assertEqual(1, len(result["artifacts"]))
        self.assertEqual("private", result["artifacts"][0]["privacy"])

        inspected = self.runtime.house.dispatch(
            {
                "action": "workshop.sandbox",
                "mode": "inspect",
                "execution_id": result["execution_id"],
            }
        )
        self.assertEqual("succeeded", inspected["execution"]["status"])
        self.assertEqual("none", inspected["execution"]["outward_effect"])
        self.assertFalse(inspected["source_included"])
        self.assertFalse(inspected["raw_arguments_included"])
        self.assertEqual(
            result["workshop_receipt_id"], inspected["receipts"][0]["receipt_id"]
        )

    def test_tool_action_does_not_accept_inline_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported fields: source"):
            self.runtime.house.dispatch(
                {
                    "action": "workshop.sandbox",
                    "mode": "run_acceptance",
                    "source": "print('not allowed')",
                }
            )

    def test_internal_runner_strips_environment_and_keeps_follow_up_inert(self) -> None:
        source = """import json\nimport os\nimport sys\npayload = json.load(sys.stdin)\njson.dump({\n  'schema_version': 'vestigia.script-output.v0.1',\n  'value': {'environment': dict(os.environ)},\n  'artifacts': [],\n  'warnings': [],\n  'requested_follow_up': [{'action': 'discord.send', 'text': 'nope'}]\n}, sys.stdout)\n"""
        result = execute_source(
            self.runtime.house,
            source=source,
            script_id="test.environment",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("succeeded", result["status"])
        environment = result["value"]["environment"]
        keys = set(environment)
        required = {
            "PYTHONHASHSEED",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONIOENCODING",
            "PYTHONUTF8",
            "TEMP",
            "TMP",
        }
        platform_inserted = {"LC_CTYPE", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}
        self.assertTrue(required <= keys)
        self.assertTrue(keys <= required | platform_inserted)
        for forbidden in (
            "OPENAI_API_KEY",
            "PATH",
            "HOME",
            "USERPROFILE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
        ):
            self.assertNotIn(forbidden, keys)
        temp_path = Path(environment["TEMP"])
        self.assertEqual("tmp", temp_path.name)
        self.assertTrue(temp_path.parent.name.startswith("sandbox-"))
        self.assertEqual(1, len(result["requested_follow_up"]))
        self.assertFalse(result["follow_up_executed"])

    def test_timeout_fails_closed_with_durable_receipt(self) -> None:
        result = execute_source(
            self.runtime.house,
            source="while True:\n    pass\n",
            script_id="test.timeout",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 1},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("timeout", result["error"]["category"])
        self.assertEqual("none", result["outward_effect"])
        inspected = self.runtime.house.dispatch(
            {
                "action": "workshop.sandbox",
                "mode": "inspect",
                "execution_id": result["execution_id"],
            }
        )
        self.assertEqual("timeout", inspected["receipts"][0]["error"]["category"])

    def test_output_overflow_and_malformed_result_fail_closed(self) -> None:
        overflow = execute_source(
            self.runtime.house,
            source="import sys\nsys.stdout.write('x' * 200000)\n",
            script_id="test.output-limit",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("failed", overflow["status"])
        self.assertEqual("resource_limit", overflow["error"]["category"])

        malformed = execute_source(
            self.runtime.house,
            source="print('not json')\n",
            script_id="test.malformed",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("failed", malformed["status"])
        self.assertEqual("malformed_result", malformed["error"]["category"])

    def test_declared_private_file_is_harvested_with_media_type(self) -> None:
        source = """import json\nimport pathlib\nimport sys\npayload = json.load(sys.stdin)\npathlib.Path('output/note.txt').write_text('hello from inside', encoding='utf-8')\njson.dump({\n  'schema_version': 'vestigia.script-output.v0.1',\n  'value': {'made_file': True},\n  'artifacts': [{'path': 'note.txt', 'media_type': 'text/plain'}],\n  'warnings': []\n}, sys.stdout)\n"""
        result = execute_source(
            self.runtime.house,
            source=source,
            script_id="test.declared-file",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("succeeded", result["status"])
        self.assertEqual(2, len(result["artifacts"]))
        file_ref = next(item for item in result["artifacts"] if item["kind"] == "script_file")
        self.assertEqual("text/plain", file_ref["media_type"])
        self.assertEqual("private", file_ref["privacy"])
        self.assertNotIn("storage_path", file_ref)

    def test_undeclared_or_missing_output_file_is_rejected(self) -> None:
        undeclared = """import json\nimport pathlib\nimport sys\npathlib.Path('output/surprise.txt').write_text('nope', encoding='utf-8')\njson.dump({'schema_version': 'vestigia.script-output.v0.1', 'value': {}, 'artifacts': [], 'warnings': []}, sys.stdout)\n"""
        rejected = execute_source(
            self.runtime.house,
            source=undeclared,
            script_id="test.undeclared-file",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("failed", rejected["status"])
        self.assertEqual("artifact_rejected", rejected["error"]["category"])
        self.assertEqual([], rejected["artifacts"])

        missing = """import json\nimport sys\njson.dump({'schema_version': 'vestigia.script-output.v0.1', 'value': {}, 'artifacts': [{'path': 'missing.txt', 'media_type': 'text/plain'}], 'warnings': []}, sys.stdout)\n"""
        absent = execute_source(
            self.runtime.house,
            source=missing,
            script_id="test.missing-file",
            script_version=1,
            arguments={},
            context={"interface": "test"},
            payload={"wall_seconds": 2},
        )
        self.assertEqual("failed", absent["status"])
        self.assertEqual("artifact_rejected", absent["error"]["category"])

    def test_observatory_and_list_are_metadata_only(self) -> None:
        run = self.runtime.house.dispatch(
            {"action": "workshop.sandbox", "mode": "run_acceptance", "name": "Liora"}
        )
        listed = self.runtime.house.dispatch(
            {"action": "workshop.sandbox", "mode": "list", "limit": 10}
        )
        self.assertEqual(run["execution_id"], listed["executions"][0]["id"])
        self.assertNotIn("source", listed["executions"][0])
        self.assertNotIn("arguments", listed["executions"][0])

        observatory = self.runtime.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )
        panel = observatory["observatory"]["workshop_sandbox"]
        self.assertEqual("local.process", panel["backend"]["backend_id"])
        self.assertEqual(run["execution_id"], panel["latest_execution"]["id"])
        self.assertEqual("none", panel["outward_boundary"])


if __name__ == "__main__":
    unittest.main()
