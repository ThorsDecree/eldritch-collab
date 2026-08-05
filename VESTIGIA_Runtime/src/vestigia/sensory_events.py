from __future__ import annotations

import json
from typing import Any, Callable

from .utils import stable_json, utc_now_iso


def ensure_schema(db: Any, ensure_listening_schema: Callable[[Any], None]) -> None:
    ensure_listening_schema(db)
    additions = {
        "signal_kind": "TEXT NOT NULL DEFAULT 'ambient_text'",
        "attention_mode": "TEXT NOT NULL DEFAULT 'present'",
        "retention_mode": "TEXT NOT NULL DEFAULT 'receipt_only'",
        "permission_basis": "TEXT NOT NULL DEFAULT 'resident_listening_policy'",
        "explanation_json": "TEXT NOT NULL DEFAULT '{}'",
        "digest_text": "TEXT",
        "response_caused": "INTEGER",
        "authorization_effect": "TEXT NOT NULL DEFAULT 'none'",
        "forgotten_at": "TEXT",
    }
    with db.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(resident_listening_events)"
            ).fetchall()
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE resident_listening_events ADD COLUMN {name} {definition}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_resident_listening_explain "
            "ON resident_listening_events(resident_id, status, created_at)"
        )


def _digest(content: str, maximum: int) -> str:
    text = " ".join(str(content).split())
    if len(text) <= maximum:
        return text
    return text[: max(1, maximum - 1)].rstrip() + "…"


def record(
    original: Callable[..., dict[str, Any]],
    ensure_listening_schema: Callable[[Any], None],
    db: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    ensure_schema(db, ensure_listening_schema)
    match = dict(kwargs.get("match") or {})
    sensory = dict(match.get("_sensory") or {})
    retention = str(sensory.get("retention_mode") or "receipt_only")
    if retention == "none":
        return {
            "accepted": False,
            "reason": "retention_none",
            "status": "unrecorded",
            "consequence": "ignore",
        }
    result = original(db, **kwargs)
    if not result.get("accepted"):
        return result

    event_id = str(result["event_id"])
    trust = str(kwargs.get("author_trust") or "")
    effective_retention = retention
    digest_text: str | None = None
    if retention == "short_digest":
        if trust == "allowlisted":
            digest_text = _digest(
                str(kwargs.get("content") or ""),
                max(40, min(int(sensory.get("digest_chars") or 280), 1000)),
            )
        else:
            effective_retention = "receipt_only"
    explanation = {
        "source": str(kwargs.get("interface") or "unknown"),
        "signal_kind": str(sensory.get("signal_kind") or "ambient_text"),
        "permission_basis": str(
            sensory.get("permission_basis") or "resident_listening_policy"
        ),
        "attention_mode": str(sensory.get("attention_mode") or "present"),
        "retention_mode": effective_retention,
        "author_trust": trust,
        "consequence": str(kwargs.get("consequence") or "queue_only"),
        "authorization_changed": False,
        "raw_content_stored": bool(digest_text),
    }
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE resident_listening_events
            SET signal_kind=?, attention_mode=?, retention_mode=?,
                permission_basis=?, explanation_json=?, digest_text=?,
                authorization_effect='none', updated_at=?
            WHERE id=?
            """,
            (
                explanation["signal_kind"],
                explanation["attention_mode"],
                effective_retention,
                explanation["permission_basis"],
                stable_json(explanation),
                digest_text,
                utc_now_iso(),
                event_id,
            ),
        )
    return {
        **result,
        "signal_kind": explanation["signal_kind"],
        "attention_mode": explanation["attention_mode"],
        "retention_mode": effective_retention,
        "raw_content_stored": bool(digest_text),
        "authorization_changed": False,
    }


def mark(
    original: Callable[..., None],
    ensure_listening_schema: Callable[[Any], None],
    db: Any,
    event_id: str,
    *,
    status: str,
) -> None:
    ensure_schema(db, ensure_listening_schema)
    original(db, event_id, status=status)
    caused: int | None = None
    if status == "resident_response_prepared":
        caused = 1
    elif status in {
        "observed_no_reply",
        "runtime_suppressed",
        "rate_limited",
        "forgotten",
    }:
        caused = 0
    if caused is not None:
        with db.connect() as connection:
            connection.execute(
                "UPDATE resident_listening_events "
                "SET response_caused=?, updated_at=? WHERE id=?",
                (caused, utc_now_iso(), event_id),
            )


def _row(row: Any) -> dict[str, Any]:
    result = dict(row)
    raw = str(result.pop("explanation_json", "{}") or "{}")
    try:
        result["explanation"] = json.loads(raw)
    except json.JSONDecodeError:
        result["explanation"] = {}
    if result.get("forgotten_at"):
        result["digest_text"] = None
    if result.get("response_caused") is not None:
        result["response_caused"] = bool(result["response_caused"])
    result["authorization_changed"] = False
    return result


def list_events(
    db: Any,
    resident_id: str,
    ensure_listening_schema: Callable[[Any], None],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_schema(db, ensure_listening_schema)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, room_id, interface, channel_id, message_id, author_trust,
                   match_kind, matched_term_hash, content_hash, consequence,
                   status, signal_kind, attention_mode, retention_mode,
                   permission_basis, explanation_json, digest_text,
                   response_caused, authorization_effect, forgotten_at,
                   created_at, updated_at
            FROM resident_listening_events
            WHERE resident_id=? ORDER BY created_at DESC LIMIT ?
            """,
            (resident_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [_row(row) for row in rows]


def explain(
    db: Any,
    resident_id: str,
    event_id: str,
    ensure_listening_schema: Callable[[Any], None],
) -> dict[str, Any]:
    ensure_schema(db, ensure_listening_schema)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM resident_listening_events WHERE id=? AND resident_id=?",
            (event_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown listening event")
    event = _row(row)
    caused = event.get("response_caused")
    response = (
        "It prepared a resident response."
        if caused is True
        else "It caused no resident response."
        if caused is False
        else "Its response consequence is not resolved yet."
    )
    return {
        "event": event,
        "why": (
            f"This reached the listening ledger through {event['signal_kind']} under "
            f"{event['permission_basis']}; attention was {event['attention_mode']} and "
            f"retention was {event['retention_mode']}. {response} "
            "It granted no participant or tool authority."
        ),
        "authorization_changed": False,
    }


def forget(
    db: Any,
    resident_id: str,
    event_id: str,
    ensure_listening_schema: Callable[[Any], None],
) -> dict[str, Any]:
    ensure_schema(db, ensure_listening_schema)
    now = utc_now_iso()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM resident_listening_events WHERE id=? AND resident_id=?",
            (event_id, resident_id),
        ).fetchone()
        if not row:
            raise KeyError("unknown listening event")
        connection.execute(
            """
            UPDATE resident_listening_events
            SET digest_text=NULL, status='forgotten', retention_mode='receipt_only',
                response_caused=0, forgotten_at=?, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (now, now, event_id, resident_id),
        )
    return {
        "event_id": event_id,
        "status": "forgotten",
        "digest_removed": True,
        "minimal_hash_receipt_preserved": True,
        "authorization_changed": False,
    }
