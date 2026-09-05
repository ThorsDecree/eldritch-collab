from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .composition import register_capability_installer


def _install(house: Any) -> None:
    previous = house.registry.handler("retrieval.inspect")

    def inspect_with_sources(
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        result = previous(payload, context)
        turn_id = str(result.get("turn_id") or "").strip()
        if not turn_id:
            return result
        receipt_path = Path(house.home) / "traces" / f"{turn_id}.receipt.json"
        if not receipt_path.is_file() or receipt_path.is_symlink():
            return result
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                **result,
                "schema_version": "vestigia.retrieval-inspector.v0.7",
                "context_sources": [],
                "source_receipt_available": False,
                "source_receipt_warning": "context receipt could not be decoded",
            }

        raw_sources = receipt.get("context_sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        source_queries: list[dict[str, Any]] = []
        available = 0
        unavailable = 0
        truncated = 0
        unknown_truncation = 0
        advisory = 0
        required = 0
        for source in sources:
            if not isinstance(source, dict):
                continue
            is_available = bool(source.get("available", False))
            available += int(is_available)
            unavailable += int(not is_available)
            required += int(bool(source.get("required", False)))
            advisory += int(bool(source.get("advisory", False)))
            truncation = source.get("truncated")
            truncated += int(truncation is True)
            unknown_truncation += int(truncation is None)
            source_queries.append(
                {
                    "name": source.get("name"),
                    "layer": source.get("layer"),
                    "query": source.get("query"),
                    "available": is_available,
                    "required": bool(source.get("required", False)),
                    "authority": source.get("authority"),
                    "advisory": bool(source.get("advisory", False)),
                    "truncated": truncation,
                    "truncation_reason": source.get("truncation_reason"),
                    "warnings": source.get("warnings", []),
                    "item_count": source.get("item_count", 0),
                    "included_item_ids": source.get("included_item_ids", []),
                    "omitted_item_ids": source.get("omitted_item_ids", []),
                    "post_total_cap_item_boundary_unknown": source.get(
                        "post_total_cap_item_boundary_unknown",
                        False,
                    ),
                }
            )

        return {
            **result,
            "schema_version": "vestigia.retrieval-inspector.v0.7",
            "context_receipt_schema": receipt.get("schema_version"),
            "context_sources": sources,
            "source_queries": source_queries,
            "source_summary": {
                "count": len(source_queries),
                "available": available,
                "unavailable": unavailable,
                "required": required,
                "advisory": advisory,
                "truncated": truncated,
                "truncation_unknown": unknown_truncation,
            },
            "source_receipt_available": True,
            "not_automatically_searched": [
                "Archive content outside configured source/query routes",
                "private image pixels unless an image capability explicitly inspects them",
                "sealed records",
                "inherited-unreviewed Runtime memories outside ORIENTATION",
            ],
            "context_boundary": (
                "A source item being included in a prompt is evidence of prompt inclusion only. "
                "It is not automatic Runtime memory, resident adoption, canon, or proof of "
                "model attention/causal use."
            ),
        }

    house.registry.replace_handler("retrieval.inspect", inspect_with_sources)


def register_composition() -> None:
    register_capability_installer(
        "context_source_introspection",
        _install,
        order=260,
    )
