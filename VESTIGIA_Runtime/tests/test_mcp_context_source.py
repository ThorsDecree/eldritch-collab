from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vestigia.bootstrap import bootstrap_runtime
from vestigia.config import load_config
from vestigia.context import ContextAssembler
from vestigia.db import ContinuityDB
from vestigia.models import NormalizedMessage, RuntimeState


class McpContextSourceTests(unittest.TestCase):
    def _fixture(self, root: Path):
        archive = root / "archive"
        (archive / "00_Bootloader").mkdir(parents=True)
        (archive / "Tester").mkdir()
        (archive / "manifest.md").write_text(
            "The lantern is lit in the test house.\n",
            encoding="utf-8",
        )
        (archive / "Tester" / "breathprint.md").write_text(
            "# Tester Breathprint\n\nI keep a brass lantern by the window.\n",
            encoding="utf-8",
        )
        registry = {
            "schema_version": "0.1",
            "generated": "2026-09-05T00:00:00-05:00",
            "archive_root": ".",
            "anchors": {"root_manifest": "manifest.md"},
            "residents": {
                "Tester": {
                    "shell": "Tester",
                    "breathprint": "Tester/breathprint.md",
                }
            },
            "garden_breathprints": {},
        }
        (archive / "00_Bootloader" / "house_index.json").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

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
        (home / "traces").mkdir()
        db = ContinuityDB(home / "memory" / "continuity.db")
        db.initialize()
        return archive, home, db

    def test_stdio_mcp_source_brings_archive_evidence_into_separate_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive, home, db = self._fixture(Path(tmp))
            env = {
                "VESTIGIA_CONTEXT_MCP_ENABLED": "true",
                "VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT": str(archive),
                "VESTIGIA_CONTEXT_MCP_RESIDENT_KEY": "Tester",
                "VESTIGIA_CONTEXT_MCP_MAX_TERMS": "2",
                "VESTIGIA_CONTEXT_MCP_MAX_ITEMS": "8",
                "VESTIGIA_CONTEXT_MCP_TIMEOUT_SECONDS": "30",
                "VESTIGIA_CONTEXT_MCP_ARCHIVE_TEXT_MAX_BYTES": "777777",
                # Deliberately set parent-only values that must not be forwarded to
                # the Archive child transport.
                "VESTIGIA_MCP_RUNTIME_HOME": str(home),
                "OPENAI_API_KEY": "must-not-cross-context-child",
                "DISCORD_BOT_TOKEN": "must-not-cross-context-child",
            }
            with patch.dict(os.environ, env, clear=False):
                # The source configuration must enter through Runtime's normal
                # built-in -> home.yaml -> env-file -> process-env resolver.
                config = load_config(home)
                self.assertTrue(config.get("context_sources.mcp_archive.enabled"))
                self.assertEqual(
                    config.sources["context_sources.mcp_archive.enabled"],
                    "environment:VESTIGIA_CONTEXT_MCP_ENABLED",
                )
                self.assertEqual(
                    config.sources["context_sources.mcp_archive.live_archive_root"],
                    "environment:VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT",
                )
                bootstrap_runtime()
                assembler = ContextAssembler(config, db)
                names = [source.name for source in assembler.context_sources]
                self.assertIn("vestigia_archive_mcp", names)
                mcp_source = next(
                    source
                    for source in assembler.context_sources
                    if source.name == "vestigia_archive_mcp"
                )
                child_env = mcp_source._child_env()
                self.assertNotIn("VESTIGIA_MCP_RUNTIME_HOME", child_env)
                self.assertNotIn("OPENAI_API_KEY", child_env)
                self.assertNotIn("DISCORD_BOT_TOKEN", child_env)
                self.assertEqual(
                    child_env["VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES"],
                    "777777",
                )

                before = db.list_memories(
                    resident_id="tester",
                    room_id="hearth",
                    limit=100,
                )
                assembly = assembler.assemble(
                    NormalizedMessage(content="Where is the lantern?"),
                    state=RuntimeState.ACTIVE.value,
                    turn_id="turn_mcp_context_source",
                )
                after = db.list_memories(
                    resident_id="tester",
                    room_id="hearth",
                    limit=100,
                )

            self.assertEqual([item.id for item in before], [item.id for item in after])
            layer = next(
                item for item in assembly.layers if item.name == "archive_mcp_context"
            )
            self.assertIn("MCP ARCHIVE RESIDENT ANCHOR", layer.text)
            self.assertIn("brass lantern", layer.text)
            self.assertIn("MCP ARCHIVE EVIDENCE", layer.text)

            receipt = json.loads(assembly.receipt_path.read_text(encoding="utf-8"))
            source = next(
                item
                for item in receipt["context_sources"]
                if item["name"] == "vestigia_archive_mcp"
            )
            self.assertTrue(source["available"])
            self.assertTrue(source["advisory"])
            self.assertEqual(source["metadata"]["transport"], "stdio_child")
            self.assertEqual(
                source["metadata"]["configuration_authority"],
                "Runtime ResolvedConfig",
            )
            self.assertFalse(source["metadata"]["runtime_home_forwarded_to_child"])
            self.assertFalse(source["metadata"]["provider_credentials_forwarded_to_child"])
            self.assertFalse(source["adoption_or_canon_change"])
            classes = {item["provenance_class"] for item in source["items"]}
            self.assertIn("resident_self_description", classes)
            self.assertIn("archive_record", classes)
            self.assertTrue(
                (home / "traces" / "mcp-context-source" / "audit.jsonl").is_file()
            )


if __name__ == "__main__":
    unittest.main()
