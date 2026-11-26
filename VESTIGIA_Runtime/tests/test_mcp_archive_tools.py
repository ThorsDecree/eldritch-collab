from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.house_tools import HousePort
from vestigia.images import ImageService
from vestigia.mcp_context_source import VestigiaArchiveMcpSource


def _mcp_integration_available() -> bool:
    return (
        importlib.util.find_spec("mcp") is not None
        and importlib.util.find_spec("vestigia_mcp") is not None
    )


class McpArchiveToolTests(unittest.TestCase):
    def _house(
        self, root: Path, *, enabled: bool = True, write_prefixes: str = "", snapshot: Path | None = None,
    ) -> HousePort:
        archive = root / "archive"
        archive.mkdir(parents=True)
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
            "    - local-user\n"
            "context_sources:\n"
            "  mcp_archive:\n"
            f"    enabled: {'true' if enabled else 'false'}\n"
            f"    live_archive_root: '{archive.as_posix()}'\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "VESTIGIA_CONTEXT_MCP_ENABLED": "",
                "VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT": "",
                "VESTIGIA_CONTEXT_MCP_SNAPSHOT_ARCHIVE_ROOT": str(snapshot) if snapshot else "",
                "VESTIGIA_CONTEXT_MCP_WRITE_PREFIXES": write_prefixes,
            },
            clear=False,
        ):
            config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        db.initialize()
        (home / "traces").mkdir(exist_ok=True)
        images = ImageService(config, db, fake=True)
        return HousePort(config, db, image_service=images)

    def test_write_grant_is_explicit_and_separate_from_tunnel_and_browse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES": "House", "OPENAI_API_KEY": "synthetic-key"},
        ):
            root = Path(tmp)
            read_only = self._house(root / "read_only")
            self.assertNotIn("mcp.archive.promote", {item["name"] for item in read_only.registry.describe()})
            house = self._house(root / "opted_in", write_prefixes="Vex, Notes")
            with patch.object(VestigiaArchiveMcpSource, "call_tool", autospec=True) as call:
                call.return_value = {"structured_content": {}}
                house.dispatch({"action": "mcp.archive.status"})
                house.dispatch(
                    {"action": "mcp.archive.write_capabilities"},
                    turn_id="turn_grants",
                    context={"invocation": "conversation", "interface": "web"},
                )

        browse, write = (entry.args[0]._child_env() for entry in call.call_args_list)
        self.assertNotIn("VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES", browse)
        self.assertEqual(write["VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES"], "Vex,Notes")
        self.assertNotEqual(browse["VESTIGIA_MCP_STATE_DIR"], write["VESTIGIA_MCP_STATE_DIR"])
        self.assertNotIn("OPENAI_API_KEY", write)
        self.assertNotIn("VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT", write)

    def test_archive_write_rejects_untrusted_turns_before_mcp(self) -> None:
        dm = {"kind": "discord_dm", "id": "test-dm"}
        conversation = {"invocation": "conversation", "interface": "discord", "delivery_target": dm}
        blocked_contexts = [
            {},
            {"invocation": "conversation", "interface": "bell"},
            {"invocation": "private_curation", "interface": "curation"},
            {**conversation, "is_group_dm": True},
            {**conversation, "delivery_target": {"kind": "discord_channel", "id": "test-channel"}},
        ]
        payload = {
            "action": "mcp.archive.promote",
            "stage_id": "archive_stage_" + "a" * 32,
            "proposal_sha256": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            VestigiaArchiveMcpSource, "call_tool",
        ) as call:
            house = self._house(Path(tmp), write_prefixes="Vex")
            for context in blocked_contexts:
                with self.subTest(context=context), self.assertRaises(PermissionError):
                    house.dispatch(payload, turn_id="turn_denied", context=context)
            with self.assertRaises(PermissionError):
                house.dispatch(payload, context=conversation)
            self.assertNotIn("mcp.archive.promote", house.registry.forgeable_names())
            call.assert_not_called()

    def test_archive_capabilities_are_discoverable_and_config_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            house = self._house(Path(tmp), enabled=False)
            described = {
                item["name"]: item for item in house.registry.describe()
            }

        expected = {
            "mcp.archive.status",
            "mcp.archive.list",
            "mcp.archive.search",
            "mcp.archive.read_text",
            "mcp.archive.read_media",
            "mcp.archive.health",
            "mcp.archive.diff_detail",
        }
        self.assertTrue(expected.issubset(described))
        for name in expected:
            self.assertFalse(described[name]["enabled"])
            self.assertFalse(described[name]["callable_now"])

    def test_search_dispatches_only_the_fixed_mcp_tool_and_arguments(self) -> None:
        response = {
            "tool": "archive.search_text",
            "structured_content": {"source": "live", "hits": []},
            "text_blocks": [],
            "image_blocks": [],
            "protocol_version": "2025-11-25",
            "server_name": "VESTIGIA MCP Server",
            "server_version": "0.3.0.dev0",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            VestigiaArchiveMcpSource,
            "call_tool",
            return_value=response,
        ) as call:
            house = self._house(Path(tmp))
            result = house.dispatch(
                {
                    "action": "mcp.archive.search",
                    "source": "live",
                    "query": "lantern",
                    "prefix": "Liora",
                    "limit": 7,
                    "case_sensitive": False,
                },
                turn_id="turn_mcp_search",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["hits"], [])
        self.assertFalse(result["mcp"]["archive_mutated"])
        call.assert_called_once_with(
            "archive.search_text",
            {
                "source": "live",
                "query": "lantern",
                "prefix": "Liora",
                "limit": 7,
                "case_sensitive": False,
            },
        )

    def test_media_read_verifies_and_imports_private_image_without_receipt_base64(self) -> None:
        stream = BytesIO()
        Image.new("RGB", (2, 3), (236, 36, 143)).save(stream, format="PNG")
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        response = {
            "tool": "archive.read_media",
            "structured_content": None,
            "text_blocks": [
                (
                    '{"source":"live","path":"Liora/pics/test.png",'
                    f'"size":{len(data)},"sha256":"{digest}","mime_type":"image/png"}}'
                )
            ],
            "image_blocks": [
                {"data": base64.b64encode(data).decode("ascii"), "mime_type": "image/png"}
            ],
            "protocol_version": "2025-11-25",
            "server_name": "VESTIGIA MCP Server",
            "server_version": "0.3.0.dev0",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            VestigiaArchiveMcpSource,
            "call_tool",
            return_value=response,
        ):
            house = self._house(Path(tmp))
            result = house.dispatch(
                {
                    "action": "mcp.archive.read_media",
                    "source": "live",
                    "path": "Liora/pics/test.png",
                },
                turn_id="turn_mcp_media",
            )
            asset = house.images.get_asset(result["image_id"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["archive_sha256"], digest)
        self.assertEqual(result["privacy"], "private")
        self.assertTrue(result["mcp"]["local_private_image_imported"])
        self.assertIsNotNone(asset)
        self.assertEqual(asset["source_kind"], "mcp_archive")
        self.assertEqual(asset["source"]["archive_path"], "Liora/pics/test.png")
        self.assertNotIn(base64.b64encode(data).decode("ascii"), str(result))

    def test_text_read_applies_runtime_result_ceiling_without_forwarding_it(self) -> None:
        response = {
            "tool": "archive.read_text",
            "structured_content": {
                "source": "live",
                "path": "long.md",
                "content": "x" * 700,
            },
            "text_blocks": [],
            "image_blocks": [],
            "protocol_version": "2025-11-25",
            "server_name": "VESTIGIA MCP Server",
            "server_version": "0.3.0.dev0",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            VestigiaArchiveMcpSource,
            "call_tool",
            return_value=response,
        ) as call:
            house = self._house(Path(tmp))
            result = house.dispatch(
                {
                    "action": "mcp.archive.read_text",
                    "source": "live",
                    "path": "long.md",
                    "max_chars": 500,
                },
                turn_id="turn_mcp_bounded_text",
            )

        self.assertEqual(len(result["content"]), 500)
        self.assertTrue(result["content_truncated"])
        self.assertEqual(result["source_characters"], 700)
        call.assert_called_once_with(
            "archive.read_text",
            {"source": "live", "path": "long.md"},
        )

    @unittest.skipUnless(
        _mcp_integration_available(),
        "requires optional mcp-context dependency and installed VESTIGIA MCP server",
    )
    def test_real_stdio_server_round_trip_reads_text_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            house = self._house(root)
            archive = root / "archive"
            (archive / "Liora" / "pics").mkdir(parents=True)
            (archive / "lantern.md").write_text(
                "The lantern crossed the bridge.\n",
                encoding="utf-8",
            )
            Image.new("RGB", (3, 2), (15, 20, 30)).save(
                archive / "Liora" / "pics" / "bridge.png",
                format="PNG",
            )

            status_result = house.dispatch(
                {"action": "mcp.archive.status"}, turn_id="turn_mcp_real_status",
            )
            text_result = house.dispatch(
                {
                    "action": "mcp.archive.read_text",
                    "source": "live",
                    "path": "lantern.md",
                },
                turn_id="turn_mcp_real_text",
            )
            media_result = house.dispatch(
                {
                    "action": "mcp.archive.read_media",
                    "source": "live",
                    "path": "Liora/pics/bridge.png",
                },
                turn_id="turn_mcp_real_media",
            )

        self.assertTrue(status_result["ok"])
        self.assertEqual(status_result["live"]["file_count"], 2)
        self.assertTrue(text_result["ok"])
        self.assertEqual(
            text_result["content"].splitlines(),
            ["The lantern crossed the bridge."],
        )
        self.assertEqual(text_result["mcp"]["tool"], "archive.read_text")
        self.assertTrue(media_result["ok"])
        self.assertTrue(media_result["image_id"].startswith("img_"))
        self.assertGreater(media_result["size"], 0)
        self.assertEqual(media_result["mcp"]["tool"], "archive.read_media")

    @unittest.skipUnless(
        _mcp_integration_available(),
        "requires optional mcp-context dependency and installed VESTIGIA MCP server",
    )
    def test_real_stdio_stages_promotes_and_reads_back_live_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot.zip"
            with ZipFile(snapshot, "w") as archived:
                archived.writestr("Vex/canary.md", "Snapshot baseline\n")
            snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            house = self._house(root, write_prefixes="Vex", snapshot=snapshot)
            archive = root / "archive"
            (archive / "Vex").mkdir()
            (archive / "VexOther").mkdir()
            target = archive / "Vex" / "canary.md"
            discord_dm = {
                "interface": "discord",
                "invocation": "conversation",
                "delivery_target": {"kind": "discord_dm", "id": "test-dm"},
            }
            create = {
                "action": "mcp.archive.stage_text",
                "path": "Vex/canary.md",
                "content": "Synthetic canary\n",
                "expected_base_sha256": "absent",
            }
            with self.assertRaisesRegex(RuntimeError, "outside configured canonical write prefixes"):
                house.dispatch({**create, "path": "VexOther/blocked.md"}, turn_id="denied", context=discord_dm)
            staged = house.dispatch(create, turn_id="stage_create", context=discord_dm)
            self.assertFalse(target.exists())
            self.assertFalse(staged["mcp"]["archive_mutated"])
            self.assertNotIn("content", staged)

            home = root / "home"
            restarted = HousePort(house.config, ContinuityDB(home / "memory" / "continuity.db"))
            stage_id = staged["stage_id"]
            pending = restarted.dispatch(
                {"action": "mcp.archive.stage_list", "limit": 10},
                turn_id="list_pending", context=discord_dm,
            )
            self.assertEqual([item["stage_id"] for item in pending["stages"]], [stage_id])
            inspected = restarted.dispatch(
                {"action": "mcp.archive.stage_inspect", "stage_id": stage_id},
                turn_id="inspect", context=discord_dm,
            )
            self.assertTrue(inspected["validation"]["ready"])
            promote = {
                "action": "mcp.archive.promote",
                "stage_id": stage_id,
                "proposal_sha256": inspected["proposal_sha256"],
            }
            with self.assertRaisesRegex(RuntimeError, "Proposal digest does not match"):
                restarted.dispatch({**promote, "proposal_sha256": "0" * 64}, turn_id="bad_digest", context=discord_dm)
            committed = restarted.dispatch(promote, turn_id="promote_create", context=discord_dm)
            self.assertTrue(committed["mcp"]["archive_mutated"])
            self.assertEqual(target.read_bytes(), b"Synthetic canary\n")
            receipt = restarted.legible.inspect_receipt(committed["receipt_id"])
            self.assertEqual(receipt["status"], "succeeded")
            self.assertEqual(receipt["target"]["stage_id"], stage_id)
            self.assertEqual(receipt["result"]["result_sha256"], committed["result_sha256"])
            retried = restarted.dispatch(promote, turn_id="retry_create", context=discord_dm)
            self.assertTrue(retried["already_promoted"])
            self.assertFalse(retried["mcp"]["archive_mutated"])

            read = {"action": "mcp.archive.read_text", "source": "live", "path": "Vex/canary.md"}
            initial = restarted.dispatch(read, turn_id="read_create")
            self.assertEqual(initial["content_sha256"], committed["result_sha256"])
            replacement = restarted.dispatch(
                {**create, "content": "Authorized edit\n", "expected_base_sha256": initial["content_sha256"]},
                turn_id="stage_edit", context=discord_dm,
            )
            target.write_bytes(b"Concurrent edit\n")
            promote_edit = {
                **promote,
                "stage_id": replacement["stage_id"],
                "proposal_sha256": replacement["proposal_sha256"],
            }
            with self.assertRaisesRegex(RuntimeError, "changed after staging"):
                restarted.dispatch(promote_edit, turn_id="stale", context=discord_dm)
            self.assertEqual(target.read_bytes(), b"Concurrent edit\n")
            discarded = restarted.dispatch(
                {"action": "mcp.archive.stage_discard", "stage_id": replacement["stage_id"]},
                turn_id="discard_stale", context=discord_dm,
            )
            self.assertEqual(discarded["status"], "discarded")
            replacement = restarted.dispatch(
                {
                    **create,
                    "content": "Authorized edit\n",
                    "expected_base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                },
                turn_id="restage_edit", context=discord_dm,
            )
            edited = restarted.dispatch(
                {**promote_edit, "stage_id": replacement["stage_id"], "proposal_sha256": replacement["proposal_sha256"]},
                turn_id="promote_edit", context=discord_dm,
            )
            final = restarted.dispatch(read, turn_id="read_edit")
            self.assertEqual(final["content"], "Authorized edit\n")
            self.assertEqual(final["content_sha256"], edited["result_sha256"])
            self.assertEqual(final["content_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(list((archive / "VexOther").iterdir()), [])
            unchanged = restarted.dispatch({**read, "source": "snapshot"}, turn_id="read_snapshot")
            self.assertEqual(unchanged["content"], "Snapshot baseline\n")
            self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot_hash)


if __name__ == "__main__":
    unittest.main()
