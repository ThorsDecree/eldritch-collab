from __future__ import annotations

import json
from typing import Any

from .config import ResolvedConfig
from .db import ContinuityDB
from .utils import stable_json, utc_now_iso


VISIBILITY_MODES = {"allowlisted_only", "all_channel", "mentions_only", "hidden"}


def default_context_controls(config: ResolvedConfig) -> dict[str, Any]:
    return {
        "prompt_budget_tokens": int(config.get("context.total_tokens", 20_000)),
        "verbatim_turns": int(config.get("context.verbatim_turns", 12)),
        "compression_source_turns": int(
            config.get("context.compression_source_turns", 60)
        ),
        "compressed_token_budget": int(
            config.get("context.compressed_transcript_tokens", 3_500)
        ),
        "ambient_visibility": str(
            config.get("discord.ambient_visibility", "allowlisted_only")
        ),
    }


def load_context_controls(
    config: ResolvedConfig,
    db: ContinuityDB,
    resident_id: str,
) -> dict[str, Any]:
    controls = default_context_controls(config)
    try:
        with db.connect() as connection:
            row = connection.execute(
                """
                SELECT config_json FROM resident_jobs
                WHERE resident_id=? AND kind='context_controls'
                """,
                (resident_id,),
            ).fetchone()
    except Exception:
        return controls
    if row:
        stored = json.loads(str(row["config_json"]) or "{}")
        if isinstance(stored, dict):
            controls.update(stored)
    return controls


def save_context_controls(
    db: ContinuityDB,
    resident_id: str,
    controls: dict[str, Any],
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO resident_jobs
            (id, resident_id, kind, status, config_json, updated_at)
            VALUES ('context-controls:' || ?, ?, 'context_controls', 'active', ?, ?)
            ON CONFLICT(resident_id, kind) DO UPDATE SET
              status='active', config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (resident_id, resident_id, stable_json(controls), utc_now_iso()),
        )
