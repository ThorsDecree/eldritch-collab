from __future__ import annotations

import json
from typing import Any

from .attention_types import _string_list, defaults, operator_settings
from .utils import stable_json, utc_now_iso

_ROUTER_JOB_KIND = "attention_router_controls"

def _ensure_job_store(db: Any) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resident_jobs (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(resident_id, kind)
            )
            """
        )


def requested(config: Any, db: Any, resident_id: str) -> dict[str, Any]:
    _ensure_job_store(db)
    result = defaults(config)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT config_json FROM resident_jobs WHERE resident_id=? AND kind=?",
            (resident_id, _ROUTER_JOB_KIND),
        ).fetchone()
    if row:
        try:
            stored = json.loads(str(row["config_json"]) or "{}")
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict):
            result.update(stored)
    for field in ("hard_wake_terms", "soft_signal_terms", "suppress_terms"):
        result[field] = _string_list(result.get(field, []))
    return result


def _bounded_terms(values: list[str], limits: dict[str, Any]) -> list[str]:
    maximum = int(limits["max_terms"])
    length = int(limits["max_term_length"])
    return [item for item in values if len(item) <= length][:maximum]


def effective(
    config: Any,
    values: dict[str, Any],
    *,
    listening_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = operator_settings(config)
    hard = _bounded_terms(_string_list(values.get("hard_wake_terms", [])), limits)
    soft = _bounded_terms(_string_list(values.get("soft_signal_terms", [])), limits)
    suppress = _bounded_terms(_string_list(values.get("suppress_terms", [])), limits)

    if bool(values.get("include_resident_name", True)):
        name = str(config.get("resident.name", "Resident")).strip()
        if name:
            hard = _string_list([*hard, name])
    controls = listening_controls or {}
    if bool(values.get("include_listening_aliases", True)):
        hard = _string_list([*hard, *controls.get("listening_aliases", [])])
    if bool(values.get("include_watch_phrases", True)):
        soft = _string_list([*soft, *controls.get("listening_watch_phrases", [])])

    hard = _bounded_terms(hard, limits)
    soft = _bounded_terms(soft, limits)
    suppress = _bounded_terms(suppress, limits)
    queue_threshold = max(-20, min(int(values.get("queue_threshold", 1)), 20))
    semantic_threshold = max(
        queue_threshold,
        min(int(values.get("semantic_threshold", 2)), 40),
    )
    return {
        **limits,
        "hard_wake_terms": hard,
        "soft_signal_terms": soft,
        "suppress_terms": suppress,
        "include_resident_name": bool(values.get("include_resident_name", True)),
        "include_listening_aliases": bool(
            values.get("include_listening_aliases", True)
        ),
        "include_watch_phrases": bool(values.get("include_watch_phrases", True)),
        "queue_threshold": queue_threshold,
        "semantic_threshold": semantic_threshold,
    }


def report(
    config: Any,
    db: Any,
    resident_id: str,
    *,
    listening_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wanted = requested(config, db, resident_id)
    return {
        "requested": wanted,
        "operator_limits": operator_settings(config),
        "effective": effective(
            config, wanted, listening_controls=listening_controls
        ),
    }


def save(db: Any, resident_id: str, values: dict[str, Any]) -> None:
    _ensure_job_store(db)
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO resident_jobs
            (id, resident_id, kind, status, config_json, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)
            ON CONFLICT(resident_id, kind) DO UPDATE SET
              status='active', config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (
                f"attention-router:{resident_id}",
                resident_id,
                _ROUTER_JOB_KIND,
                stable_json(values),
                now,
            ),
        )


def configure(
    config: Any,
    db: Any,
    resident_id: str,
    payload: dict[str, Any],
    *,
    listening_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = str(payload.get("mode") or "inspect").strip().lower()
    if mode == "inspect":
        return report(
            config, db, resident_id, listening_controls=listening_controls
        )
    if mode == "reset":
        save(db, resident_id, defaults(config))
        return report(
            config, db, resident_id, listening_controls=listening_controls
        )
    if mode != "configure":
        raise ValueError("attention router controls accept inspect, configure, or reset")

    values = requested(config, db, resident_id)
    limits = operator_settings(config)
    for field in ("hard_wake_terms", "soft_signal_terms", "suppress_terms"):
        if field not in payload:
            continue
        terms = _string_list(payload[field])
        if len(terms) > int(limits["max_terms"]):
            raise ValueError(f"{field} exceeds the operator maximum")
        if any(len(item) > int(limits["max_term_length"]) for item in terms):
            raise ValueError(f"{field} contains a term that is too long")
        values[field] = terms
    for field in (
        "include_resident_name",
        "include_listening_aliases",
        "include_watch_phrases",
    ):
        if field in payload:
            values[field] = bool(payload[field])
    for field in ("queue_threshold", "semantic_threshold"):
        if field in payload:
            values[field] = int(payload[field])
    if int(values.get("semantic_threshold", 2)) < int(
        values.get("queue_threshold", 1)
    ):
        raise ValueError("semantic_threshold must be >= queue_threshold")
    save(db, resident_id, values)
    return report(config, db, resident_id, listening_controls=listening_controls)
