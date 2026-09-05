from __future__ import annotations

import unittest

from vestigia.context_sources import ContextSourceItem, ContextSourceResult


class ContextSourceContractTests(unittest.TestCase):
    def test_optional_source_cannot_impersonate_protected_runtime_layer(self) -> None:
        with self.assertRaisesRegex(ValueError, "protected Runtime layer identity_core"):
            ContextSourceResult(
                source_name="external_fixture",
                layer_name="identity_core",
                query="fixture",
                items=(),
                budget_tokens=100,
                required=False,
                authority="advisory",
            )

    def test_runtime_memory_keeps_compatibility_layer(self) -> None:
        with self.assertRaisesRegex(ValueError, "retrieved_continuity compatibility layer"):
            ContextSourceResult(
                source_name="runtime_memory",
                layer_name="other_memory_layer",
                query="fixture",
                items=(),
                budget_tokens=100,
                required=True,
                authority="runtime_memory",
                advisory=False,
            )

    def test_duplicate_item_ids_are_rejected_at_result_boundary(self) -> None:
        item = ContextSourceItem(
            item_id="same",
            text="evidence",
            provenance_class="fixture",
            authority="advisory",
        )
        with self.assertRaisesRegex(ValueError, "duplicate context source item_id"):
            ContextSourceResult(
                source_name="external_fixture",
                layer_name="external_fixture_context",
                query="fixture",
                items=(item, item),
                budget_tokens=100,
                required=False,
                authority="advisory",
            )


if __name__ == "__main__":
    unittest.main()
