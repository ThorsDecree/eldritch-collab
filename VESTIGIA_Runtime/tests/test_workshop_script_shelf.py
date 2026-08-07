from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime
from vestigia.workshop_script_store import record_event


SAFE_SOURCE = """import json
import sys
payload = json.load(sys.stdin)
json.dump({'schema_version':'vestigia.script-output.v0.1','value':payload,'artifacts':[],'warnings':[]}, sys.stdout)
"""
INPUT_SCHEMA = {"type": "object", "additionalProperties": True}
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": True}


class ScriptShelfCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Shelf Resident", glyph="S"
        )
        self.config = load_config(self.home)
        self.runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, mode: str, **payload):
        return self.runtime.house.dispatch(
            {"action": "script.shelf", "mode": mode, **payload},
            context={"interface": "test"},
        )

    def draft(
        self,
        *,
        script_id: str = "resident.greeter",
        source: str = SAFE_SOURCE,
    ):
        return self.call(
            "draft",
            script_id=script_id,
            name="Greeter",
            source=source,
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )


class ScriptShelfTests(ScriptShelfCase):
    def test_capability_exposes_no_execution_lifecycle_modes(self) -> None:
        spec = self.runtime.house.registry.spec("script.shelf")
        modes = set(spec.input_schema["properties"]["mode"]["enum"])
        self.assertEqual(
            {
                "draft",
                "receive",
                "list",
                "show",
                "read_source",
                "inspect",
                "quarantine",
                "archive",
            },
            modes,
        )
        self.assertTrue(
            modes.isdisjoint({"test", "approve", "activate", "run", "disable"})
        )
        for mode in ("test", "approve", "activate", "run"):
            with self.assertRaises(ValueError):
                self.call(mode, script_id="resident.greeter", version=1)

    def test_draft_and_inspection_never_execute_source(self) -> None:
        marker = self.root / "must-not-exist.txt"
        source = f"open(r'{marker.as_posix()}', 'w').write('executed')\n"
        drafted = self.draft(source=source)
        self.assertEqual("draft", drafted["state"])
        self.assertFalse(drafted["source_executed"])
        inspected = self.call("inspect", script_id="resident.greeter", version=1)
        self.assertTrue(inspected["inspection"]["parse_ok"])
        self.assertEqual("await_hardened_execution_path", inspected["next_required"])
        self.assertFalse(inspected["execution_available"])
        self.assertFalse(marker.exists())

    def test_script_cards_and_observatory_are_explicitly_non_callable(self) -> None:
        drafted = self.draft()
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual(drafted["source"]["sha256"], card["source"]["sha256"])
        self.assertFalse(card["source_included"])
        self.assertFalse(card["callable"])
        self.assertFalse(card["sandbox"]["available"])
        self.assertEqual([], card["sandbox"]["allowed_backends"])
        exact = self.call("read_source", script_id="resident.greeter", version=1)
        self.assertEqual(SAFE_SOURCE, exact["source"])
        panel = self.runtime.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )["observatory"]["script_shelf"]
        self.assertFalse(panel["execution_available"])
        self.assertFalse(panel["provider_reachable_execution"])

    def test_received_and_risky_source_remain_hardened_only(self) -> None:
        self.call(
            "receive",
            script_id="imported.greeter",
            version=1,
            name="Imported",
            source=SAFE_SOURCE,
            authored_lane="model",
            authored_actor_id="model",
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )
        imported = self.call("inspect", script_id="imported.greeter", version=1)
        self.assertEqual("hardened_only", imported["inspection"]["classification"])
        self.assertEqual("await_hardened_execution_path", imported["next_required"])

        self.draft(
            script_id="resident.socket-test",
            source="import socket\n" + SAFE_SOURCE,
        )
        risky = self.call("inspect", script_id="resident.socket-test", version=1)
        self.assertEqual("hardened_only", risky["inspection"]["classification"])
        self.assertIn(
            "sensitive_import_requires_hardened",
            risky["inspection"]["violations"],
        )

    def test_version_allocation_is_immediate_and_unique(self) -> None:
        from vestigia import workshop_script_shelf as shelf_module

        implementation = inspect.getsource(shelf_module._draft)
        self.assertIn('connection.execute("BEGIN IMMEDIATE")', implementation)
        versions = [
            int(
                self.draft(
                    script_id="resident.concurrent",
                    source=SAFE_SOURCE + f"\n# candidate {index}\n",
                )["version"]
            )
            for index in range(4)
        ]
        self.assertEqual([1, 2, 3, 4], versions)

    def test_interrupted_inspection_rolls_back_evidence_and_state(self) -> None:
        self.draft()
        with patch(
            "vestigia.workshop_script_store.record_event",
            side_effect=RuntimeError("simulated interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                self.call("inspect", script_id="resident.greeter", version=1)
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT state FROM workshop_scripts WHERE resident_id=? "
                "AND script_id='resident.greeter' AND version=1",
                (self.runtime.house.resident_id,),
            ).fetchone()
            count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM workshop_script_inspections "
                    "WHERE resident_id=? AND script_id='resident.greeter' AND version=1",
                    (self.runtime.house.resident_id,),
                ).fetchone()["count"]
            )
        self.assertEqual("draft", row["state"])
        self.assertEqual(0, count)

    def test_record_event_can_own_its_connection(self) -> None:
        self.draft()
        event_id = record_event(
            self.runtime.house,
            script_id="resident.greeter",
            version=1,
            event_type="fixture_event",
            from_state="draft",
            to_state="draft",
        )
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM workshop_script_events WHERE id=?", (event_id,)
            ).fetchone()
        self.assertIsNotNone(row)

    def test_digest_conflict_quarantines_without_overwriting(self) -> None:
        first = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE,
            authored_lane="participant",
        )
        conflict = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE + "\n# different\n",
            authored_lane="participant",
        )
        self.assertEqual("quarantined_conflict", conflict["status"])
        card = self.call("show", script_id="shared.tool", version=1)["script"]
        self.assertEqual("quarantined", card["state"])
        self.assertEqual(first["source"]["sha256"], card["source"]["sha256"])

    def test_source_tamper_fails_closed(self) -> None:
        self.draft()
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT source_path FROM workshop_scripts WHERE resident_id=? "
                "AND script_id='resident.greeter' AND version=1",
                (self.runtime.house.resident_id,),
            ).fetchone()
        (self.home / row["source_path"]).write_text("# tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "hash no longer matches"):
            self.call("inspect", script_id="resident.greeter", version=1)
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual("quarantined", card["state"])


if __name__ == "__main__":
    unittest.main()
