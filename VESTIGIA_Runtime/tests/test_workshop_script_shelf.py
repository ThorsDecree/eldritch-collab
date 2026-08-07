from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


SAFE_SOURCE = """import json
import sys
payload = json.load(sys.stdin)
name = str(payload['arguments'].get('name', 'friend'))[:80]
json.dump({
  'schema_version': 'vestigia.script-output.v0.1',
  'value': {'text': f'hello {name}'},
  'artifacts': [],
  'warnings': []
}, sys.stdout)
"""
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "maxLength": 80}},
    "required": ["name"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "maxLength": 100}},
    "required": ["text"],
    "additionalProperties": False,
}


class ScriptShelfCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(self.root / "home", name="Shelf Resident", glyph="S")
        self.config = load_config(self.home)
        self.runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, mode: str, **payload):
        return self.runtime.house.dispatch(
            {"action": "script.shelf", "mode": mode, **payload},
            context={"interface": "test"},
        )

    def draft(self, *, script_id: str = "resident.greeter", source: str = SAFE_SOURCE, **extra):
        return self.call(
            "draft",
            script_id=script_id,
            name="Greeter",
            source=source,
            input_schema=extra.pop("input_schema", INPUT_SCHEMA),
            output_schema=extra.pop("output_schema", OUTPUT_SCHEMA),
            determinism="deterministic",
            **extra,
        )

    def activate(self, *, script_id: str = "resident.greeter", version: int = 1, arguments=None):
        arguments = arguments or {"name": "test"}
        self.call("inspect", script_id=script_id, version=version)
        tested = self.call("test", script_id=script_id, version=version, arguments=arguments)
        self.assertEqual("succeeded", tested["status"])
        self.call("approve", script_id=script_id, version=version)
        return self.call("activate", script_id=script_id, version=version)


class ScriptShelfTests(ScriptShelfCase):
    def test_draft_and_inspection_are_inert(self) -> None:
        marker = self.root / "should-not-exist.txt"
        source = f"open(r'{marker.as_posix()}', 'w').write('oops')\n"
        drafted = self.draft(source=source, input_schema={"type": "object"}, output_schema={"type": "object"})
        self.assertEqual("draft", drafted["state"])
        self.assertFalse(drafted["source_executed"])
        self.assertFalse(marker.exists())
        inspected = self.call("inspect", script_id="resident.greeter", version=1)
        self.assertTrue(inspected["inspection"]["parse_ok"])
        self.assertFalse(inspected["source_executed"])
        self.assertFalse(marker.exists())

    def test_resident_script_full_lifecycle_is_hash_bound_and_private(self) -> None:
        drafted = self.draft()
        source_hash = drafted["source"]["sha256"]
        with self.assertRaises(PermissionError):
            self.call("run", script_id="resident.greeter", arguments={"name": "Liora"})

        inspected = self.call("inspect", script_id="resident.greeter", version=1)
        self.assertEqual("local_process_eligible", inspected["inspection"]["classification"])
        tested = self.call("test", script_id="resident.greeter", version=1, arguments={"name": "Liora"})
        self.assertEqual("succeeded", tested["status"])
        approved = self.call("approve", script_id="resident.greeter", version=1)
        self.assertEqual(["sandbox.local_compute"], approved["granted_capabilities"])
        self.assertEqual(0, approved["provider_calls"])
        self.assertEqual(0, approved["outward_actions"])
        activated = self.call("activate", script_id="resident.greeter", version=1)
        self.assertTrue(activated["callable"])

        result = self.call("run", script_id="resident.greeter", arguments={"name": "Liora"})
        self.assertEqual("succeeded", result["status"])
        self.assertEqual({"text": "hello Liora"}, result["value"])
        self.assertEqual(source_hash, result["source_hash"])
        self.assertEqual("script_generated", result["output_authorship"])
        self.assertEqual("private", result["output_privacy"])
        self.assertEqual("none", result["outward_effect"])
        self.assertFalse(result["follow_up_executed"])
        self.assertFalse(result["authority_changed"])
        self.assertFalse(result["memory_adopted"])
        self.assertFalse(result["published"])

        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual("active", card["state"])
        self.assertEqual(source_hash, card["source"]["sha256"])
        self.assertFalse(card["source_included"])
        self.assertTrue(card["callable"])
        exact = self.call("read_source", script_id="resident.greeter", version=1)
        self.assertEqual(SAFE_SOURCE, exact["source"])
        listed = self.call("list")["scripts"]
        self.assertNotIn("source", listed[0])

        observatory = self.runtime.house.dispatch({"action": "house.observatory", "section": "all"})
        panel = observatory["observatory"]["script_shelf"]
        self.assertEqual(1, panel["state_counts"]["active"])
        self.assertFalse(panel["source_included"])

    def test_received_source_is_hardened_only_and_cannot_test_locally(self) -> None:
        received = self.call(
            "receive",
            script_id="imported.greeter",
            version=1,
            name="Imported greeter",
            source=SAFE_SOURCE,
            authored_lane="model",
            authored_actor_id="some-model",
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )
        self.assertEqual("received", received["state"])
        inspected = self.call("inspect", script_id="imported.greeter", version=1)
        self.assertEqual("hardened_only", inspected["inspection"]["classification"])
        self.assertEqual("hardened_backend", inspected["next_required"])
        with self.assertRaises(PermissionError):
            self.call("test", script_id="imported.greeter", version=1, arguments={"name": "x"})

    def test_risky_resident_source_requires_hardened_backend(self) -> None:
        source = "import socket\n" + SAFE_SOURCE
        self.draft(script_id="resident.socket-test", source=source)
        inspected = self.call("inspect", script_id="resident.socket-test", version=1)
        self.assertEqual("hardened_only", inspected["inspection"]["classification"])
        self.assertIn("sensitive_import_requires_hardened", inspected["inspection"]["violations"])
        with self.assertRaises(PermissionError):
            self.call("test", script_id="resident.socket-test", version=1, arguments={"name": "x"})

    def test_failed_output_contract_blocks_approval(self) -> None:
        source = """import json, sys
json.load(sys.stdin)
json.dump({'schema_version':'vestigia.script-output.v0.1','value':42,'artifacts':[],'warnings':[]}, sys.stdout)
"""
        self.draft(source=source)
        self.call("inspect", script_id="resident.greeter", version=1)
        tested = self.call("test", script_id="resident.greeter", version=1, arguments={"name": "x"})
        self.assertEqual("failed", tested["status"])
        self.assertFalse(tested["report"]["value_contract_ok"])
        with self.assertRaises(PermissionError):
            self.call("approve", script_id="resident.greeter", version=1)

    def test_new_version_never_inherits_activation_and_supersession_is_explicit(self) -> None:
        self.draft()
        self.activate()
        second_source = SAFE_SOURCE.replace("hello {name}", "hiya {name}")
        second = self.draft(source=second_source)
        self.assertEqual(2, second["version"])
        self.assertEqual("draft", second["state"])
        old = self.call("run", script_id="resident.greeter", arguments={"name": "A"})
        self.assertEqual({"text": "hello A"}, old["value"])
        with self.assertRaises(PermissionError):
            self.call("run", script_id="resident.greeter", version=2, arguments={"name": "A"})

        self.activate(version=2)
        newest = self.call("run", script_id="resident.greeter", arguments={"name": "B"})
        self.assertEqual(2, newest["version"])
        self.assertEqual({"text": "hiya B"}, newest["value"])
        superseded = self.call("supersede", script_id="resident.greeter", version=1, replacement_version=2)
        self.assertEqual("superseded", superseded["state"])
        with self.assertRaises(PermissionError):
            self.call("run", script_id="resident.greeter", version=1, arguments={"name": "C"})

    def test_disable_immediately_removes_callability_but_keeps_evidence(self) -> None:
        self.draft()
        self.activate()
        disabled = self.call("disable", script_id="resident.greeter", version=1, reason="nap")
        self.assertEqual("disabled", disabled["state"])
        with self.assertRaises(PermissionError):
            self.call("run", script_id="resident.greeter", version=1, arguments={"name": "x"})
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertIsNotNone(card["evidence"]["inspection"])
        self.assertIsNotNone(card["evidence"]["test"])
        self.assertIsNotNone(card["evidence"]["approval"])
        self.assertIsNotNone(card["evidence"]["activation"])

    def test_version_digest_conflict_quarantines_instead_of_overwrite(self) -> None:
        first = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE,
            authored_lane="participant",
            authored_actor_id="friend",
        )
        conflict = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE + "\n# different bytes\n",
            authored_lane="participant",
            authored_actor_id="friend",
        )
        self.assertEqual("quarantined_conflict", conflict["status"])
        card = self.call("show", script_id="shared.tool", version=1)["script"]
        self.assertEqual("quarantined", card["state"])
        self.assertEqual(first["source"]["sha256"], card["source"]["sha256"])
        with self.assertRaisesRegex(ValueError, "not reopened"):
            self.call("inspect", script_id="shared.tool", version=1)

    def test_source_tamper_fails_closed_and_quarantines(self) -> None:
        self.draft()
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT source_path FROM workshop_scripts WHERE resident_id=? AND script_id='resident.greeter' AND version=1",
                (self.runtime.house.resident_id,),
            ).fetchone()
        path = self.home / row["source_path"]
        path.write_text("# tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "hash no longer matches"):
            self.call("inspect", script_id="resident.greeter", version=1)
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual("quarantined", card["state"])

    def test_live_contract_violation_quarantines_active_version(self) -> None:
        source = """import json, sys
p=json.load(sys.stdin)
ok=bool(p['arguments']['ok'])
value='good' if ok else {'unexpected': True}
json.dump({'schema_version':'vestigia.script-output.v0.1','value':value,'artifacts':[],'warnings':[]}, sys.stdout)
"""
        self.draft(
            script_id="resident.flip",
            source=source,
            input_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            output_schema={"type": "string"},
        )
        self.activate(script_id="resident.flip", arguments={"ok": True})
        result = self.call("run", script_id="resident.flip", arguments={"ok": False})
        self.assertEqual("failed", result["status"])
        self.assertIsNotNone(result["contract_error"])
        card = self.call("show", script_id="resident.flip", version=1)["script"]
        self.assertEqual("quarantined", card["state"])
        self.assertFalse(card["callable"])

    def test_undeclared_artifact_during_test_quarantines(self) -> None:
        source = """import json, pathlib, sys
json.load(sys.stdin)
pathlib.Path('output/surprise.txt').write_text('surprise', encoding='utf-8')
json.dump({'schema_version':'vestigia.script-output.v0.1','value':{},'artifacts':[],'warnings':[]}, sys.stdout)
"""
        self.draft(
            script_id="resident.surprise",
            source=source,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        self.call("inspect", script_id="resident.surprise", version=1)
        tested = self.call("test", script_id="resident.surprise", version=1, arguments={})
        self.assertEqual("failed", tested["status"])
        card = self.call("show", script_id="resident.surprise", version=1)["script"]
        self.assertEqual("quarantined", card["state"])


if __name__ == "__main__":
    unittest.main()
