from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .attention_store import ensure_schema as ensure_router_schema
from .attention_types import normalize, operator_settings
from .utils import new_id, sha256_text, stable_json, utc_now_iso


PREFERENCE_KINDS = {"always_notice", "usually_ignore", "semantic_check_only"}
QUIET_PRESETS = {"ambient_closed", "direct_only", "everything_closed", "custom"}
CORRECTION_KINDS = {
    "should_ignore",
    "worth_inviting",
    "keyword_too_broad",
    "fixture_or_quote",
}


def _aware(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def ensure_schema(db: Any) -> None:
    ensure_router_schema(db)
    with db.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS attention_quiet_sessions (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                preset TEXT NOT NULL,
                ambient_open INTEGER NOT NULL,
                mention_open INTEGER NOT NULL,
                reply_open INTEGER NOT NULL,
                command_open INTEGER NOT NULL,
                dm_open INTEGER NOT NULL,
                baseline_json TEXT NOT NULL,
                status TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                expires_at TEXT,
                restored_at TEXT,
                released_at TEXT,
                created_by TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attention_quiet_resident
            ON attention_quiet_sessions(resident_id, created_at);

            CREATE TABLE IF NOT EXISTS attention_preferences (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                term TEXT NOT NULL,
                normalized_term TEXT NOT NULL,
                interface TEXT NOT NULL DEFAULT 'all',
                channel_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(resident_id, kind, normalized_term, interface, channel_id)
            );

            CREATE INDEX IF NOT EXISTS idx_attention_preferences_active
            ON attention_preferences(resident_id, status, interface, channel_id);

            CREATE TABLE IF NOT EXISTS attention_wake_receipts (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                listening_event_id TEXT,
                signal_kind TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                platform_allowed INTEGER NOT NULL DEFAULT 1,
                resident_scope_allowed INTEGER NOT NULL DEFAULT 1,
                live_route TEXT NOT NULL,
                router_event_id TEXT,
                lexical_route TEXT,
                semantic_status TEXT,
                semantic_route TEXT,
                included_context_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                turn_id TEXT,
                response_prepared INTEGER,
                authority_changed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(resident_id, interface, channel_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_attention_wake_recent
            ON attention_wake_receipts(resident_id, created_at);

            CREATE TABLE IF NOT EXISTS attention_corrections (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                router_event_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                proposed_route TEXT,
                term_hash TEXT,
                note_hash TEXT,
                status TEXT NOT NULL DEFAULT 'awaiting_review',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attention_corrections_review
            ON attention_corrections(resident_id, status, created_at);

            CREATE TABLE IF NOT EXISTS attention_keyring_receipts (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject_id TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )


def _receipt(
    db: Any,
    resident_id: str,
    kind: str,
    *,
    subject_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_schema(db)
    receipt_id = new_id("keyring_receipt")
    created_at = utc_now_iso()
    body = dict(payload or {})
    body.setdefault("authority_changed", False)
    body.setdefault("outward_action", False)
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO attention_keyring_receipts
            (id, resident_id, kind, subject_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                resident_id,
                kind,
                subject_id,
                stable_json(body),
                created_at,
            ),
        )
    return {
        "id": receipt_id,
        "kind": kind,
        "subject_id": subject_id,
        "payload": body,
        "created_at": created_at,
    }


def list_keyring_receipts(
    db: Any, resident_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM attention_keyring_receipts
            WHERE resident_id=? ORDER BY created_at DESC LIMIT ?
            """,
            (resident_id, max(1, min(int(limit), 200))),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(str(item.pop("payload_json") or "{}"))
        except json.JSONDecodeError:
            item["payload"] = {}
        result.append(item)
    return result


def baseline_from_controls(controls: dict[str, Any]) -> dict[str, bool]:
    signals = {
        str(item)
        for item in controls.get(
            "listening_ingress_signals",
            ["mention", "reply", "dm", "command", "ambient_text"],
        )
    }
    return {
        "ambient_open": "ambient_text" in signals,
        "mention_open": "mention" in signals,
        "reply_open": "reply" in signals,
        "command_open": "command" in signals,
        "dm_open": bool(controls.get("listening_allow_dms", True)) and "dm" in signals,
    }


def _quiet_mask(
    preset: str,
    baseline: dict[str, bool],
    custom: dict[str, Any] | None = None,
) -> dict[str, bool]:
    if preset not in QUIET_PRESETS:
        raise ValueError("quiet preset is invalid")
    if preset in {"ambient_closed", "direct_only"}:
        return {**baseline, "ambient_open": False}
    if preset == "everything_closed":
        return {key: False for key in baseline}
    supplied = dict(custom or {})
    return {
        key: bool(supplied.get(key, baseline[key]))
        for key in baseline
    }


def _quiet_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    try:
        result["baseline"] = json.loads(str(result.pop("baseline_json") or "{}"))
    except json.JSONDecodeError:
        result["baseline"] = {}
    for field in (
        "ambient_open",
        "mention_open",
        "reply_open",
        "command_open",
        "dm_open",
    ):
        result[field] = bool(result[field])
    result["authority_changed"] = False
    result["outward_action"] = False
    return result


def latest_quiet(db: Any, resident_id: str) -> dict[str, Any] | None:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM attention_quiet_sessions
            WHERE resident_id=? AND status IN ('active', 'restored_locked')
            ORDER BY created_at DESC LIMIT 1
            """,
            (resident_id,),
        ).fetchone()
    return _quiet_row(row) if row else None


def activate_quiet(
    db: Any,
    resident_id: str,
    controls: dict[str, Any],
    *,
    preset: str,
    expires_at: str | None,
    custom: dict[str, Any] | None = None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    ensure_schema(db)
    normalized_preset = str(preset).strip().lower()
    baseline = baseline_from_controls(controls)
    mask = _quiet_mask(normalized_preset, baseline, custom)
    now = utc_now_iso()
    expiry = _aware(expires_at).isoformat() if expires_at else None
    if expiry and _aware(expiry) <= datetime.now(UTC):
        raise ValueError("quiet expiry must be in the future")
    session_id = new_id("quiet")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_quiet_sessions
            SET status='released', released_at=?, updated_at=?
            WHERE resident_id=? AND status IN ('active', 'restored_locked')
            """,
            (now, now, resident_id),
        )
        connection.execute(
            """
            INSERT INTO attention_quiet_sessions
            (id, resident_id, preset, ambient_open, mention_open, reply_open,
             command_open, dm_open, baseline_json, status, starts_at, expires_at,
             created_by, reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                resident_id,
                normalized_preset,
                int(mask["ambient_open"]),
                int(mask["mention_open"]),
                int(mask["reply_open"]),
                int(mask["command_open"]),
                int(mask["dm_open"]),
                stable_json(baseline),
                now,
                expiry,
                actor,
                reason,
                now,
                now,
            ),
        )
    receipt = _receipt(
        db,
        resident_id,
        "quiet_activated",
        subject_id=session_id,
        payload={
            "preset": normalized_preset,
            "expires_at": expiry,
            "baseline": baseline,
            "active_mask": mask,
            "restoration": "baseline_locked_until_explicit_release",
        },
    )
    return {"quiet": quiet_state(db, resident_id, controls), "receipt": receipt}


def quiet_state(
    db: Any, resident_id: str, controls: dict[str, Any]
) -> dict[str, Any]:
    current = latest_quiet(db, resident_id)
    if current is None:
        return {
            "phase": "open",
            "session": None,
            "effective": baseline_from_controls(controls),
            "restoration_locked": False,
            "authority_changed": False,
        }
    now = datetime.now(UTC)
    if current["status"] == "active" and current.get("expires_at"):
        if _aware(str(current["expires_at"])) <= now:
            stamp = now.isoformat()
            with db.connect() as connection:
                connection.execute(
                    """
                    UPDATE attention_quiet_sessions
                    SET status='restored_locked', restored_at=?, updated_at=?
                    WHERE id=? AND status='active'
                    """,
                    (stamp, stamp, current["id"]),
                )
            _receipt(
                db,
                resident_id,
                "quiet_expired_restored",
                subject_id=str(current["id"]),
                payload={
                    "restored_to": current["baseline"],
                    "restoration_locked": True,
                    "note": "Expiry restored only the previously open doors.",
                },
            )
            current = latest_quiet(db, resident_id)
            if current is None:
                raise RuntimeError("quiet restoration state disappeared")

    current_controls = baseline_from_controls(controls)
    if current["status"] == "active":
        desired = {
            key: bool(current[key])
            for key in current_controls
        }
        phase = "quiet"
    else:
        desired = {
            key: bool(current["baseline"].get(key, False))
            for key in current_controls
        }
        phase = "restored_locked"
    effective = {
        key: bool(current_controls[key] and desired[key])
        for key in current_controls
    }
    return {
        "phase": phase,
        "session": current,
        "effective": effective,
        "restoration_locked": phase == "restored_locked",
        "authority_changed": False,
        "outward_action": False,
    }


def cancel_quiet(
    db: Any, resident_id: str, controls: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    current = latest_quiet(db, resident_id)
    if current is None:
        return {"quiet": quiet_state(db, resident_id, controls), "receipt": None}
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_quiet_sessions
            SET status='restored_locked', restored_at=?, updated_at=?
            WHERE id=?
            """,
            (now, now, current["id"]),
        )
    receipt = _receipt(
        db,
        resident_id,
        "quiet_cancelled_restored",
        subject_id=str(current["id"]),
        payload={
            "actor": actor,
            "restored_to": current["baseline"],
            "restoration_locked": True,
        },
    )
    return {"quiet": quiet_state(db, resident_id, controls), "receipt": receipt}


def release_quiet(
    db: Any, resident_id: str, controls: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    current = latest_quiet(db, resident_id)
    if current is None:
        return {"quiet": quiet_state(db, resident_id, controls), "receipt": None}
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_quiet_sessions
            SET status='released', released_at=?, updated_at=?
            WHERE id=?
            """,
            (now, now, current["id"]),
        )
    receipt = _receipt(
        db,
        resident_id,
        "quiet_explicitly_released",
        subject_id=str(current["id"]),
        payload={
            "actor": actor,
            "explicit_widening_possible_after_release": True,
        },
    )
    return {"quiet": quiet_state(db, resident_id, controls), "receipt": receipt}


def _preference_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    try:
        result["provenance"] = json.loads(str(result.pop("provenance_json") or "{}"))
    except json.JSONDecodeError:
        result["provenance"] = {}
    result["authority_changed"] = False
    return result


def list_preferences(
    db: Any,
    resident_id: str,
    *,
    status: str | None = "active",
    limit: int = 200,
) -> list[dict[str, Any]]:
    ensure_schema(db)
    query = "SELECT * FROM attention_preferences WHERE resident_id=?"
    params: list[Any] = [resident_id]
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with db.connect() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_preference_row(row) for row in rows]


def create_preference(
    db: Any,
    resident_id: str,
    *,
    kind: str,
    term: str,
    interface: str = "all",
    channel_id: str | None = None,
    expires_at: str | None = None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    ensure_schema(db)
    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in PREFERENCE_KINDS:
        raise ValueError("attention preference kind is invalid")
    clean_term = " ".join(str(term).split()).strip()
    normalized_term = normalize(clean_term)
    if not normalized_term:
        raise ValueError("attention preference term is required")
    clean_interface = str(interface or "all").strip().lower()
    if clean_interface not in {"all", "discord"}:
        raise ValueError("attention preference interface must be all or discord")
    clean_channel = str(channel_id).strip() if channel_id else None
    expiry = _aware(expires_at).isoformat() if expires_at else None
    now = utc_now_iso()
    preference_id = new_id("attention_pref")
    provenance = {
        "actor": actor,
        "reason": reason or "explicit resident preference",
        "source": "resident_attention_keyring",
        "inferred_from_memory": False,
    }
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO attention_preferences
            (id, resident_id, kind, term, normalized_term, interface, channel_id,
             status, provenance_json, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                preference_id,
                resident_id,
                normalized_kind,
                clean_term,
                normalized_term,
                clean_interface,
                clean_channel,
                stable_json(provenance),
                expiry,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM attention_preferences WHERE id=?", (preference_id,)
        ).fetchone()
    item = _preference_row(row)
    item["receipt"] = _receipt(
        db,
        resident_id,
        "attention_preference_created",
        subject_id=preference_id,
        payload={
            "kind": normalized_kind,
            "term_hash": sha256_text(normalized_term),
            "interface": clean_interface,
            "channel_id": clean_channel,
            "expires_at": expiry,
        },
    )
    return item


def update_preference(
    db: Any,
    resident_id: str,
    preference_id: str,
    *,
    term: str | None = None,
    kind: str | None = None,
    interface: str | None = None,
    channel_id: str | None = None,
    expires_at: str | None = None,
    status: str | None = None,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM attention_preferences WHERE id=? AND resident_id=?",
            (preference_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown attention preference")
    current = _preference_row(row)
    next_kind = str(kind or current["kind"]).strip().lower()
    if next_kind not in PREFERENCE_KINDS:
        raise ValueError("attention preference kind is invalid")
    next_term = " ".join(str(term if term is not None else current["term"]).split()).strip()
    next_normalized = normalize(next_term)
    if not next_normalized:
        raise ValueError("attention preference term is required")
    next_interface = str(interface or current["interface"]).strip().lower()
    if next_interface not in {"all", "discord"}:
        raise ValueError("attention preference interface must be all or discord")
    next_channel = (
        str(channel_id).strip()
        if channel_id is not None and str(channel_id).strip()
        else current.get("channel_id")
    )
    if expires_at is None:
        next_expiry = current.get("expires_at")
    elif str(expires_at).strip():
        next_expiry = _aware(str(expires_at)).isoformat()
    else:
        next_expiry = None
    next_status = str(status or current["status"]).strip().lower()
    if next_status not in {"active", "disabled", "deleted"}:
        raise ValueError("attention preference status is invalid")
    provenance = dict(current.get("provenance") or {})
    provenance["last_actor"] = actor
    provenance["last_reason"] = reason or "explicit resident revision"
    provenance["inferred_from_memory"] = False
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_preferences
            SET kind=?, term=?, normalized_term=?, interface=?, channel_id=?,
                status=?, provenance_json=?, expires_at=?, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (
                next_kind,
                next_term,
                next_normalized,
                next_interface,
                next_channel,
                next_status,
                stable_json(provenance),
                next_expiry,
                now,
                preference_id,
                resident_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM attention_preferences WHERE id=?", (preference_id,)
        ).fetchone()
    item = _preference_row(updated)
    item["receipt"] = _receipt(
        db,
        resident_id,
        "attention_preference_revised",
        subject_id=preference_id,
        payload={
            "kind": next_kind,
            "term_hash": sha256_text(next_normalized),
            "status": next_status,
            "interface": next_interface,
            "channel_id": next_channel,
            "expires_at": next_expiry,
        },
    )
    return item


def delete_preference(
    db: Any, resident_id: str, preference_id: str, *, actor: str
) -> dict[str, Any]:
    item = update_preference(
        db,
        resident_id,
        preference_id,
        status="deleted",
        actor=actor,
        reason="explicit resident deletion",
    )
    item["deleted"] = True
    return item


def scoped_preference_terms(
    db: Any,
    resident_id: str,
    *,
    interface: str,
    channel_id: str,
) -> dict[str, list[str]]:
    ensure_schema(db)
    now = utc_now_iso()
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM attention_preferences
            WHERE resident_id=? AND status='active'
              AND (expires_at IS NULL OR expires_at>?)
              AND interface IN ('all', ?)
              AND (channel_id IS NULL OR channel_id='' OR channel_id=?)
            ORDER BY created_at
            """,
            (resident_id, now, interface, channel_id),
        ).fetchall()
    result = {kind: [] for kind in PREFERENCE_KINDS}
    seen: dict[str, set[str]] = {kind: set() for kind in PREFERENCE_KINDS}
    for row in rows:
        item = _preference_row(row)
        kind = str(item["kind"])
        key = str(item["normalized_term"])
        if kind in result and key not in seen[kind]:
            seen[kind].add(key)
            result[kind].append(str(item["term"]))
    return result


def _wake_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    try:
        result["included_context_ids"] = json.loads(
            str(result.pop("included_context_ids_json") or "[]")
        )
    except json.JSONDecodeError:
        result["included_context_ids"] = []
    for field in (
        "platform_allowed",
        "resident_scope_allowed",
        "response_prepared",
        "authority_changed",
    ):
        if result.get(field) is not None:
            result[field] = bool(result[field])
    result["included_is_influenced"] = False
    result["influence_claimed"] = False
    return result


def open_wake_receipt(
    db: Any,
    *,
    resident_id: str,
    room_id: str,
    interface: str,
    channel_id: str,
    message_id: str,
    listening_event_id: str | None,
    signal_kind: str,
    reason_code: str,
    live_route: str,
    router_event: dict[str, Any] | None,
    included_context_ids: list[str],
) -> dict[str, Any]:
    ensure_schema(db)
    receipt_id = new_id("wake")
    now = utc_now_iso()
    router = dict(router_event or {})
    with db.connect() as connection:
        existing = connection.execute(
            """
            SELECT * FROM attention_wake_receipts
            WHERE resident_id=? AND interface=? AND channel_id=? AND message_id=?
            """,
            (resident_id, interface, channel_id, message_id),
        ).fetchone()
        if existing:
            return _wake_row(existing)
        connection.execute(
            """
            INSERT INTO attention_wake_receipts
            (id, resident_id, room_id, interface, channel_id, message_id,
             listening_event_id, signal_kind, reason_code, platform_allowed,
             resident_scope_allowed, live_route, router_event_id, lexical_route,
             semantic_status, semantic_route, included_context_ids_json, status,
             authority_changed, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?,
                    'opened', 0, ?, ?)
            """,
            (
                receipt_id,
                resident_id,
                room_id,
                interface,
                channel_id,
                message_id,
                listening_event_id,
                signal_kind,
                reason_code,
                live_route,
                router.get("id"),
                router.get("lexical_route"),
                router.get("semantic_status"),
                router.get("semantic_route"),
                stable_json(list(dict.fromkeys(included_context_ids))),
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM attention_wake_receipts WHERE id=?", (receipt_id,)
        ).fetchone()
    return _wake_row(row)


def complete_wake_receipt(
    db: Any,
    resident_id: str,
    wake_id: str,
    *,
    turn_id: str | None,
    status: str,
    response_prepared: bool | None,
) -> dict[str, Any]:
    ensure_schema(db)
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_wake_receipts
            SET turn_id=?, status=?, response_prepared=?, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (
                turn_id,
                status,
                None if response_prepared is None else int(response_prepared),
                now,
                wake_id,
                resident_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM attention_wake_receipts WHERE id=? AND resident_id=?",
            (wake_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown wake receipt")
    return _wake_row(row)


def list_wake_receipts(
    db: Any, resident_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM attention_wake_receipts
            WHERE resident_id=? ORDER BY created_at DESC LIMIT ?
            """,
            (resident_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_wake_row(row) for row in rows]


def inspect_wake_receipt(
    db: Any, resident_id: str, wake_id: str
) -> dict[str, Any]:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM attention_wake_receipts WHERE id=? AND resident_id=?",
            (wake_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown wake receipt")
    result = _wake_row(row)
    result["why"] = (
        f"The turn opened through {result['signal_kind']} under "
        f"{result['reason_code']}; the live route was {result['live_route']}. "
        "The listed context was included, not proven influential. No authority changed."
    )
    return result


def record_correction(
    db: Any,
    resident_id: str,
    *,
    router_event_id: str,
    kind: str,
    proposed_route: str | None = None,
    term: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    ensure_schema(db)
    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in CORRECTION_KINDS:
        raise ValueError("attention correction kind is invalid")
    route = str(proposed_route).strip().lower() if proposed_route else None
    if route and route not in {"ignore", "queue", "invite"}:
        raise ValueError("proposed route must be ignore, queue, or invite")
    correction_id = new_id("attention_correction")
    now = utc_now_iso()
    term_hash = sha256_text(normalize(term)) if term and normalize(term) else None
    note_hash = sha256_text(note) if note else None
    with db.connect() as connection:
        event = connection.execute(
            "SELECT id FROM attention_router_events WHERE id=? AND resident_id=?",
            (router_event_id, resident_id),
        ).fetchone()
        if not event:
            raise KeyError("unknown attention router event")
        connection.execute(
            """
            INSERT INTO attention_corrections
            (id, resident_id, router_event_id, kind, proposed_route, term_hash,
             note_hash, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_review', ?, ?)
            """,
            (
                correction_id,
                resident_id,
                router_event_id,
                normalized_kind,
                route,
                term_hash,
                note_hash,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM attention_corrections WHERE id=?", (correction_id,)
        ).fetchone()
    result = dict(row)
    result["automatic_retraining"] = False
    result["live_routing_changed"] = False
    result["raw_candidate_stored"] = False
    result["authority_changed"] = False
    return result


def list_corrections(
    db: Any,
    resident_id: str,
    *,
    status: str | None = "awaiting_review",
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_schema(db)
    query = "SELECT * FROM attention_corrections WHERE resident_id=?"
    params: list[Any] = [resident_id]
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with db.connect() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["automatic_retraining"] = False
        item["live_routing_changed"] = False
        item["raw_candidate_stored"] = False
    return result


def review_correction(
    db: Any,
    resident_id: str,
    correction_id: str,
    *,
    status: str,
) -> dict[str, Any]:
    normalized = str(status).strip().lower()
    if normalized not in {"reviewed", "dismissed", "awaiting_review"}:
        raise ValueError("correction review status is invalid")
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE attention_corrections SET status=?, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (normalized, now, correction_id, resident_id),
        )
        row = connection.execute(
            "SELECT * FROM attention_corrections WHERE id=? AND resident_id=?",
            (correction_id, resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown attention correction")
    result = dict(row)
    result["automatic_retraining"] = False
    result["live_routing_changed"] = False
    return result


def semantic_budget_snapshot(db: Any, resident_id: str, config: Any) -> dict[str, Any]:
    ensure_schema(db)
    settings = operator_settings(config)
    now = datetime.now(UTC)
    hour_cutoff = (now - timedelta(hours=1)).isoformat()
    day_cutoff = (now - timedelta(days=1)).isoformat()
    with db.connect() as connection:
        hour_calls = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count FROM attention_router_events
                WHERE resident_id=? AND semantic_requested=1 AND created_at>=?
                """,
                (resident_id, hour_cutoff),
            ).fetchone()["count"]
        )
        day = connection.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(estimated_input_tokens), 0) AS tokens
            FROM attention_router_events
            WHERE resident_id=? AND semantic_requested=1 AND created_at>=?
            """,
            (resident_id, day_cutoff),
        ).fetchone()
    day_calls = int(day["count"])
    day_tokens = int(day["tokens"])
    return {
        "semantic_enabled": bool(settings["semantic_enabled"]),
        "model": str(settings["model"]),
        "hourly": {
            "used": hour_calls,
            "limit": int(settings["max_calls_per_hour"]),
            "remaining": max(0, int(settings["max_calls_per_hour"]) - hour_calls),
        },
        "daily_calls": {
            "used": day_calls,
            "limit": int(settings["max_calls_per_day"]),
            "remaining": max(0, int(settings["max_calls_per_day"]) - day_calls),
        },
        "daily_input_tokens": {
            "used_estimated": day_tokens,
            "limit": int(settings["daily_input_token_budget"]),
            "remaining_estimated": max(
                0, int(settings["daily_input_token_budget"]) - day_tokens
            ),
        },
        "fail_closed_route": str(settings["fail_closed_route"]),
        "live_semantic_routing": False,
    }
