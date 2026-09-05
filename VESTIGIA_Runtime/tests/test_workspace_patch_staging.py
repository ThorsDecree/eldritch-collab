from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.house_tools import HousePort


class WorkspacePatchStagingTests(unittest.TestCase):
    def _house(self, root: Path) -> tuple[Path, HousePort]:
        home = root / "home"
        home.mkdir()
        (home / "home.yaml").write_text(
            "resident:\n"
            "  id: tester\n"
            "  name: Tester\n"
            "room:\n"
            "  id: hearth\n"
            "  name: Hearth\n"
            "  active_resident_ids:\n"
            "    - tester\n"
            "  participant_ids:\n"
            "    - tester\n"
            "    - local-user\n",
            encoding="utf-8",
        )
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        db.initialize()
        return home, HousePort(config, db)

    @staticmethod
    def _dispatch(house: HousePort, payload: dict) -> dict:
        return house.dispatch(
            {**payload, "after": "finish"},
            turn_id="turn_patch_fixture",
            context={"interface": "test"},
        )

    def test_create_patch_is_durable_proposal_but_does_not_write_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, house = self._house(Path(tmp))
            target = home / "workspace" / "hello.md"
            staged = self._dispatch(
                house,
                {
                    "action": "fs.stage_patch",
                    "operation": "create",
                    "path": "workspace/hello.md",
                    "content": "hello from staging\n",
                    "reason": "fixture proposal",
                },
            )

            self.assertTrue(staged["ok"])
            self.assertFalse(target.exists())
            self.assertTrue(staged["proposal_only"])
            self.assertFalse(staged["workspace_changed"])
            self.assertFalse(staged["apply_capability_available"])
            patch_id = staged["patch_id"]

            listed = self._dispatch(
                house,
                {"action": "fs.patch_list", "status": "staged"},
            )
            patch = next(item for item in listed["patches"] if item["patch_id"] == patch_id)
            self.assertTrue(patch["candidate_content_stored"])
            self.assertNotIn("candidate_content", patch)

            validation = self._dispatch(
                house,
                {"action": "fs.patch_validate", "patch_id": patch_id},
            )
            self.assertTrue(validation["validation"]["valid"])
            self.assertFalse(target.exists())

    def test_edit_patch_becomes_stale_when_base_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, house = self._house(Path(tmp))
            target = home / "workspace" / "notes.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("old\n", encoding="utf-8")

            staged = self._dispatch(
                house,
                {
                    "action": "fs.stage_patch",
                    "operation": "edit",
                    "path": "workspace/notes.md",
                    "content": "new\n",
                },
            )
            patch_id = staged["patch_id"]
            first = self._dispatch(
                house,
                {"action": "fs.patch_preview", "patch_id": patch_id},
            )
            self.assertTrue(first["validation"]["valid"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

            target.write_text("changed somewhere else\n", encoding="utf-8")
            stale = self._dispatch(
                house,
                {"action": "fs.patch_validate", "patch_id": patch_id},
            )
            self.assertFalse(stale["validation"]["valid"])
            self.assertEqual(stale["validation"]["reason"], "source_hash_changed")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "changed somewhere else\n",
            )

    def test_delete_and_move_are_previewable_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, house = self._house(Path(tmp))
            workspace = home / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            delete_target = workspace / "delete-me.txt"
            move_source = workspace / "move-me.txt"
            move_destination = workspace / "moved.txt"
            delete_target.write_text("delete candidate\n", encoding="utf-8")
            move_source.write_text("move candidate\n", encoding="utf-8")

            delete_patch = self._dispatch(
                house,
                {
                    "action": "fs.stage_patch",
                    "operation": "delete",
                    "path": "workspace/delete-me.txt",
                },
            )
            move_patch = self._dispatch(
                house,
                {
                    "action": "fs.stage_patch",
                    "operation": "move",
                    "path": "workspace/move-me.txt",
                    "destination": "workspace/moved.txt",
                },
            )

            self.assertTrue(delete_target.is_file())
            self.assertTrue(move_source.is_file())
            self.assertFalse(move_destination.exists())
            delete_validation = self._dispatch(
                house,
                {
                    "action": "fs.patch_validate",
                    "patch_id": delete_patch["patch_id"],
                },
            )
            move_validation = self._dispatch(
                house,
                {
                    "action": "fs.patch_validate",
                    "patch_id": move_patch["patch_id"],
                },
            )
            self.assertTrue(delete_validation["validation"]["valid"])
            self.assertTrue(move_validation["validation"]["valid"])
            self.assertFalse(
                move_validation["validation"]["preview"]["destination"][
                    "full_destination_diff_available"
                ]
            )

    def test_discard_changes_only_draft_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, house = self._house(Path(tmp))
            target = home / "workspace" / "discard.md"
            staged = self._dispatch(
                house,
                {
                    "action": "fs.stage_patch",
                    "operation": "create",
                    "path": "workspace/discard.md",
                    "content": "never applied\n",
                },
            )
            discarded = self._dispatch(
                house,
                {
                    "action": "fs.patch_discard",
                    "patch_id": staged["patch_id"],
                    "reason": "changed my mind",
                },
            )
            self.assertEqual(discarded["status"], "discarded")
            self.assertFalse(target.exists())
            validation = self._dispatch(
                house,
                {
                    "action": "fs.patch_validate",
                    "patch_id": staged["patch_id"],
                },
            )
            self.assertFalse(validation["validation"]["valid"])
            self.assertEqual(
                validation["validation"]["reason"],
                "patch_status_discarded",
            )

    def test_stage_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, house = self._house(Path(tmp))
            with self.assertRaisesRegex(ValueError, "workspace shelf"):
                self._dispatch(
                    house,
                    {
                        "action": "fs.stage_patch",
                        "operation": "create",
                        "path": "identity/nope.md",
                        "content": "nope",
                    },
                )


if __name__ == "__main__":
    unittest.main()
