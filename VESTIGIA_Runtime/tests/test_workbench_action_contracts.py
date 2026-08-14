from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.workbench import _validate_card_contract, workbench_act


class WorkbenchActionContractTests(unittest.TestCase):
    def _port(self, home: Path) -> HousePort:
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        return HousePort(config, db)

    def test_reading_actions_expose_selected_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Desk Resident", glyph="🪑")
            document = home / "workspace" / "book.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text("page " * 5000, encoding="utf-8")
            port = self._port(home)
            opened = port.dispatch(
                {
                    "action": "read",
                    "path": "workspace/book.md",
                    "chunk": 0,
                    "max_tokens": 300,
                },
                turn_id="turn-open",
                context={"interface": "cli"},
            )
            self.assertTrue(opened["cursor"])

            view = port.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-view",
                context={"interface": "cli"},
            )
            card = view["cards"][0]
            action = next(item for item in card["actions"] if item["action_id"] == "continue")
            self.assertEqual("read_only", action["effect_class"])
            self.assertEqual(
                ["database:read", "filesystem:read_indexed_house"],
                action["effects"],
            )
            self.assertEqual("free", action["cost_class"])
            self.assertEqual("none", action["confirmation"])
            self.assertFalse(action["outward_facing"])

            acted = port.dispatch(
                {
                    "action": "workbench.act",
                    "card_id": card["card_id"],
                    "action_id": "continue",
                    "max_tokens": 300,
                },
                turn_id="turn-act",
                context={"interface": "cli"},
            )
            self.assertEqual("vestigia.workbench.v0.2", acted["schema_version"])
            self.assertEqual("read_only", acted["effect_class"])
            self.assertEqual(
                {
                    "effect_class": "read_only",
                    "effects": ["database:read", "filesystem:read_indexed_house"],
                    "cost_class": "free",
                    "confirmation": "none",
                    "outward_facing": False,
                },
                acted["action_contract"],
            )

            spec = port.registry.spec("workbench.act")
            self.assertEqual("free", spec.cost_class)
            self.assertEqual("none", spec.confirmation)
            self.assertFalse(spec.outward_facing)

    def test_stronger_action_cannot_hide_behind_read_only_broker(self) -> None:
        card = {
            "card_id": "wb_stronger_contract",
            "state_fingerprint": "fingerprint",
            "provider": "future.tend",
            "lane": "tend",
            "effect_class": "read_only",
            "actions": [
                {
                    "action_id": "change_setting",
                    "label": "Change setting",
                    "effect_class": "house_change",
                    "effects": ["database:write"],
                    "cost_class": "free",
                    "confirmation": "none",
                    "outward_facing": False,
                }
            ],
        }
        _validate_card_contract(card)
        with patch("vestigia.workbench._current_card", return_value=card), patch(
            "vestigia.workbench.dispatch_workbench_action"
        ) as dispatch:
            with self.assertRaisesRegex(PermissionError, "read-only semantic broker"):
                workbench_act(
                    object(),
                    {
                        "card_id": card["card_id"],
                        "action_id": "change_setting",
                    },
                    {"interface": "cli"},
                )
            dispatch.assert_not_called()

    def test_outward_action_must_declare_confirmation(self) -> None:
        card = {
            "card_id": "wb_outward_contract",
            "provider": "future.share",
            "actions": [
                {
                    "action_id": "share",
                    "effect_class": "outward",
                    "effects": ["discord:send"],
                    "cost_class": "free",
                    "confirmation": "none",
                    "outward_facing": True,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "cannot be outward without confirmation"):
            _validate_card_contract(card)


if __name__ == "__main__":
    unittest.main()
