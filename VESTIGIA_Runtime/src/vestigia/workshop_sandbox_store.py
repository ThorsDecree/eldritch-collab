from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .utils import new_id
from .workshop_sandbox_backend import backend_descriptor


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workshop_sandbox_executions (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    script_version INTEGER NOT NULL,
    script_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    backend_version TEXT NOT NULL,
    profile TEXT NOT NULL,
    guarantees_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    exit_code INTEGER,
    timed_out INTEGER NOT NULL DEFAULT 0,
    output_limited INTEGER NOT NULL DEFAULT 0,
    wall_ms INTEGER NOT NULL DEFAULT 0,
    error_category TEXT,
    safe_message TEXT,
    execution_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_workshop_sandbox_executions_resident
ON workshop_sandbox_executions(resident_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workshop_artifacts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    privacy TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    FOREIGN KEY (execution_id) REFERENCES workshop_sandbox_executions(id)
);

CREATE INDEX IF NOT EXISTS idx_workshop_artifacts_execution
ON workshop_artifacts(execution_id, created_at);

CREATE TABLE IF NOT EXISTS workshop_receipts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    execution_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workshop_receipts_resident
ON workshop_receipts(resident_id, created_at DESC);
"""


def ensure_schema(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(_SCHEMA)
    (house.home / "workshop" / "tmp").mkdir(parents=True, exist_ok=True)
    (house.home / "workshop" / "artifacts").mkdir(parents=True, exist_ok=True)


def _artifact_storage(house: Any, artifact_id: str, suffix: str) -> tuple[Path, str]:
    relative = Path("workshop") / "artifacts" / f"{artifact_id}{suffix}"
    absolute = house.home / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return absolute, relative.as_posix()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _cleanup_staged_artifacts(items: list[dict[str, Any]]) -> None:
    for item in items:
        try:
            Path(str(item["_absolute_path"])).unlink(missing_ok=True)
        except OSError:
            pass


def _stage_artifact(
    house: Any,
    *,
    kind: str,
    media_type: str,
    data: bytes,
    suffix: str,
    now: str,
) -> dict[str, Any]:
    """Write private bytes first; database adoption happens with the execution row."""

    artifact_id = new_id("workshop_artifact")
    path, relative = _artifact_storage(house, artifact_id, suffix)
    _atomic_write_bytes(path, data)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "object_id": artifact_id,
        "content_hash": digest,
        "privacy": "private",
        "media_type": media_type,
        "size_bytes": len(data),
        "kind": kind,
        "storage_path": relative,
        "created_at": now,
        "_absolute_path": str(path),
    }


def _public_artifact_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": item["object_id"],
        "content_hash": item["content_hash"],
        "privacy": "private",
        "media_type": item["media_type"],
        "size_bytes": item["size_bytes"],
        "kind": item["kind"],
    }


def _list_executions(house: Any, limit: int) -> list[dict[str, Any]]:
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, script_id, script_version, script_hash, plan_hash, status,
                   backend_id, backend_version, profile, wall_ms, error_category,
                   created_at, completed_at
            FROM workshop_sandbox_executions
            WHERE resident_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (house.resident_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _inspect_execution(house: Any, execution_id: str) -> dict[str, Any]:
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT execution_json FROM workshop_sandbox_executions
            WHERE id=? AND resident_id=?
            """,
            (execution_id, house.resident_id),
        ).fetchone()
        receipt_rows = connection.execute(
            """
            SELECT receipt_json FROM workshop_receipts
            WHERE execution_id=? AND resident_id=?
            ORDER BY created_at
            """,
            (execution_id, house.resident_id),
        ).fetchall()
        artifacts = connection.execute(
            """
            SELECT id, kind, media_type, content_hash, size_bytes, privacy, status, created_at
            FROM workshop_artifacts
            WHERE execution_id=? AND resident_id=?
            ORDER BY created_at
            """,
            (execution_id, house.resident_id),
        ).fetchall()
    if row is None:
        raise ValueError("unknown workshop execution")
    return {
        "execution": json.loads(str(row["execution_json"])),
        "receipts": [json.loads(str(item["receipt_json"])) for item in receipt_rows],
        "artifacts": [dict(item) for item in artifacts],
        "source_included": False,
        "raw_arguments_included": False,
        "outward_effect": "none",
    }


def _observatory_summary(house: Any) -> dict[str, Any]:
    ensure_schema(house)
    with house.db.connect() as connection:
        counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM workshop_sandbox_executions
                WHERE resident_id=?
                GROUP BY status
                """,
                (house.resident_id,),
            ).fetchall()
        }
        latest = connection.execute(
            """
            SELECT id, status, script_id, wall_ms, created_at
            FROM workshop_sandbox_executions
            WHERE resident_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (house.resident_id,),
        ).fetchone()
    return {
        "backend": backend_descriptor(house),
        "execution_counts": counts,
        "latest_execution": dict(latest) if latest else None,
        "pending_executions": 0,
        "outward_boundary": "none",
    }
