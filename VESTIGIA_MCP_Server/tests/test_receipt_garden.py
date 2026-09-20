import json
from pathlib import Path

import pytest

from vestigia_mcp.audit import AuditLedger
from vestigia_mcp.policy import PolicyEngine
from vestigia_mcp.receipt_garden import ReceiptGarden, ReceiptGardenError


def test_receipt_garden_keeps_typed_edges_and_omits_raw_payloads(tmp_path: Path) -> None:
    garden = ReceiptGarden(tmp_path / "state", "deployment")
    receipt = garden.append(
        request_id="req-1",
        operation="porchlight.capture",
        edges=[
            {
                "type": "observed",
                "source": "porchlight",
                "target": "page:reddit",
                "evidence_scope": "readable_text",
                "detail": "user-selected snapshot",
            },
            {
                "type": "included",
                "source": "mcp_receipt_garden",
                "target": "context:turn-1",
                "evidence_scope": "warm_receipt_reference",
            },
        ],
        omitted=("raw_html", "cookies"),
        safe_summary={"bytes": 1842, "capture_mode": "page"},
    )

    trace = garden.trace("req-1")
    assert trace["matched_total"] == 1
    record = trace["receipts"][0]
    assert {edge["type"] for edge in record["edges"]} == {"observed", "included"}
    assert record["omitted"] == ["cookies", "raw_html"]
    assert record["causal_influence"] == "unknown"
    assert "do not copy" not in json.dumps(record)
    assert record["safe_summary"] == {"bytes": 1842, "capture_mode": "page"}
    assert receipt["receipt_id"] == record["receipt_id"]


def test_receipt_garden_can_join_audit_event_without_arguments(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ledger = AuditLedger(state, "deployment")
    garden = ReceiptGarden(state, "deployment")
    event = ledger.record(
        PolicyEngine().require_allowed("archive.read_text"),
        {"path": "private.md", "content": "do not copy"},
        "ok",
        request_id="req-2",
    )

    garden.record_audit_event(event)
    raw = garden.path.read_text(encoding="utf-8")
    assert "private.md" not in raw
    assert "do not copy" not in raw
    trace = garden.trace("req-2")
    assert trace["receipts"][0]["request_id"] == "req-2"
    assert trace["receipts"][0]["safe_summary"]["arguments_sha256"] == event.arguments_sha256


def test_receipt_garden_reopens_and_rejects_unbounded_records(tmp_path: Path) -> None:
    state = tmp_path / "state"
    garden = ReceiptGarden(state, "deployment")
    garden.append(
        request_id="req-3",
        operation="test",
        edges=[],
        safe_summary={"ok": True},
    )
    reopened = ReceiptGarden(state, "deployment")
    assert reopened.recent(limit=10)["matched_total"] == 1
    with pytest.raises(ReceiptGardenError):
        reopened.append(
            request_id="req-4",
            operation="test",
            edges=[],
            safe_summary={"too_big": "x" * 70_000},
        )


def test_trace_filters_are_bounded(tmp_path: Path) -> None:
    garden = ReceiptGarden(tmp_path / "state", "deployment")
    with pytest.raises(ReceiptGardenError):
        garden.recent(limit=0)
