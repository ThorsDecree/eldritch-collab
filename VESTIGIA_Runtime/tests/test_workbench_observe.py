from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.composition import composition_plan
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort


class WorkbenchObserveTests(unittest.TestCase):
    def _port(self, home: Path) -> HousePort:
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        return HousePort(config, db)

    def test_observe_status_card_uses_existing_status_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Observer", glyph="👁")
            port = self._port(home)

            view = port.dispatch(
                {"action": "workbench.view", "lane": "observe"},
                turn_id="turn-observe-view",
                context={"interface": "cli"},
            )
            self.assertEqual(1, view["card_count"])
            self.assertIn("observe", view["implemented_lanes"])
            self.assertNotIn("observe", view["planned_lanes"])
            self.assertEqual(
                ["reading.continue", "observe.runtime"],
                view["providers"],
            )

            card = view["cards"][0]
            self.assertEqual("observe.runtime", card["provider"])
            self.assertEqual("observe.runtime_status", card["projection_kind"])
            self.assertEqual("observe", card["lane"])
            self.assertEqual("runtime_status", card["kind"])
            self.assertEqual("read_only", card["effect_class"])
            self.assertEqual("ORIENTATION", card["snapshot"]["runtime_state"])
            self.assertGreater(card["snapshot"]["capability_count"], 0)

            action = card["actions"][0]
            self.assertEqual("inspect", action["action_id"])
            self.assertEqual("read_only", action["effect_class"])
            self.assertEqual(["database:read"], action["effects"])
            self.assertEqual("free", action["cost_class"])
            self.assertEqual("none", action["confirmation"])
            self.assertFalse(action["outward_facing"])

            acted = port.dispatch(
                {
                    "action": "workbench.act",
                    "card_id": card["card_id"],
                    "action_id": "inspect",
                },
                turn_id="turn-observe-act",
                context={"interface": "cli"},
            )
            self.assertEqual("observe.runtime", acted["provider"])
            self.assertEqual("status", acted["underlying_action"])
            self.assertTrue(acted["underlying_receipt_id"])
            self.assertEqual("read_only", acted["action_contract"]["effect_class"])
            self.assertEqual(["database:read"], acted["action_contract"]["effects"])
            self.assertEqual("none", acted["outward_effect"])
            self.assertIsNotNone(acted["refreshed_card"])

            receipts = port.legible.list_receipts(limit=20)
            actions = {item["action"] for item in receipts}
            self.assertIn("status", actions)
            self.assertIn("workbench.act", actions)

    def test_provider_registry_contains_independent_observe_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Observer", glyph="👁")
            self._port(home)
            plan = composition_plan()
            self.assertEqual(
                ["reading.continue", "observe.runtime"],
                plan["workbench_providers"],
            )


if __name__ == "__main__":
    unittest.main()
