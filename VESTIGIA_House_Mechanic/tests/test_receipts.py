from pathlib import Path

from house_mechanic.health import HealthResult
from house_mechanic.receipts import ReceiptStore
from house_mechanic.runner import RunReceipt


def _run_receipt() -> RunReceipt:
    return RunReceipt(
        request_id="req-run",
        recipe_id="runtime.test",
        recipe_sha256="a" * 64,
        cwd="VESTIGIA_Runtime",
        env_profile="python",
        source_commit="b" * 40,
        source_branch="feature/test",
        source_dirty=True,
        source_status_sha256="c" * 64,
        process_id=123,
        started_at="2026-09-21T00:00:00+00:00",
        completed_at="2026-09-21T00:00:01+00:00",
        wall_seconds=1.0,
        exit_code=1,
        expected_exit=False,
        timed_out=False,
        output_limit_exceeded=False,
        stdout="x" * 5000,
        stderr="failure detail",
        stdout_bytes=5000,
        stderr_bytes=len("failure detail"),
        stdout_sha256="d" * 64,
        stderr_sha256="e" * 64,
        stdout_truncated=False,
        stderr_truncated=False,
        process_tree_termination_attempted=False,
        process_tree_containment_proven=False,
    )


def test_receipt_store_persists_bounded_run_evidence(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path / "receipts.jsonl")
    record = store.append_run(_run_receipt())

    assert record["kind"] == "recipe_run"
    evidence = record["evidence"]
    assert evidence["recipe_id"] == "runtime.test"
    assert evidence["raw_full_output_persisted"] is False
    assert evidence["stdout_excerpt_truncated"] is True
    assert len(evidence["stdout_excerpt"].encode("utf-8")) <= 4096

    recent = store.recent(limit=5)
    assert recent[0]["receipt_id"] == record["receipt_id"]
    assert store.get(record["receipt_id"]) == record


def test_receipt_store_persists_health_evidence(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path / "receipts.jsonl")
    result = HealthResult(
        request_id="req-health",
        service_id="daemon-bridge",
        service_sha256="f" * 64,
        checked_at="2026-09-21T00:00:00+00:00",
        healthy=True,
        reachable=True,
        expected_status=200,
        observed_status=200,
        expected_protocol="daemon-bridge-api.v0.1",
        observed_protocol="daemon-bridge-api.v0.1",
        latency_ms=2.5,
        response_bytes_captured=32,
        response_truncated=False,
        captured_response_sha256="1" * 64,
        error_type=None,
    )
    record = store.append_health(result)
    assert record["kind"] == "service_health"
    assert store.recent(limit=5, kind="service_health")[0]["receipt_id"] == record["receipt_id"]
