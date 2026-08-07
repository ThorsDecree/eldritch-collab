from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .utils import new_id, sha256_text, stable_json, utc_now_iso


_SCRIPT_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workshop_scripts (
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    source_object_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    input_schema_json TEXT NOT NULL,
    input_schema_hash TEXT NOT NULL,
    output_schema_json TEXT NOT NULL,
    output_schema_hash TEXT NOT NULL,
    requested_grant_json TEXT NOT NULL,
    requested_grant_hash TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    allowed_backends_json TEXT NOT NULL,
    minimum_profile TEXT NOT NULL,
    determinism TEXT NOT NULL,
    privacy_json TEXT NOT NULL,
    quarantine_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (resident_id, script_id, version)
);

CREATE INDEX IF NOT EXISTS idx_workshop_scripts_resident_state
ON workshop_scripts(resident_id, state, script_id, version DESC);

CREATE TABLE IF NOT EXISTS workshop_script_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    evidence_id TEXT,
    note_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_events_version
ON workshop_script_events(resident_id, script_id, version, created_at);

CREATE TABLE IF NOT EXISTS workshop_script_inspections (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    inspector_version TEXT NOT NULL,
    ruleset_hash TEXT NOT NULL,
    classification TEXT NOT NULL,
    parse_ok INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_inspections_version
ON workshop_script_inspections(resident_id, script_id, version, created_at DESC);

CREATE TABLE IF NOT EXISTS workshop_script_tests (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    inspection_id TEXT NOT NULL,
    sandbox_execution_id TEXT NOT NULL,
    status TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    backend_version TEXT NOT NULL,
    guarantees_hash TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_tests_version
ON workshop_script_tests(resident_id, script_id, version, created_at DESC);

CREATE TABLE IF NOT EXISTS workshop_script_approvals (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    inspection_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    grant_json TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_approvals_version
ON workshop_script_approvals(resident_id, script_id, version, created_at DESC);

CREATE TABLE IF NOT EXISTS workshop_script_activations (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    inspection_id TEXT NOT NULL,
    inspection_ruleset_hash TEXT NOT NULL,
    test_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    grant_hash TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    backend_version TEXT NOT NULL,
    guarantees_hash TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    max_wall_seconds INTEGER NOT NULL,
    direct_call INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_activations_version
ON workshop_script_activations(resident_id, script_id, version, status, created_at DESC);

CREATE TABLE IF NOT EXISTS workshop_script_runs (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    sandbox_execution_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    authorship TEXT NOT NULL,
    privacy TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workshop_script_runs_version
ON workshop_script_runs(resident_id, script_id, version, created_at DESC);
"""


def ensure_schema(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(_SCHEMA)
    (house.home / "workshop" / "scripts").mkdir(parents=True, exist_ok=True)


def validate_script_id(value: str) -> str:
    clean = str(value or "").strip().casefold()
    if not _SCRIPT_ID_RE.fullmatch(clean) or len(clean) > 160:
        raise ValueError("script_id must be a dotted/dashed/underscored lowercase stable ID")
    return clean


def source_limit(house: Any) -> int:
    try:
        configured = int(house.config.get("workshop.max_script_source_bytes", 131072))
    except (TypeError, ValueError):
        configured = 131072
    return max(4096, min(configured, 1048576))


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_source(house: Any, source: str) -> dict[str, Any]:
    ensure_schema(house)
    data = source.encode("utf-8")
    if not data:
        raise ValueError("script source cannot be empty")
    if len(data) > source_limit(house):
        raise ValueError("script source exceeded the configured byte ceiling")
    digest = hashlib.sha256(data).hexdigest()
    relative = Path("workshop") / "scripts" / f"{digest}.py"
    path = house.home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != digest or existing != data:
            raise RuntimeError("content-addressed script source integrity mismatch")
    else:
        _atomic_write(path, data)
    return {
        "object_id": f"workshop_source_{digest}",
        "sha256": digest,
        "size_bytes": len(data),
        "media_type": "text/x-python",
        "storage_path": relative.as_posix(),
    }


def read_source(house: Any, row: Any) -> str:
    relative = Path(str(row["source_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("stored script source path is invalid")
    path = (house.home / relative).resolve()
    root = (house.home / "workshop" / "scripts").resolve()
    if root not in path.parents:
        raise RuntimeError("stored script source escaped the script shelf")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(row["source_hash"]):
        raise RuntimeError("stored script source hash no longer matches its immutable record")
    return data.decode("utf-8")


def get_script(house: Any, script_id: str, version: int | None = None, *, active_only: bool = False) -> Any:
    ensure_schema(house)
    script_id = validate_script_id(script_id)
    with house.db.connect() as connection:
        if version is not None:
            row = connection.execute(
                "SELECT * FROM workshop_scripts WHERE resident_id=? AND script_id=? AND version=?",
                (house.resident_id, script_id, int(version)),
            ).fetchone()
        elif active_only:
            row = connection.execute(
                """
                SELECT * FROM workshop_scripts
                WHERE resident_id=? AND script_id=? AND state='active'
                ORDER BY version DESC LIMIT 1
                """,
                (house.resident_id, script_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM workshop_scripts
                WHERE resident_id=? AND script_id=?
                ORDER BY version DESC LIMIT 1
                """,
                (house.resident_id, script_id),
            ).fetchone()
    if row is None:
        raise ValueError("unknown resident script version")
    return row


def next_version(house: Any, script_id: str) -> int:
    ensure_schema(house)
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT MAX(version) AS value FROM workshop_scripts WHERE resident_id=? AND script_id=?",
            (house.resident_id, script_id),
        ).fetchone()
    return int(row["value"] or 0) + 1


def record_event(
    house: Any,
    *,
    script_id: str,
    version: int,
    event_type: str,
    from_state: str | None,
    to_state: str | None,
    evidence_id: str | None = None,
    note: str | None = None,
    connection: Any | None = None,
) -> str:
    event_id = new_id("script_event")
    note_hash = sha256_text(note) if note else None
    own = connection is None
    if own:
        connection = house.db.connect()
    try:
        connection.execute(
            """
            INSERT INTO workshop_script_events
            (id, resident_id, script_id, version, event_type, from_state, to_state,
             evidence_id, note_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                house.resident_id,
                script_id,
                version,
                event_type,
                from_state,
                to_state,
                evidence_id,
                note_hash,
                utc_now_iso(),
            ),
        )
    finally:
        if own:
            connection.close()
    return event_id


def set_state(
    house: Any,
    row: Any,
    state: str,
    *,
    event_type: str,
    evidence_id: str | None = None,
    reason: str | None = None,
) -> None:
    previous = str(row["state"])
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE workshop_scripts SET state=?, quarantine_reason=?, updated_at=?
            WHERE resident_id=? AND script_id=? AND version=?
            """,
            (
                state,
                reason if state == "quarantined" else None,
                now,
                house.resident_id,
                row["script_id"],
                row["version"],
            ),
        )
        record_event(
            house,
            script_id=str(row["script_id"]),
            version=int(row["version"]),
            event_type=event_type,
            from_state=previous,
            to_state=state,
            evidence_id=evidence_id,
            note=reason,
            connection=connection,
        )


def list_scripts(house: Any, limit: int) -> list[dict[str, Any]]:
    ensure_schema(house)
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT script_id, version, state, name, source_hash, source_size,
                   minimum_profile, determinism, quarantine_reason, created_at, updated_at
            FROM workshop_scripts WHERE resident_id=?
            ORDER BY updated_at DESC, script_id, version DESC LIMIT ?
            """,
            (house.resident_id, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def latest_evidence(house: Any, table: str, script_id: str, version: int) -> Any | None:
    allowed = {
        "workshop_script_inspections",
        "workshop_script_tests",
        "workshop_script_approvals",
        "workshop_script_activations",
    }
    if table not in allowed:
        raise ValueError("unsupported workshop evidence table")
    with house.db.connect() as connection:
        return connection.execute(
            f"""
            SELECT * FROM {table}
            WHERE resident_id=? AND script_id=? AND version=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (house.resident_id, script_id, int(version)),
        ).fetchone()


def mark_activations(house: Any, script_id: str, version: int, status: str) -> None:
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            """
            UPDATE workshop_script_activations SET status=?, updated_at=?
            WHERE resident_id=? AND script_id=? AND version=? AND status='active'
            """,
            (status, now, house.resident_id, script_id, int(version)),
        )


def json_field(row: Any, name: str) -> Any:
    return json.loads(str(row[name]))


def script_identity(row: Any) -> dict[str, Any]:
    return {
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "source_hash": str(row["source_hash"]),
        "input_schema_hash": str(row["input_schema_hash"]),
        "output_schema_hash": str(row["output_schema_hash"]),
        "requested_grant_hash": str(row["requested_grant_hash"]),
        "minimum_profile": str(row["minimum_profile"]),
        "environment_id": str(row["environment_id"]),
    }


def observatory_summary(house: Any) -> dict[str, Any]:
    ensure_schema(house)
    with house.db.connect() as connection:
        counts = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM workshop_scripts WHERE resident_id=? GROUP BY state",
                (house.resident_id,),
            ).fetchall()
        }
        active = connection.execute(
            """
            SELECT script_id, version, source_hash, updated_at
            FROM workshop_scripts WHERE resident_id=? AND state='active'
            ORDER BY updated_at DESC LIMIT 10
            """,
            (house.resident_id,),
        ).fetchall()
        latest = connection.execute(
            """
            SELECT script_id, version, event_type, from_state, to_state, evidence_id, created_at
            FROM workshop_script_events WHERE resident_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (house.resident_id,),
        ).fetchone()
    return {
        "state_counts": counts,
        "active_scripts": [dict(row) for row in active],
        "latest_event": dict(latest) if latest else None,
        "source_included": False,
        "import_execution_allowed": False,
    }
