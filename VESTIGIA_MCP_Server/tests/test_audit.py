from pathlib import Path

from vestigia_mcp.audit import AuditLedger
from vestigia_mcp.policy import PolicyEngine


def test_recent_receipts_are_filtered_and_newest_first(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "state", "test-deployment")
    policy = PolicyEngine()
    status = policy.require_allowed("archive.status")
    read_text = policy.require_allowed("archive.read_text")

    first = ledger.record(status, {}, "ok")
    second = ledger.record(read_text, {"path": "manifest.md"}, "error")
    third = ledger.record(status, {}, "ok")

    recent = ledger.recent(limit=2)
    assert [event["event_id"] for event in recent["events"]] == [
        third.event_id,
        second.event_id,
    ]
    assert recent["matched_total"] == 3
    assert recent["excludes_current_call"] is True

    errors = ledger.recent(limit=10, outcome="error")
    assert [event["event_id"] for event in errors["events"]] == [second.event_id]

    status_only = ledger.recent(limit=10, capability="archive.status")
    assert [event["event_id"] for event in status_only["events"]] == [
        third.event_id,
        first.event_id,
    ]


def test_audit_summary_counts_valid_and_malformed_lines(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ledger = AuditLedger(state, "test-deployment")
    status = PolicyEngine().require_allowed("archive.status")
    ledger.record(status, {}, "ok")

    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    summary = ledger.summary()
    assert summary == {
        "exists": True,
        "event_count": 1,
        "malformed_lines": 1,
    }
