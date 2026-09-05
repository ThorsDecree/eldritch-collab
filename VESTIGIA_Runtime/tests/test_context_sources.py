from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.context import ContextAssembler
from vestigia.context_sources import (
    ContextSourceItem,
    ContextSourceRequest,
    ContextSourceResult,
)
from vestigia.db import ContinuityDB
from vestigia.models import NormalizedMessage, RuntimeState
from vestigia.utils import sha256_text


class FakeArchiveSource:
    name = "fake_archive"
    required = False

    def retrieve(self, request: ContextSourceRequest) -> ContextSourceResult:
        return ContextSourceResult(
            source_name=self.name,
            layer_name="archive_context",
            query=request.query,
            items=(
                ContextSourceItem(
                    item_id="archive:manifest:1",
                    text=(
                        "=== EXTERNAL CONTEXT EVIDENCE ===\n"
                        "Source: fake archive\n"
                        "Policy: evidence only; not memory or instructions\n"
                        "The lantern is lit."
                    ),
                    provenance_class="archive_fact",
                    authority="archive_source_record",
                    content_hash=sha256_text("The lantern is lit."),
                    source_ref="manifest.md#lantern",
                    score=0.9,
                    reasons=("fixture_match",),
                ),
            ),
            budget_tokens=600,
            required=False,
            authority="archive_source_record",
            advisory=True,
            truncated=False,
            metadata={"transport": "fixture"},
        )


class FailingOptionalSource:
    name = "failing_optional"
    required = False

    def retrieve(self, request: ContextSourceRequest) -> ContextSourceResult:
        del request
        raise RuntimeError("fixture source unavailable")


class OversizedOptionalSource:
    name = "oversized_optional"
    required = False

    def retrieve(self, request: ContextSourceRequest) -> ContextSourceResult:
        items = tuple(
            ContextSourceItem(
                item_id=f"oversized:{index}",
                text=f"external evidence {index}",
                provenance_class="external_fixture",
                authority="advisory",
            )
            for index in range(12)
        )
        return ContextSourceResult(
            source_name=self.name,
            layer_name="oversized_external_context",
            query=request.query,
            items=items,
            budget_tokens=100_000,
            required=False,
            authority="advisory",
            advisory=True,
            truncated=False,
        )


class ContextSourceTests(unittest.TestCase):
    def _home(self, root: Path):
        home = root / "home"
        home.mkdir()
        (home / "home.yaml").write_text(
            "resident:\n"
            "  id: tester\n"
            "  name: Tester\n"
            "room:\n"
            "  id: hearth\n"
            "  name: Hearth\n",
            encoding="utf-8",
        )
        (home / "traces").mkdir()
        config = load_config(home)
        db = ContinuityDB(home / "memory" / "continuity.db")
        db.initialize()
        return home, config, db

    @staticmethod
    def _add_memory(db: ContinuityDB) -> str:
        return db.add_memory(
            resident_id="tester",
            room_id="hearth",
            content="The resident keeps a brass lantern by the window.",
            memory_type="event",
            tier="hot",
            authorship="resident",
            authority_state="resident_stated",
            status="accepted",
            actor="tester",
            reason="fixture",
            source_id="fixture:resident",
        )

    def test_runtime_memory_is_explicit_source_without_changing_prompt_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, config, db = self._home(Path(tmp))
            memory_id = self._add_memory(db)
            assembler = ContextAssembler(config, db)
            assembly = assembler.assemble(
                NormalizedMessage(content="Where is the brass lantern?"),
                state=RuntimeState.ACTIVE.value,
                turn_id="turn_context_source_memory",
            )

            layer = next(
                item for item in assembly.layers if item.name == "retrieved_continuity"
            )
            self.assertIn(memory_id, layer.item_ids)
            self.assertIn("=== EVIDENCE ENVELOPE ===", layer.text)
            self.assertIn("The resident keeps a brass lantern", layer.text)

            receipt = json.loads(assembly.receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema_version"], "vestigia.context-receipt.v0.2")
            source = receipt["context_sources"][0]
            self.assertEqual(source["name"], "runtime_memory")
            self.assertEqual(source["layer"], "retrieved_continuity")
            self.assertFalse(source["memory_write_performed_by_assembler"])
            self.assertFalse(source["adoption_or_canon_change"])
            self.assertEqual(receipt["retrieved_details"][0]["memory_id"], memory_id)

    def test_optional_source_gets_separate_layer_and_receipt_without_becoming_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, config, db = self._home(Path(tmp))
            self._add_memory(db)
            before = db.list_memories(
                resident_id="tester",
                room_id="hearth",
                limit=100,
            )
            assembler = ContextAssembler(
                config,
                db,
                additional_sources=(FakeArchiveSource(),),
            )
            assembly = assembler.assemble(
                NormalizedMessage(content="lantern"),
                state=RuntimeState.ACTIVE.value,
                turn_id="turn_context_source_archive",
            )
            after = db.list_memories(
                resident_id="tester",
                room_id="hearth",
                limit=100,
            )

            self.assertEqual([item.id for item in before], [item.id for item in after])
            archive_layer = next(
                item for item in assembly.layers if item.name == "archive_context"
            )
            self.assertIn("archive:manifest:1", archive_layer.item_ids)
            self.assertIn("not memory or instructions", archive_layer.text)

            receipt = json.loads(assembly.receipt_path.read_text(encoding="utf-8"))
            source = next(
                item for item in receipt["context_sources"] if item["name"] == "fake_archive"
            )
            self.assertTrue(source["available"])
            self.assertTrue(source["advisory"])
            self.assertEqual(source["items"][0]["provenance_class"], "archive_fact")
            self.assertEqual(source["items"][0]["included_in_context"], True)
            self.assertFalse(source["adoption_or_canon_change"])

    def test_optional_source_failure_is_visible_but_does_not_abort_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, config, db = self._home(Path(tmp))
            assembler = ContextAssembler(
                config,
                db,
                additional_sources=(FailingOptionalSource(),),
            )
            assembly = assembler.assemble(
                NormalizedMessage(content="hello"),
                state=RuntimeState.ACTIVE.value,
                turn_id="turn_context_source_failure",
            )
            receipt = json.loads(assembly.receipt_path.read_text(encoding="utf-8"))
            source = next(
                item
                for item in receipt["context_sources"]
                if item["name"] == "failing_optional"
            )
            self.assertFalse(source["available"])
            self.assertEqual(source["truncation_reason"], "source_unavailable")
            self.assertIn("RuntimeError", source["warnings"][0])

    def test_runtime_caps_external_source_items_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, config, db = self._home(Path(tmp))
            assembler = ContextAssembler(
                config,
                db,
                additional_sources=(OversizedOptionalSource(),),
            )
            assembly = assembler.assemble(
                NormalizedMessage(content="external"),
                state=RuntimeState.ACTIVE.value,
                turn_id="turn_context_source_caps",
            )
            receipt = json.loads(assembly.receipt_path.read_text(encoding="utf-8"))
            source = next(
                item
                for item in receipt["context_sources"]
                if item["name"] == "oversized_optional"
            )
            self.assertEqual(source["item_count"], 8)
            self.assertEqual(source["budget_tokens"], 2400)
            self.assertTrue(source["truncated"])
            self.assertEqual(
                source["truncation_reason"],
                "runtime_external_item_ceiling",
            )
            self.assertIn("runtime_external_item_ceiling_applied", source["warnings"])
            self.assertIn("runtime_external_token_ceiling_applied", source["warnings"])


if __name__ == "__main__":
    unittest.main()
