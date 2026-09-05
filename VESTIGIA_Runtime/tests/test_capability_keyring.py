from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.house_tools import HousePort


class CapabilityKeyringTests(unittest.TestCase):
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
    def _dispatch(house: HousePort, payload: dict, *, interface: str = "test") -> dict:
        return house.dispatch(
            {**payload, "after": "finish"},
            turn_id="turn_keyring_fixture",
            context={"interface": interface},
        )

    def test_whoami_exposes_stable_resident_epoch_without_granting_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, house = self._house(Path(tmp))
            first = self._dispatch(house, {"action": "policy.whoami"})
            second = self._dispatch(house, {"action": "policy.whoami"})

            self.assertEqual(first["schema_version"], "vestigia.capability-keyring.v0.1")
            self.assertEqual(first["principal"]["principal_id"], "resident:tester")
            self.assertEqual(first["principal"]["room_id"], "hearth")
            self.assertEqual(first["authority_epoch"], 1)
            self.assertEqual(second["authority_epoch"], 1)
            self.assertEqual(
                first["capability_surface"]["digest_sha256"],
                second["capability_surface"]["digest_sha256"],
            )
            self.assertGreater(first["capability_surface"]["count"], 4)
            self.assertFalse(first["general_keyring_enforcement_active"])
            self.assertFalse(first["invariants"]["preview_is_authorization"])

    def test_preview_validates_and_hashes_stage_patch_without_staging_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, house = self._house(Path(tmp))
            before = self._dispatch(
                house,
                {"action": "fs.patch_list", "status": "staged"},
            )
            preview = self._dispatch(
                house,
                {
                    "action": "capability.preview",
                    "capability": "fs.stage_patch",
                    "arguments": {
                        "operation": "create",
                        "path": "workspace/keyring-demo.md",
                        "content": "proposal only\n",
                    },
                },
            )
            after = self._dispatch(
                house,
                {"action": "fs.patch_list", "status": "staged"},
            )

            preflight = preview["preflight"]
            self.assertEqual(preflight["decision"], "allow")
            self.assertEqual(preflight["effect_class"], "prepare")
            self.assertTrue(preflight["schema_valid"])
            self.assertEqual(len(preflight["candidate_payload_sha256"]), 64)
            self.assertEqual(preflight["target"]["path"], "workspace/keyring-demo.md")
            self.assertEqual(preflight["target"]["scope_class"], "workspace")
            self.assertNotIn("content", preflight["target"])
            self.assertFalse(preview["preview_is_authorization"])
            self.assertFalse(preview["approval_created"])
            self.assertFalse(preview["target_executed"])
            self.assertFalse(preview["canonical_changed"])
            self.assertEqual(before["patches"], after["patches"])
            self.assertFalse((home / "workspace" / "keyring-demo.md").exists())

    def test_policy_explain_surfaces_legacy_unkeyed_act_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, house = self._house(Path(tmp))
            explained = self._dispatch(
                house,
                {"action": "policy.explain", "capability": "file.write"},
            )
            preflight = explained["preflight"]

            self.assertEqual(preflight["decision"], "allow")
            self.assertEqual(preflight["effect_class"], "act")
            self.assertTrue(preflight["legacy_unkeyed_authority"])
            self.assertTrue(preflight["keyring_gap"])
            self.assertFalse(preflight["focused_authorizer_executed"])
            self.assertFalse(preflight["target_handler_executed"])
            self.assertFalse(preflight["final_dispatch_recheck_implemented"])
            self.assertEqual(
                explained["runtime_contract"]["confirmation"],
                "none",
            )

    def test_policy_can_reports_confirmation_and_schema_failure_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, house = self._house(Path(tmp))
            outward = self._dispatch(
                house,
                {"action": "policy.can", "capability": "discord.react"},
            )
            self.assertEqual(outward["decision"], "confirm")
            self.assertTrue(outward["outward_facing"])
            self.assertFalse(outward["focused_authorizer_executed"])

            invalid = self._dispatch(
                house,
                {
                    "action": "policy.can",
                    "capability": "fs.stage_patch",
                    "arguments": {"operation": "create"},
                },
            )
            self.assertEqual(invalid["decision"], "deny")
            self.assertFalse(invalid["schema_valid"])
            self.assertIn("required", invalid["schema_error"])

            unknown = self._dispatch(
                house,
                {"action": "policy.can", "capability": "definitely.not.real"},
            )
            self.assertEqual(unknown["decision"], "deny")
            self.assertFalse(unknown["known"])


if __name__ == "__main__":
    unittest.main()
