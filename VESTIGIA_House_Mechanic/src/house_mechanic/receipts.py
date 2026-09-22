from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from .health import HealthResult
from .runner import RunReceipt


RECEIPT_SCHEMA_VERSION = "vestigia.house-mechanic-receipt.v0.1"
DEFAULT_EXCERPT_BYTES = 4096


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tail_excerpt(value: str, limit: int = DEFAULT_EXCERPT_BYTES) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    return raw[-limit:].decode("utf-8", errors="replace")


class ReceiptStore:
    """Append-only local evidence store for House Mechanic actions."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.path.open("a", encoding="utf-8"):
            pass

    def _append(
        self,
        *,
        kind: str,
        request_id: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "receipt_id": f"hm_receipt_{uuid.uuid4().hex}",
            "kind": kind,
            "request_id": request_id,
            "created_at": datetime.now(UTC).isoformat(),
            "evidence": evidence,
        }
        line = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def append_run(self, receipt: RunReceipt) -> dict[str, Any]:
        stdout = receipt.stdout
        stderr = receipt.stderr
        return self._append(
            kind="recipe_run",
            request_id=receipt.request_id,
            evidence={
                "recipe_id": receipt.recipe_id,
                "recipe_sha256": receipt.recipe_sha256,
                "cwd": receipt.cwd,
                "env_profile": receipt.env_profile,
                "source_commit": receipt.source_commit,
                "source_branch": receipt.source_branch,
                "source_dirty": receipt.source_dirty,
                "source_status_sha256": receipt.source_status_sha256,
                "process_id": receipt.process_id,
                "started_at": receipt.started_at,
                "completed_at": receipt.completed_at,
                "wall_seconds": receipt.wall_seconds,
                "exit_code": receipt.exit_code,
                "expected_exit": receipt.expected_exit,
                "timed_out": receipt.timed_out,
                "output_limit_exceeded": receipt.output_limit_exceeded,
                "stdout_bytes": receipt.stdout_bytes,
                "stderr_bytes": receipt.stderr_bytes,
                "stdout_sha256": receipt.stdout_sha256,
                "stderr_sha256": receipt.stderr_sha256,
                "stdout_excerpt": _tail_excerpt(stdout),
                "stderr_excerpt": _tail_excerpt(stderr),
                "stdout_excerpt_truncated": len(stdout.encode("utf-8")) > DEFAULT_EXCERPT_BYTES,
                "stderr_excerpt_truncated": len(stderr.encode("utf-8")) > DEFAULT_EXCERPT_BYTES,
                "stdout_truncated": receipt.stdout_truncated,
                "stderr_truncated": receipt.stderr_truncated,
                "process_tree_termination_attempted": (
                    receipt.process_tree_termination_attempted
                ),
                "process_tree_containment_proven": (
                    receipt.process_tree_containment_proven
                ),
                "raw_full_output_persisted": False,
            },
        )

    def append_health(self, result: HealthResult) -> dict[str, Any]:
        return self._append(
            kind="service_health",
            request_id=result.request_id,
            evidence=result.to_dict(),
        )

    def recent(
        self,
        *,
        limit: int = 50,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        if not self.path.is_file():
            return []
        rows: deque[dict[str, Any]] = deque(maxlen=bounded)
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if kind is not None and item.get("kind") != kind:
                        continue
                    rows.append(item)
        return list(reversed(rows))

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        wanted = receipt_id.strip()
        if not wanted or not self.path.is_file():
            return None
        found: dict[str, Any] | None = None
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("receipt_id") == wanted:
                        found = item
        return found
