from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .policy import Capability, Decision


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
            detail=detail,
        )
        line = json.dumps(asdict(event), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
        return event
