from __future__ import annotations

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
from vestigia.house_tools import HousePort
from vestigia.models import NormalizedMessage, RuntimeState


class InspectorFixtureSource:
    name = "inspector_fixture"
    required = False

    def retrieve(self, request: ContextSourceRequest) -> ContextSourceResult:
        return ContextSourceResult(
            source_name=self.name,
            layer_name="inspector_fixture_context",
            query=request.query,
            items=(
                ContextSourceItem(
                    item_id="fixture:item:1",
                    text="Fixture evidence offered only to this turn.",
                    provenance_class="fixture_evidence",
                    authority="advisory",
                    source_ref="fixture://one",
                ),
            ),
            budget_tokens=300,
            required=False,
            authority="advisory",
            advisory=True,
            truncated=False,
            metadata={"transport": "fixture"},
        )


class ContextIntrospectionTests(unittest.TestCase):
    def test_retrieval_inspect_projects_source_neutral_context_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
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
            config = load_config(home)
            db = ContinuityDB(home / "memory" / "continuity.db")
            db.initialize()

            # HousePort bootstraps composition and installs the wrapper around the
            # existing retrieval.inspect capability; no duplicate inspector action.
            house = HousePort(config, db)
            assembler = ContextAssembler(
                config,
                db,
                additional_sources=(InspectorFixtureSource(),),
            )
            turn_id = "turn_context_inspection"
            assembler.assemble(
                NormalizedMessage(content="show me the fixture evidence"),
                state=RuntimeState.ACTIVE.value,
                turn_id=turn_id,
            )

            result = house.dispatch(
                {
                    "action": "retrieval.inspect",
                    "turn_id": turn_id,
                    "after": "finish",
                },
                turn_id="turn_inspector_call",
                context={"interface": "test"},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["schema_version"],
                "vestigia.retrieval-inspector.v0.7",
            )
            self.assertEqual(result["context_receipt_schema"], "vestigia.context-receipt.v0.2")
            sources = {item["name"]: item for item in result["context_sources"]}
            self.assertIn("runtime_memory", sources)
            self.assertIn("inspector_fixture", sources)
            fixture = sources["inspector_fixture"]
            self.assertTrue(fixture["available"])
            self.assertTrue(fixture["advisory"])
            self.assertEqual(fixture["items"][0]["provenance_class"], "fixture_evidence")
            self.assertFalse(fixture["adoption_or_canon_change"])
            query = next(
                item
                for item in result["source_queries"]
                if item["name"] == "inspector_fixture"
            )
            self.assertEqual(query["query"], "show me the fixture evidence")
            self.assertEqual(query["included_item_ids"], ["fixture:item:1"])
            self.assertIn("not automatic Runtime memory", result["context_boundary"])


if __name__ == "__main__":
    unittest.main()
