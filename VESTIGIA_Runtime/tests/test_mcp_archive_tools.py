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
    def _house(self, root: Path, *, enabled: bool = True) -> HousePort:
        archive = root / "archive"
        archive.mkdir()
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
            },
            clear=False,
        ):
            config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        db.initialize()
        (home / "traces").mkdir(exist_ok=True)
        images = ImageService(config, db, fake=True)
        return HousePort(config, db, image_service=images)

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

        self.assertTrue(text_result["ok"])
        self.assertEqual(text_result["content"], "The lantern crossed the bridge.\n")
        self.assertEqual(text_result["mcp"]["tool"], "archive.read_text")
        self.assertTrue(media_result["ok"])
        self.assertTrue(media_result["image_id"].startswith("img_"))
        self.assertGreater(media_result["size"], 0)
        self.assertEqual(media_result["mcp"]["tool"], "archive.read_media")


if __name__ == "__main__":
    unittest.main()
