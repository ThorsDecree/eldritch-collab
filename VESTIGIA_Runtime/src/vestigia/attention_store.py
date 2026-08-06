from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .attention_semantic import _estimate_tokens, _usage_value, _validate_semantic, evaluate_semantics
from .attention_types import LexicalDecision, SEMANTIC_ROUTES, SemanticEvaluator, operator_settings
from .utils import new_id, sha256_text, stable_json, utc_now_iso

def ensure_schema(db: Any) -> None:
    with db.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS attention_router_events (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                listening_event_id TEXT,
                interface TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                author_trust TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                lexical_route TEXT NOT NULL,
                lexical_score INTEGER NOT NULL,
                lexical_reasons_json TEXT NOT NULL DEFAULT '[]',
                matched_term_hashes_json TEXT NOT NULL DEFAULT '[]',
                semantic_requested INTEGER NOT NULL DEFAULT 0,
                semantic_status TEXT NOT NULL DEFAULT 'not_needed',
                semantic_route TEXT,
                semantic_confidence REAL,
                semantic_addressed INTEGER,
                semantic_relevance TEXT,
                semantic_reason_code TEXT,
                model TEXT,
                estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
                actual_input_tokens INTEGER,
                actual_output_tokens INTEGER,
                effective_route TEXT NOT NULL,
                live_route TEXT NOT NULL,
                shadow_mode INTEGER NOT NULL DEFAULT 1,
                corrected_route TEXT,
                correction_note_hash TEXT,
                corrected_at TEXT,
                error_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(resident_id, interface, channel_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_attention_router_recent
            ON attention_router_events(resident_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_attention_router_cache
            ON attention_router_events(
                resident_id, content_hash, model, semantic_status, created_at
            );

            CREATE TABLE IF NOT EXISTS attention_router_counters (
                resident_id TEXT NOT NULL,
                bucket_hour TEXT NOT NULL,
                counter_name TEXT NOT NULL,
                counter_value INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(resident_id, bucket_hour, counter_name)
            );
            """
        )



def _budget_state(
    db: Any,
    resident_id: str,
    settings: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    ensure_schema(db)
    hour = (now - timedelta(hours=1)).isoformat()
    day = (now - timedelta(days=1)).isoformat()
    with db.connect() as connection:
        hour_calls = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count FROM attention_router_events
                WHERE resident_id=? AND semantic_requested=1 AND created_at>=?
                """,
                (resident_id, hour),
            ).fetchone()["count"]
        )
        day_row = connection.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(estimated_input_tokens), 0) AS tokens
            FROM attention_router_events
            WHERE resident_id=? AND semantic_requested=1 AND created_at>=?
            """,
            (resident_id, day),
        ).fetchone()
    day_calls = int(day_row["count"])
    day_tokens = int(day_row["tokens"])
    allowed = (
        hour_calls < int(settings["max_calls_per_hour"])
        and day_calls < int(settings["max_calls_per_day"])
        and day_tokens < int(settings["daily_input_token_budget"])
    )
    reason = None
    if hour_calls >= int(settings["max_calls_per_hour"]):
        reason = "hourly_call_budget"
    elif day_calls >= int(settings["max_calls_per_day"]):
        reason = "daily_call_budget"
    elif day_tokens >= int(settings["daily_input_token_budget"]):
        reason = "daily_input_token_budget"
    return {
        "allowed": allowed,
        "reason": reason,
        "hour_calls": hour_calls,
        "day_calls": day_calls,
        "day_estimated_input_tokens": day_tokens,
    }


def _cached_semantic(
    db: Any,
    resident_id: str,
    content_hash: str,
    model: str,
    cache_hours: int,
) -> dict[str, Any] | None:
    if cache_hours <= 0:
        return None
    cutoff = (datetime.now(UTC) - timedelta(hours=cache_hours)).isoformat()
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT semantic_route, semantic_confidence, semantic_addressed,
                   semantic_relevance, semantic_reason_code, model,
                   actual_input_tokens, actual_output_tokens
            FROM attention_router_events
            WHERE resident_id=? AND content_hash=? AND model=?
              AND semantic_status IN ('succeeded', 'cached')
              AND created_at>=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (resident_id, content_hash, model, cutoff),
        ).fetchone()
    if not row:
        return None
    return {
        "route": str(row["semantic_route"]),
        "confidence": float(row["semantic_confidence"]),
        "addressed_to_resident": bool(row["semantic_addressed"]),
        "resident_relevance": str(row["semantic_relevance"]),
        "reason_code": str(row["semantic_reason_code"]),
        "model": str(row["model"]),
        "usage": {
            "input_tokens": row["actual_input_tokens"],
            "output_tokens": row["actual_output_tokens"],
        },
    }


def semantic_effective_route(
    semantic: dict[str, Any], settings: dict[str, Any]
) -> str:
    route = str(semantic["route"])
    confidence = float(semantic["confidence"])
    if route == "invite":
        return "invite" if confidence >= float(settings["invite_confidence"]) else "queue"
    if route == "queue":
        return "queue" if confidence >= float(settings["queue_confidence"]) else "ignore"
    return "ignore"


def record_evaluation(
    db: Any,
    config: Any,
    *,
    resident_id: str,
    room_id: str,
    listening_event_id: str | None,
    interface: str,
    channel_id: str,
    message_id: str,
    author_trust: str,
    content: str,
    lexical: LexicalDecision,
    live_route: str,
    signal_kind: str = "ambient_text",
    semantic_evaluator: SemanticEvaluator | None = None,
) -> dict[str, Any]:
    ensure_schema(db)
    content_hash = sha256_text(content)
    now = datetime.now(UTC)
    now_text = now.isoformat()
    settings = operator_settings(config)
    event_id = new_id("router")
    estimated_tokens = _estimate_tokens(
        str(content)[: int(settings["max_message_chars"])]
    )
    semantic_requested = False
    semantic_status = "not_needed"
    semantic: dict[str, Any] | None = None
    error_type: str | None = None

    with db.connect() as connection:
        existing = connection.execute(
            """
            SELECT * FROM attention_router_events
            WHERE resident_id=? AND interface=? AND channel_id=? AND message_id=?
            """,
            (resident_id, interface, channel_id, message_id),
        ).fetchone()
    if existing:
        return _event_row(existing)

    if lexical.route == "semantic_check" and live_route != "invite":
        if author_trust != "allowlisted":
            semantic_status = "refused_untrusted"
        elif not bool(settings["semantic_enabled"]):
            semantic_status = "disabled"
        else:
            cached = _cached_semantic(
                db,
                resident_id,
                content_hash,
                str(settings["model"]),
                int(settings["cache_hours"]),
            )
            if cached is not None:
                semantic = _validate_semantic(cached)
                semantic_status = "cached"
            else:
                budget = _budget_state(db, resident_id, settings, now)
                if not budget["allowed"]:
                    semantic_status = "budget_blocked"
                    error_type = str(budget["reason"])
                else:
                    semantic_requested = True
                    evaluator = semantic_evaluator or evaluate_semantics
                    try:
                        semantic = _validate_semantic(
                            evaluator(
                                config,
                                str(content)[: int(settings["max_message_chars"])],
                                {
                                    "settings": settings,
                                    "interface": interface,
                                    "signal_kind": signal_kind,
                                    "lexical_reasons": list(lexical.reasons),
                                },
                            )
                        )
                        semantic_status = "succeeded"
                    except Exception as exc:
                        semantic_status = "error"
                        error_type = type(exc).__name__

    if semantic is not None:
        effective_route = semantic_effective_route(semantic, settings)
    elif lexical.route == "semantic_check":
        effective_route = str(settings["fail_closed_route"])
    else:
        effective_route = lexical.route

    usage = dict(semantic.get("usage") or {}) if semantic else {}
    actual_input = _usage_value(usage, "input_tokens", "prompt_tokens")
    actual_output = _usage_value(usage, "output_tokens", "completion_tokens")
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO attention_router_events
            (id, resident_id, room_id, listening_event_id, interface, channel_id,
             message_id, author_trust, content_hash, lexical_route, lexical_score,
             lexical_reasons_json, matched_term_hashes_json, semantic_requested,
             semantic_status, semantic_route, semantic_confidence,
             semantic_addressed, semantic_relevance, semantic_reason_code, model,
             estimated_input_tokens, actual_input_tokens, actual_output_tokens,
             effective_route, live_route, shadow_mode, created_at, updated_at,
             error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                event_id,
                resident_id,
                room_id,
                listening_event_id,
                interface,
                channel_id,
                message_id,
                author_trust,
                content_hash,
                lexical.route,
                lexical.score,
                stable_json(list(lexical.reasons)),
                stable_json(list(lexical.matched_term_hashes)),
                1 if semantic_requested else 0,
                semantic_status,
                semantic.get("route") if semantic else None,
                semantic.get("confidence") if semantic else None,
                (1 if semantic.get("addressed_to_resident") else 0)
                if semantic
                else None,
                semantic.get("resident_relevance") if semantic else None,
                semantic.get("reason_code") if semantic else None,
                semantic.get("model") if semantic else str(settings["model"]),
                estimated_tokens if semantic_requested else 0,
                actual_input,
                actual_output,
                effective_route,
                live_route,
                now_text,
                now_text,
                error_type,
            ),
        )
        row = connection.execute(
            "SELECT * FROM attention_router_events WHERE id=?", (event_id,)
        ).fetchone()
    return _event_row(row)


def increment_counter(db: Any, resident_id: str, name: str, value: int = 1) -> None:
    ensure_schema(db)
    now = datetime.now(UTC)
    bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO attention_router_counters
            (resident_id, bucket_hour, counter_name, counter_value, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(resident_id, bucket_hour, counter_name) DO UPDATE SET
              counter_value=counter_value + excluded.counter_value,
              updated_at=excluded.updated_at
            """,
            (resident_id, bucket, str(name), int(value), utc_now_iso()),
        )


def _event_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    for field in ("lexical_reasons_json", "matched_term_hashes_json"):
        raw = str(result.pop(field, "[]") or "[]")
        try:
            result[field.removesuffix("_json")] = json.loads(raw)
        except json.JSONDecodeError:
            result[field.removesuffix("_json")] = []
    for field in ("semantic_requested", "semantic_addressed", "shadow_mode"):
        if result.get(field) is not None:
            result[field] = bool(result[field])
    result["raw_content_stored"] = False
    result["authority_changed"] = False
    return result


def list_events(db: Any, resident_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM attention_router_events
            WHERE resident_id=? ORDER BY created_at DESC LIMIT ?
            """,
            (resident_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_event_row(row) for row in rows]


def inspect_event(db: Any, resident_id: str, event_id: str) -> dict[str, Any]:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM attention_router_events WHERE id=? AND resident_id=?",
            (event_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown attention router event")
    return _event_row(row)


def by_listening_event(
    db: Any, resident_id: str, listening_event_id: str
) -> dict[str, Any] | None:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM attention_router_events
            WHERE resident_id=? AND listening_event_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (resident_id, listening_event_id),
        ).fetchone()
    return _event_row(row) if row else None


def correct(
    db: Any,
    resident_id: str,
    event_id: str,
    *,
    route: str,
    note: str = "",
) -> dict[str, Any]:
    normalized = str(route).strip().lower()
    if normalized not in SEMANTIC_ROUTES:
        raise ValueError("corrected route must be ignore, queue, or invite")
    now = utc_now_iso()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM attention_router_events WHERE id=? AND resident_id=?",
            (event_id, resident_id),
        ).fetchone()
        if not row:
            raise KeyError("unknown attention router event")
        connection.execute(
            """
            UPDATE attention_router_events
            SET corrected_route=?, correction_note_hash=?, corrected_at=?, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (
                normalized,
                sha256_text(note) if note else None,
                now,
                now,
                event_id,
                resident_id,
            ),
        )
    return inspect_event(db, resident_id, event_id)


def metrics(db: Any, resident_id: str, *, hours: int = 24) -> dict[str, Any]:
    ensure_schema(db)
    window = max(1, min(int(hours), 24 * 30))
    cutoff = (datetime.now(UTC) - timedelta(hours=window)).isoformat()
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT lexical_route, semantic_status, semantic_route, effective_route,
                   live_route, corrected_route, estimated_input_tokens,
                   actual_input_tokens, actual_output_tokens
            FROM attention_router_events
            WHERE resident_id=? AND created_at>=?
            """,
            (resident_id, cutoff),
        ).fetchall()
        counters = connection.execute(
            """
            SELECT counter_name, COALESCE(SUM(counter_value), 0) AS value
            FROM attention_router_counters
            WHERE resident_id=? AND bucket_hour>=?
            GROUP BY counter_name
            """,
            (resident_id, cutoff),
        ).fetchall()
    count_by = lambda field: {
        key: sum(1 for row in rows if str(row[field] or "") == key)
        for key in sorted({str(row[field] or "") for row in rows if row[field]})
    }
    effective = count_by("effective_route")
    avoided = sum(
        1 for row in rows if str(row["effective_route"] or "") != "invite"
    )
    settings_estimate = 0
    # Exact resident-context cost is unavailable here; report decisions and gate usage,
    # not invented savings. The caller may add a configured estimate separately.
    return {
        "window_hours": window,
        "events": len(rows),
        "lexical_routes": count_by("lexical_route"),
        "semantic_statuses": count_by("semantic_status"),
        "semantic_routes": count_by("semantic_route"),
        "effective_routes": effective,
        "live_routes": count_by("live_route"),
        "resident_corrections": sum(1 for row in rows if row["corrected_route"]),
        "non_invite_shadow_decisions": avoided,
        "semantic_calls": sum(1 for row in rows if row["semantic_status"] == "succeeded"),
        "cache_hits": sum(1 for row in rows if row["semantic_status"] == "cached"),
        "estimated_gate_input_tokens": sum(
            int(row["estimated_input_tokens"] or 0) for row in rows
        ),
        "actual_gate_input_tokens": sum(
            int(row["actual_input_tokens"] or 0) for row in rows
        ),
        "actual_gate_output_tokens": sum(
            int(row["actual_output_tokens"] or 0) for row in rows
        ),
        "local_counters": {str(row["counter_name"]): int(row["value"]) for row in counters},
        "estimated_resident_tokens_saved": settings_estimate,
        "estimate_note": (
            "Resident-turn tokens are intentionally not estimated from router data alone. "
            "Compare these decisions with provider usage receipts for a grounded savings figure."
        ),
        "shadow_mode": True,
        "live_routing_changed": False,
        "raw_content_stored": False,
    }
