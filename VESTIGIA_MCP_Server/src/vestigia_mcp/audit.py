from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .policy import Capability, Decision


class AuditError(RuntimeError):
    pass


def hash_arguments(arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: str
    deployment_id: str
    capability: str
    effect: str
    decision: str
    arguments_sha256: str
    outcome: str
    request_id: str | None = None
    detail: str | None = None


class AuditLedger:
    """Legible receipt log. This is not a tamper-evident signature chain."""

    def __init__(self, state_dir: Path, deployment_id: str):
        self._state_dir = state_dir
        self._deployment_id = deployment_id
        self._path = state_dir / "audit.jsonl"
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        capability: Capability,
        arguments: dict[str, Any],
        outcome: str,
        *,
        decision: Decision = Decision.ALLOW,
        request_id: str | None = None,
        detail: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            deployment_id=self._deployment_id,
            capability=capability.name,
            effect=capability.effect.value,
            decision=decision.value,
            arguments_sha256=hash_arguments(arguments),
            outcome=outcome,
            request_id=request_id,
            detail=detail,
        )
        line = json.dumps(asdict(event), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
        return event

    def recent(
        self,
        *,
        limit: int = 25,
        capability: str | None = None,
        outcome: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        """Return newest matching receipts without exposing raw tool arguments."""
        if limit <= 0 or limit > 200:
            raise AuditError("Receipt limit must be between 1 and 200")
        capability_filter = capability.strip() if capability else None
        outcome_filter = outcome.strip() if outcome else None
        request_filter = request_id.strip() if request_id else None
        if capability is not None and not capability_filter:
            raise AuditError("Capability filter must not be blank")
        if outcome is not None and not outcome_filter:
            raise AuditError("Outcome filter must not be blank")
        if request_id is not None and not request_filter:
            raise AuditError("Request ID filter must not be blank")

        matching: deque[dict[str, object]] = deque(maxlen=limit)
        matched_total = 0
        malformed_lines = 0

        with self._lock:
            if not self._path.exists():
                return {
                    "events": [],
                    "matched_total": 0,
                    "malformed_lines": 0,
                    "filters": {
                        "capability": capability_filter,
                        "outcome": outcome_filter,
                        "request_id": request_filter,
                    },
                    "excludes_current_call": True,
                }
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        malformed_lines += 1
                        continue
                    if not isinstance(event, dict):
                        malformed_lines += 1
                        continue
                    if capability_filter and event.get("capability") != capability_filter:
                        continue
                    if outcome_filter and event.get("outcome") != outcome_filter:
                        continue
                    if request_filter and event.get("request_id") != request_filter:
                        continue
                    matched_total += 1
                    matching.append(event)

        return {
            "events": list(reversed(matching)),
            "matched_total": matched_total,
            "malformed_lines": malformed_lines,
            "filters": {
                "capability": capability_filter,
                "outcome": outcome_filter,
                "request_id": request_filter,
            },
            "excludes_current_call": True,
        }

    def summary(self) -> dict[str, object]:
        """Return bounded ledger health information without returning receipt contents."""
        event_count = 0
        malformed_lines = 0
        with self._lock:
            if not self._path.exists():
                return {
                    "exists": False,
                    "event_count": 0,
                    "malformed_lines": 0,
                }
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        malformed_lines += 1
                        continue
                    if isinstance(event, dict):
                        event_count += 1
                    else:
                        malformed_lines += 1
        return {
            "exists": True,
            "event_count": event_count,
            "malformed_lines": malformed_lines,
        }
