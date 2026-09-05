from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vestigia.config import load_config


class ContextSourceConfigTests(unittest.TestCase):
    def _home(self, root: Path, archive: Path, *, max_items: int = 6) -> Path:
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
            "    enabled: true\n"
            f"    live_archive_root: '{archive.as_posix()}'\n"
            f"    max_items: {max_items}\n"
            "    resident_key: Tester\n",
            encoding="utf-8",
        )
        return home

    def test_home_yaml_can_configure_mcp_context_source_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            home = self._home(root, archive)
            with patch.dict(
                os.environ,
                {
                    "VESTIGIA_CONTEXT_MCP_ENABLED": "",
                    "VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT": "",
                    "VESTIGIA_CONTEXT_MCP_MAX_ITEMS": "",
                },
                clear=False,
            ):
                config = load_config(home)

            self.assertTrue(config.get("context_sources.mcp_archive.enabled"))
            self.assertEqual(
                config.get("context_sources.mcp_archive.live_archive_root"),
                archive.as_posix(),
            )
            self.assertEqual(config.get("context_sources.mcp_archive.max_items"), 6)
            self.assertEqual(
                config.sources["context_sources.mcp_archive.enabled"],
                "home.yaml",
            )
            self.assertEqual(
                config.sources["context_sources.mcp_archive.max_items"],
                "home.yaml",
            )

    def test_process_environment_overrides_home_yaml_through_normal_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            override = root / "override"
            archive.mkdir()
            override.mkdir()
            home = self._home(root, archive)
            with patch.dict(
                os.environ,
                {
                    "VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT": str(override),
                    "VESTIGIA_CONTEXT_MCP_MAX_ITEMS": "4",
                },
                clear=False,
            ):
                config = load_config(home)

            self.assertEqual(
                config.get("context_sources.mcp_archive.live_archive_root"),
                str(override),
            )
            self.assertEqual(config.get("context_sources.mcp_archive.max_items"), 4)
            self.assertEqual(
                config.sources["context_sources.mcp_archive.live_archive_root"],
                "environment:VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT",
            )

    def test_invalid_enabled_source_bounds_fail_during_config_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            home = self._home(root, archive, max_items=0)
            with self.assertRaisesRegex(
                ValueError,
                "context_sources.mcp_archive.max_items must be between 1 and 50",
            ):
                load_config(home)


if __name__ == "__main__":
    unittest.main()
