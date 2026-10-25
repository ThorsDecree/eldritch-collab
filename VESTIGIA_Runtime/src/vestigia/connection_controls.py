from __future__ import annotations

import json

from .db import ContinuityDB
from .utils import stable_json, utc_now_iso


CONNECTION_CONTROLS_KIND = "connection_controls"


def load_disabled_context_sources(db: ContinuityDB, resident_id: str) -> set[str]:
    with db.connect() as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_jobs'"
        ).fetchone()
        row = (
            connection.execute(
                "SELECT config_json FROM resident_jobs WHERE resident_id=? AND kind=?",
                (resident_id, CONNECTION_CONTROLS_KIND),
            ).fetchone()
            if table
            else None
        )
    if row is None:
        return set()
    stored = json.loads(str(row["config_json"]) or "{}")
    if not isinstance(stored, dict):
        raise ValueError("saved connection controls must be an object")
    disabled = stored.get("disabled_context_sources", [])
    if not isinstance(disabled, list) or any(not isinstance(item, str) for item in disabled):
        raise ValueError("saved disabled context sources must be a list of names")
    return {item.strip().lower() for item in disabled if item.strip()}


def set_context_source_enabled(
    db: ContinuityDB,
    resident_id: str,
    source_name: str,
    enabled: bool,
) -> None:
    disabled = load_disabled_context_sources(db, resident_id)
    normalized = source_name.strip().lower()
    if enabled:
        disabled.discard(normalized)
    else:
        disabled.add(normalized)
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO resident_jobs
            (id, resident_id, kind, status, config_json, updated_at)
            VALUES ('connection-controls:' || ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(resident_id, kind) DO UPDATE SET
              status='active', config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (
                resident_id,
                resident_id,
                CONNECTION_CONTROLS_KIND,
                stable_json({"disabled_context_sources": sorted(disabled)}),
                utc_now_iso(),
            ),
        )
