from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ReceiptGardenError(ValueError):
    pass


_EDGE_TYPES = {
    "observed",
    "authorized",
    "queried",
    "included",
    "omitted",
    "dispatched",
    "stored",
    "returned",
}
_FORBIDDEN_KEYS = {
    "raw_html",
    "cookies",
    "credentials",
    "browser_history",
    "hidden_page_state",
    "raw_payload",
    "body",
}
_MAX_RECORD_BYTES = 64_000
_MAX_EDGE_COUNT = 32


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ReceiptGardenError("Receipt detail nesting is too deep")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 2_000:
            raise ReceiptGardenError("Receipt detail string is too long")
        return value
    if isinstance(value, list):
        if len(value) > 64:
            raise ReceiptGardenError("Receipt detail list is too long")
        return [_bounded_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            normalized = str(key)
            if normalized.lower() in _FORBIDDEN_KEYS:
                raise ReceiptGardenError(f"Receipt detail contains prohibited key: {normalized}")
            result[normalized] = _bounded_value(value[key], depth=depth + 1)
        return result
    raise ReceiptGardenError(f"Receipt detail type is not supported: {type(value).__name__}")


class ReceiptGarden:
    """MCP-owned append-only provenance records, separate from Archive memory."""

    def __init__(self, state_dir: Path, deployment_id: str):
        self._state_dir = state_dir
        self._deployment_id = deployment_id
        self._path = state_dir / "receipt-garden.jsonl"
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        request_id: str | None,
        operation: str,
        edges: list[dict[str, Any]],
        omitted: tuple[str, ...] = (),
        safe_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not operation.strip():
            raise ReceiptGardenError("Receipt operation must not be blank")
        if len(edges) > _MAX_EDGE_COUNT:
            raise ReceiptGardenError("Receipt edge count exceeds the bound")
        safe_edges: list[dict[str, Any]] = []
        for edge in edges:
            if not isinstance(edge, dict):
                raise ReceiptGardenError("Receipt edges must be objects")
            edge_type = str(edge.get("type") or "").strip()
            if edge_type not in _EDGE_TYPES:
                raise ReceiptGardenError(f"Unknown receipt edge type: {edge_type}")
            safe_edges.append(_bounded_value(edge))
        safe_omitted = sorted({str(item).strip() for item in omitted if str(item).strip()})
        record: dict[str, Any] = {
            "schema_version": "vestigia.receipt-garden.v0.1",
            "receipt_id": str(uuid.uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "deployment_id": self._deployment_id,
            "request_id": request_id,
            "operation": operation.strip(),
            "edges": safe_edges,
            "omitted": safe_omitted,
            "safe_summary": _bounded_value(safe_summary or {}),
            "causal_influence": "unknown",
            "receipt_is_memory": False,
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(line.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise ReceiptGardenError("Receipt record exceeds the bound")
        with self._lock:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
        return record

    def record_audit_event(self, event: Any) -> dict[str, Any]:
        capability = str(event.capability)
        return self.append(
            request_id=event.request_id,
            operation=capability,
            edges=[
                {
                    "type": "authorized",
                    "source": "mcp_policy",
                    "target": capability,
                    "evidence_scope": event.decision,
                },
                {
                    "type": "queried",
                    "source": "mcp_request",
                    "target": capability,
                    "evidence_scope": "argument_digest_only",
                },
                {
                    "type": "returned",
                    "source": capability,
                    "target": "mcp_caller",
                    "evidence_scope": event.outcome,
                },
            ],
            omitted=("raw_arguments", "raw_payload"),
            safe_summary={
                "event_id": event.event_id,
                "arguments_sha256": event.arguments_sha256,
                "effect": event.effect,
                "outcome": event.outcome,
                "authority": event.authority,
                "detail": event.detail,
            },
        )

    def _read(self) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        malformed = 0
        with self._lock:
            if not self._path.exists():
                return records, malformed
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        malformed += 1
                        continue
                    if isinstance(record, dict):
                        records.append(record)
                    else:
                        malformed += 1
        return records, malformed

    def recent(self, *, limit: int = 25, request_id: str | None = None) -> dict[str, object]:
        if limit <= 0 or limit > 200:
            raise ReceiptGardenError("Receipt Garden limit must be between 1 and 200")
        wanted = request_id.strip() if request_id else None
        if request_id is not None and not wanted:
            raise ReceiptGardenError("Receipt Garden request ID must not be blank")
        records, malformed = self._read()
        matching = [record for record in records if not wanted or record.get("request_id") == wanted]
        return {
            "receipts": list(reversed(matching[-limit:])),
            "matched_total": len(matching),
            "malformed_lines": malformed,
            "request_id": wanted,
            "causal_influence": "unknown",
        }

    def trace(self, request_id: str) -> dict[str, object]:
        wanted = request_id.strip()
        if not wanted:
            raise ReceiptGardenError("Receipt Garden request ID must not be blank")
        result = self.recent(limit=200, request_id=wanted)
        result["trace_is_joined_provenance"] = True
        result["included_does_not_mean_caused"] = True
        return result
