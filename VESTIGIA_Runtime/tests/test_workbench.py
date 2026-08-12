from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort


class WorkbenchContinueReadingTests(unittest.TestCase):
    def _port(self, home: Path) -> HousePort:
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        return HousePort(config, db)

    def _home_with_saved_reading(self, root: Path) -> tuple[Path, str]:
        home = initialize_home(root / "home", name="Test Resident", glyph="📖")
        document = home / "workspace" / "long-book.md"
        document.parent.mkdir(parents=True, exist_ok=True)
        # The first physical line is deliberately longer than the default house chunk,
        # making the second marker land in a later indexed chunk without relying on
        # test-only configuration changes.
        document.write_text(
            ("first-page " * 800) + "\nSECOND-PAGE-MARKER\nend\n",
            encoding="utf-8",
        )
        port = self._port(home)
        port.refresh_index()
        obj = port.legible.object_by_reference("workspace/long-book.md")
        self.assertIsNotNone(obj)
        bookmark_id = port.legible.add_bookmark(
            str(obj["id"]),
            label="Long Book",
            note="A deliberately saved reading position.",
            location={"chunk": 1},
        )
        return home, bookmark_id

    def test_continue_card_survives_restart_and_uses_normal_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home, bookmark_id = self._home_with_saved_reading(Path(temp))

            # A fresh HousePort models a Runtime restart: the Workbench must rediscover
            # the card from durable bookmark/object state rather than an in-memory cache.
            port = self._port(home)
            view = port.dispatch(
                {"action": "workbench.view", "lane": "continue", "limit": 10},
                turn_id="turn-view",
                context={"interface": "cli"},
            )
            self.assertEqual(1, view["card_count"])
            card = view["cards"][0]
            self.assertEqual("continue", card["lane"])
            self.assertEqual("reading", card["kind"])
            self.assertEqual("read_only", card["effect_class"])
            self.assertEqual(bookmark_id, card["source"]["bookmark_id"])
            self.assertEqual("workspace/long-book.md", card["source"]["locator"])
            self.assertTrue(card["state_fingerprint"])
            self.assertEqual(
                {"continue", "start_over", "provenance"},
                {item["action_id"] for item in card["actions"]},
            )

            acted = port.dispatch(
                {
                    "action": "workbench.act",
                    "card_id": card["card_id"],
                    "action_id": "continue",
                    "max_tokens": 1200,
                },
                turn_id="turn-act",
                context={"interface": "cli"},
            )
            self.assertEqual("bookmark.open", acted["underlying_action"])
            self.assertTrue(acted["underlying_receipt_id"])
            self.assertIn("SECOND-PAGE-MARKER", acted["result"]["text"])
            self.assertEqual("none", acted["outward_effect"])
            self.assertFalse(acted["memory_promotion"])
            self.assertFalse(acted["identity_effect"])

            receipts = port.legible.list_receipts(limit=20)
            actions = {item["action"] for item in receipts}
            self.assertIn("bookmark.open", actions)
            self.assertIn("workbench.act", actions)

    def test_changed_document_invalidates_old_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home, _ = self._home_with_saved_reading(Path(temp))
            port = self._port(home)
            view = port.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-before-change",
                context={"interface": "cli"},
            )
            old_card = view["cards"][0]

            document = home / "workspace" / "long-book.md"
            document.write_text(
                document.read_text(encoding="utf-8") + "\nCHANGED-AFTER-CARD\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(KeyError, "stale or unavailable"):
                port.dispatch(
                    {
                        "action": "workbench.act",
                        "card_id": old_card["card_id"],
                        "action_id": "continue",
                    },
                    turn_id="turn-after-change",
                    context={"interface": "cli"},
                )

            refreshed = port.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-refresh",
                context={"interface": "cli"},
            )
            self.assertEqual(1, refreshed["card_count"])
            self.assertNotEqual(old_card["card_id"], refreshed["cards"][0]["card_id"])

    def test_non_continue_lanes_are_honest_empty_placeholders_for_now(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="🏮")
            port = self._port(home)
            result = port.dispatch(
                {"action": "workbench.view", "lane": "review"},
                turn_id="turn-review",
                context={"interface": "cli"},
            )
            self.assertEqual([], result["cards"])
            self.assertEqual(["continue"], result["implemented_lanes"])
            self.assertIn("review", result["planned_lanes"])


if __name__ == "__main__":
    unittest.main()
