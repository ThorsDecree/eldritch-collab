from __future__ import annotations

import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from vestigia.capabilities import is_formal_object_schema
from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.house_tools import HouseCursorExpiredError
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


class ImageDrawerContinuationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Drawer Resident", glyph="R"
        )
        self.config = load_config(self.home)
        self.runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def png(index: int) -> bytes:
        buffer = io.BytesIO()
        Image.new(
            "RGB",
            (2, 2),
            ((index * 41) % 256, (index * 83) % 256, (index * 127) % 256),
        ).save(buffer, format="PNG")
        return buffer.getvalue()

    def add_images(self, count: int, *, term: str = "") -> list[str]:
        result: list[str] = []
        for index in range(count):
            asset = self.runtime.images.ingest_bytes(
                self.png(index + 1),
                filename=f"drawer-{index:03d}.png",
            )
            image_id = str(asset["id"])
            result.append(image_id)
            if term:
                self.runtime.images.update_card(
                    image_id,
                    {
                        "alias": f"{term}-{index:03d}",
                        "summary": f"{term} collection item {index}",
                        "resident_note": f"Stable pagination fixture {index}",
                    },
                    actor="resident:test",
                )
        return result


class ImageDrawerContinuationTests(ImageDrawerContinuationCase):
    def test_browse_cursor_resumes_across_runtime_restart(self) -> None:
        self.add_images(7)
        first = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "browse", "limit": 3}
        )
        self.assertEqual(3, len(first["cards"]))
        self.assertTrue(first["pagination"]["stable_snapshot"])
        self.assertTrue(first["pagination"]["next_cursor"])
        self.assertFalse(first["provider_call"])
        self.assertFalse(first["resident_model_call"])
        self.assertFalse(first["outward_action"])

        first_ids = [card["image_id"] for card in first["cards"]]
        cursor = first["pagination"]["next_cursor"]
        restarted = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        second = restarted.house.dispatch(
            {"action": "image.drawer", "mode": "continue", "cursor": cursor}
        )
        second_ids = [card["image_id"] for card in second["cards"]]
        self.assertEqual(3, len(second_ids))
        self.assertTrue(set(first_ids).isdisjoint(second_ids))
        self.assertEqual(2, second["pagination"]["page_number"])
        self.assertEqual(7, second["pagination"]["total_items"])

    def test_bookmark_preserves_exact_snapshot_position_after_collection_changes(self) -> None:
        self.add_images(8)
        first = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "browse", "limit": 3}
        )
        second = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "continue",
                "cursor": first["pagination"]["next_cursor"],
            }
        )
        expected_ids = [card["image_id"] for card in second["cards"]]
        saved = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "bookmark",
                "cursor": second["pagination"]["current_cursor"],
                "label": "Middle of the drawer",
                "note": "Resume here after the collection grows.",
            }
        )
        bookmark_id = saved["bookmark"]["id"]
        self.assertTrue(saved["stable_snapshot_preserved"])

        self.runtime.images.ingest_bytes(
            self.png(99), filename="newest-after-bookmark.png"
        )
        restarted = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        reopened = restarted.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "open_bookmark",
                "bookmark_id": bookmark_id,
            }
        )
        self.assertEqual(expected_ids, [card["image_id"] for card in reopened["cards"]])
        self.assertEqual("Middle of the drawer", reopened["bookmark"]["label"])
        self.assertEqual(
            second["pagination"]["snapshot_fingerprint"],
            reopened["pagination"]["snapshot_fingerprint"],
        )
        self.assertIsNone(reopened["pagination"]["expires_at"])

    def test_search_pages_are_stable_and_bookmark_listing_hides_raw_query(self) -> None:
        self.add_images(9, term="gutterstar")
        first = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "search",
                "query": "gutterstar collection",
                "limit": 4,
            }
        )
        self.assertEqual(4, len(first["cards"]))
        second = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "continue",
                "cursor": first["pagination"]["next_cursor"],
            }
        )
        self.assertTrue(
            set(card["image_id"] for card in first["cards"]).isdisjoint(
                card["image_id"] for card in second["cards"]
            )
        )
        saved = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "bookmark",
                "cursor": second["pagination"]["current_cursor"],
                "label": "Gutterstar page two",
            }
        )
        listed = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "list_bookmarks"}
        )
        self.assertEqual(saved["bookmark"]["id"], listed["bookmarks"][0]["id"])
        self.assertNotIn("query_text", listed["bookmarks"][0])
        self.assertTrue(listed["bookmarks"][0]["query_hash"])

    def test_expired_cursor_fails_with_safe_restart_payload(self) -> None:
        self.add_images(4)
        first = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "browse", "limit": 2}
        )
        cursor = first["pagination"]["next_cursor"]
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        with self.runtime.db.connect() as connection:
            session_id = connection.execute(
                "SELECT session_id FROM image_drawer_cursors WHERE id=?",
                (cursor,),
            ).fetchone()["session_id"]
            connection.execute(
                "UPDATE image_drawer_sessions SET expires_at=? WHERE id=?",
                (past, session_id),
            )
            connection.execute(
                "UPDATE image_drawer_cursors SET expires_at=? WHERE id=?",
                (past, cursor),
            )
        with self.assertRaises(HouseCursorExpiredError) as caught:
            self.runtime.house.dispatch(
                {"action": "image.drawer", "mode": "continue", "cursor": cursor}
            )
        self.assertEqual("image_drawer_cursor_expired", caught.exception.house_error_code)
        retry = caught.exception.house_suggested_retry
        self.assertEqual("image.drawer", retry["action"])
        self.assertEqual("browse", retry["mode"])
        self.assertEqual(2, retry["limit"])

    def test_bookmark_remove_and_capability_contract_are_explicit(self) -> None:
        self.add_images(3)
        page = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "browse", "limit": 2}
        )
        saved = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "bookmark",
                "cursor": page["pagination"]["current_cursor"],
                "label": "Start",
            }
        )
        removed = self.runtime.house.dispatch(
            {
                "action": "image.drawer",
                "mode": "remove_bookmark",
                "bookmark_id": saved["bookmark"]["id"],
            }
        )
        self.assertEqual("removed", removed["status"])
        listed = self.runtime.house.dispatch(
            {"action": "image.drawer", "mode": "list_bookmarks"}
        )
        self.assertEqual([], listed["bookmarks"])

        capability = self.runtime.house.dispatch(
            {"action": "capabilities", "target": "image.drawer"}
        )["capability"]
        self.assertTrue(is_formal_object_schema(capability["input_schema"]))
        modes = capability["input_schema"]["properties"]["mode"]["enum"]
        for mode in (
            "continue",
            "bookmark",
            "open_bookmark",
            "list_bookmarks",
            "remove_bookmark",
        ):
            self.assertIn(mode, modes)
        self.assertIn("cursor", capability["input_schema"]["properties"])
        self.assertIn("bookmark_id", capability["input_schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
