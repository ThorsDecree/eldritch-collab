from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.composition import composition_plan
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort


class WorkbenchContinueReadingTests(unittest.TestCase):
    def _port(self, home: Path) -> HousePort:
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        return HousePort(config, db)

    def _write_multi_chunk_book(self, home: Path, name: str = "cursor-book.md") -> Path:
        document = home / "workspace" / name
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(
            "\n".join((f"PAGE-{index} " + (f"word-{index} " * 1000)) for index in range(6)),
            encoding="utf-8",
        )
        return document

    def _home_with_saved_reading(self, root: Path) -> tuple[Path, str]:
        home = initialize_home(root / "home", name="Test Resident", glyph="📖")
        document = home / "workspace" / "long-book.md"
        document.parent.mkdir(parents=True, exist_ok=True)
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
            port = self._port(home)
            view = port.dispatch(
                {"action": "workbench.view", "lane": "continue", "limit": 10},
                turn_id="turn-view",
                context={"interface": "cli"},
            )
            self.assertEqual(1, view["card_count"])
            self.assertEqual(["reading.continue"], view["providers"])
            card = view["cards"][0]
            self.assertEqual("continue", card["lane"])
            self.assertEqual("reading", card["kind"])
            self.assertEqual("reading.continue", card["provider"])
            self.assertEqual("reading.bookmark", card["projection_kind"])
            self.assertEqual("read_only", card["effect_class"])
            self.assertEqual(bookmark_id, card["source"]["bookmark_id"])
            self.assertEqual("workspace/long-book.md", card["source"]["locator"])
            self.assertEqual("bookmark", card["position"]["resume_mode"])
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
            self.assertEqual("reading.continue", acted["provider"])
            self.assertEqual("bookmark.open", acted["underlying_action"])
            self.assertTrue(acted["underlying_receipt_id"])
            self.assertIn("SECOND-PAGE-MARKER", acted["outcome"]["text"])
            self.assertEqual("none", acted["outward_effect"])
            self.assertFalse(acted["memory_promotion"])
            self.assertFalse(acted["identity_effect"])

            receipts = port.legible.list_receipts(limit=20)
            actions = {item["action"] for item in receipts}
            self.assertIn("bookmark.open", actions)
            self.assertIn("workbench.act", actions)

    def test_unfinished_read_becomes_continue_card_without_manual_bookmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="📖")
            self._write_multi_chunk_book(home, "unfinished-book.md")
            port = self._port(home)
            opened = port.dispatch(
                {
                    "action": "read",
                    "path": "workspace/unfinished-book.md",
                    "chunk": 0,
                    "max_tokens": 500,
                },
                turn_id="turn-unfinished-open",
                context={"interface": "cli"},
            )
            self.assertTrue(opened["cursor"])

            restarted = self._port(home)
            view = restarted.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-unfinished-view",
                context={"interface": "cli"},
            )
            card = next(
                item
                for item in view["cards"]
                if item["source"]["locator"] == "workspace/unfinished-book.md"
            )
            self.assertEqual("reading.continue", card["provider"])
            self.assertEqual("reading.cursor", card["projection_kind"])
            self.assertIsNone(card["source"]["bookmark_id"])
            self.assertEqual("cursor", card["position"]["resume_mode"])

            acted = restarted.dispatch(
                {
                    "action": "workbench.act",
                    "card_id": card["card_id"],
                    "action_id": "continue",
                    "max_tokens": 500,
                },
                turn_id="turn-unfinished-act",
                context={"interface": "cli"},
            )
            self.assertEqual("continue", acted["underlying_action"])
            self.assertTrue(acted["underlying_receipt_id"])
            self.assertIsNotNone(acted["refreshed_card"])
            self.assertNotEqual(card["card_id"], acted["refreshed_card"]["card_id"])

    def test_cursor_backed_bookmark_resumes_cursor_and_refreshes_card_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="📖")
            self._write_multi_chunk_book(home)
            port = self._port(home)
            opened = port.dispatch(
                {
                    "action": "read",
                    "path": "workspace/cursor-book.md",
                    "chunk": 0,
                    "max_tokens": 500,
                },
                turn_id="turn-open",
                context={"interface": "cli"},
            )
            self.assertTrue(opened["cursor"])
            obj = port.legible.object_by_reference("workspace/cursor-book.md")
            self.assertIsNotNone(obj)
            bookmark_id = port.legible.add_bookmark(
                str(obj["id"]),
                label="Cursor Book",
                location={"cursor": opened["cursor"]},
            )

            restarted = self._port(home)
            view = restarted.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-cursor-view",
                context={"interface": "cli"},
            )
            card = next(
                item for item in view["cards"] if item["source"]["bookmark_id"] == bookmark_id
            )
            self.assertEqual("cursor", card["position"]["resume_mode"])
            old_card_id = card["card_id"]

            acted = restarted.dispatch(
                {
                    "action": "workbench.act",
                    "card_id": old_card_id,
                    "action_id": "continue",
                    "max_tokens": 500,
                },
                turn_id="turn-cursor-act",
                context={"interface": "cli"},
            )
            self.assertEqual("continue", acted["underlying_action"])
            self.assertTrue(acted["underlying_receipt_id"])
            self.assertIsNotNone(acted["refreshed_card"])
            self.assertNotEqual(old_card_id, acted["refreshed_card"]["card_id"])

    def test_expired_cursor_only_bookmark_is_not_projected_as_working_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="📖")
            document = home / "workspace" / "expiring-book.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(("old-page " * 2000) + "\nmore\n", encoding="utf-8")
            port = self._port(home)
            opened = port.dispatch(
                {
                    "action": "read",
                    "path": "workspace/expiring-book.md",
                    "max_tokens": 300,
                },
                turn_id="turn-expiring-open",
                context={"interface": "cli"},
            )
            self.assertTrue(opened["cursor"])
            obj = port.legible.object_by_reference("workspace/expiring-book.md")
            bookmark_id = port.legible.add_bookmark(
                str(obj["id"]),
                label="Expired Cursor Book",
                location={"cursor": opened["cursor"]},
            )
            with port.db.connect() as connection:
                connection.execute(
                    "UPDATE house_cursors SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=?",
                    (opened["cursor"],),
                )

            restarted = self._port(home)
            view = restarted.dispatch(
                {"action": "workbench.view", "lane": "continue"},
                turn_id="turn-expired-view",
                context={"interface": "cli"},
            )
            self.assertNotIn(
                bookmark_id,
                {item["source"]["bookmark_id"] for item in view["cards"]},
            )

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
            self.assertEqual(["reading.continue"], result["providers"])
            self.assertIn("review", result["planned_lanes"])

    def test_provider_registry_is_frozen_and_core_is_provider_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="🪑")
            self._port(home)
            plan = composition_plan()
            self.assertTrue(plan["frozen"])
            self.assertEqual(["reading.continue"], plan["workbench_providers"])

        root = Path(__file__).resolve().parents[1] / "src" / "vestigia"
        core = (root / "workbench.py").read_text(encoding="utf-8")
        reading = (root / "workbench_reading.py").read_text(encoding="utf-8")
        self.assertNotIn("house_cursors", core)
        self.assertNotIn("bookmark.open", core)
        self.assertIn("house_cursors", reading)
        self.assertIn("bookmark.open", reading)


if __name__ == "__main__":
    unittest.main()
