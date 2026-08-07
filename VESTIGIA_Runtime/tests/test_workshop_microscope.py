from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


SOURCE = """import json
import sys
json.dump({'schema_version':'vestigia.script-output.v0.1','value':{},'artifacts':[],'warnings':[]}, sys.stdout)
"""


class MicroscopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = initialize_home(
            Path(self.temp.name) / "home", name="Microscope Resident", glyph="M"
        )
        self.runtime = CoreRuntime(
            load_config(self.home), provider=FakeProvider(), fake=True
        )
        self.house = self.runtime.house

    def tearDown(self) -> None:
        self.temp.cleanup()

    def shelf(self, mode: str, **payload):
        return self.house.dispatch(
            {"action": "script.shelf", "mode": mode, **payload},
            context={"interface": "test"},
        )

    def scope(self, mode: str, **payload):
        return self.house.dispatch(
            {"action": "workshop.microscope", "mode": mode, **payload},
            context={"interface": "test"},
        )

    def draft(self, source: str = SOURCE):
        return self.shelf(
            "draft",
            script_id="resident.greeter",
            name="Greeter",
            source=source,
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "additionalProperties": True},
        )

    def test_microscope_is_registered_read_only_and_non_executing(self) -> None:
        spec = self.house.registry.spec("workshop.microscope")
        self.assertEqual(("database:read", "filesystem:private_script_read"), spec.effects)
        result = self.scope("help")
        self.assertTrue(result["read_only"])
        self.assertIn("does not test", result["boundary"])

    def test_explain_reports_inert_effective_authority(self) -> None:
        self.draft()
        self.shelf("inspect", script_id="resident.greeter", version=1)
        result = self.scope(
            "explain", script_id="resident.greeter", version=1
        )
        self.assertTrue(result["read_only"])
        self.assertFalse(result["source_executed"])
        self.assertFalse(result["manifest"]["effective"]["callable"])
        self.assertEqual(
            "hardened_execution_unavailable",
            result["manifest"]["effective"]["reason_code"],
        )
        self.assertEqual([], result["manifest"]["declared"]["allowed_backends"])

    def test_contract_diagnostics_do_not_authorize_execution(self) -> None:
        self.draft()
        result = self.scope(
            "contracts", script_id="resident.greeter", version=1
        )
        self.assertTrue(result["contracts"]["input"]["valid"])
        self.assertTrue(result["contracts"]["output"]["valid"])
        self.assertFalse(result["contracts"]["contracts_authorize_execution"])

    def test_compare_is_private_and_does_not_mutate_versions(self) -> None:
        self.draft(SOURCE + "\n# first\n")
        self.draft(SOURCE + "\n# second\n")
        result = self.scope(
            "compare",
            script_id="resident.greeter",
            left_version=1,
            right_version=2,
        )
        self.assertTrue(result["private"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["source_executed"])
        self.assertGreater(result["diff_line_count"], 0)
        versions = self.shelf("list")["scripts"]
        self.assertEqual({1, 2}, {int(item["version"]) for item in versions})

    def test_quarantine_explanation_is_read_only(self) -> None:
        self.draft()
        self.shelf(
            "quarantine",
            script_id="resident.greeter",
            version=1,
            reason="fixture",
        )
        result = self.scope(
            "quarantine", script_id="resident.greeter", version=1
        )
        self.assertTrue(result["read_only"])
        self.assertTrue(result["quarantine"]["sticky"])
        card = self.shelf("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual("quarantined", card["state"])

    def test_observatory_panel_reports_no_execution(self) -> None:
        self.draft()
        panel = self.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )["observatory"]["workshop_microscope"]
        self.assertEqual(1, panel["script_count"])
        self.assertFalse(panel["execution_available"])
        self.assertTrue(panel["read_only"])

    def test_module_contains_no_private_method_replacement(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "vestigia"
            / "workshop_microscope.py"
        )
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("install_shelf_truth_integration", text)
        self.assertNotIn("shelf._", text)
        self.assertNotIn("def install_core", text)


if __name__ == "__main__":
    unittest.main()
