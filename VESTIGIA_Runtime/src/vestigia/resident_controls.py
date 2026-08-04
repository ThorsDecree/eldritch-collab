from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import ResolvedConfig
from .db import ContinuityDB
from .utils import new_id, sha256_text, stable_json, utc_now_iso


PRIVATE_IMAGE_MODES = {"challenge", "quickdraw_pockets", "quickdraw_adopted"}
LISTENING_MODES = {"direct_only", "aliases", "watchlist", "all_allowlisted"}
LISTENING_ON_MATCH = {"queue_only", "invite_turn"}

LISTENING_SCHEMA = """
CREATE TABLE IF NOT EXISTS resident_listening_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    interface TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    author_trust TEXT NOT NULL,
    author_id_hash TEXT NOT NULL,
    match_kind TEXT NOT NULL,
    matched_term_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    consequence TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(resident_id, interface, channel_id, message_id, matched_term_hash)
);

CREATE INDEX IF NOT EXISTS idx_resident_listening_events_recent
ON resident_listening_events(resident_id, channel_id, matched_term_hash, created_at);
"""


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = unicodedata.normalize("NFKC", text).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def default_resident_controls(config: ResolvedConfig) -> dict[str, Any]:
    resident_name = str(config.get("resident.name", "Resident")).strip()
    aliases = _string_list(config.get("discord.listening_aliases", []))
    if resident_name and resident_name.casefold() != "resident" and resident_name.casefold() not in {
        item.casefold() for item in aliases
    }:
        aliases.insert(0, resident_name)
    return {
        "private_image_mode": str(
            config.get("images.private_share_mode", "challenge")
        ).strip().lower(),
        "quickdraw_pockets": _string_list(
            config.get("images.quickdraw_pockets", [])
        ),
        "listening_mode": str(
            config.get("discord.listening_mode", "direct_only")
        ).strip().lower(),
        "listening_aliases": aliases,
        "listening_watch_phrases": _string_list(
            config.get("discord.listening_watch_phrases", [])
        ),
        "listening_on_match": str(
            config.get("discord.listening_on_match", "queue_only")
        ).strip().lower(),
        "listening_cooldown_seconds": int(
            config.get("discord.listening_cooldown_seconds", 20)
        ),
    }


def _operator_limits(config: ResolvedConfig) -> dict[str, Any]:
    allowed_private = _string_list(
        config.get(
            "resident_controls.allowed_private_image_modes",
            ["challenge", "quickdraw_pockets", "quickdraw_adopted"],
        )
    )
    allowed_listening = _string_list(
        config.get(
            "resident_controls.allowed_listening_modes",
            ["direct_only", "aliases", "watchlist", "all_allowlisted"],
        )
    )
    allowed_on_match = _string_list(
        config.get(
            "resident_controls.allowed_listening_on_match",
            ["queue_only", "invite_turn"],
        )
    )
    min_cooldown = max(
        0,
        int(
            config.get(
                "resident_controls.min_listening_cooldown_seconds", 5
            )
        ),
    )
    max_cooldown = max(
        min_cooldown,
        int(
            config.get(
                "resident_controls.max_listening_cooldown_seconds", 3600
            )
        ),
    )
    return {
        "allowed_private_image_modes": [
            item for item in allowed_private if item in PRIVATE_IMAGE_MODES
        ]
        or ["challenge"],
        "allowed_listening_modes": [
            item for item in allowed_listening if item in LISTENING_MODES
        ]
        or ["direct_only"],
        "allowed_listening_on_match": [
            item for item in allowed_on_match if item in LISTENING_ON_MATCH
        ]
        or ["queue_only"],
        "allow_non_allowlisted_turns": False,
        "max_quickdraw_pockets": max(
            0, int(config.get("resident_controls.max_quickdraw_pockets", 24))
        ),
        "max_listening_terms": max(
            0, int(config.get("resident_controls.max_listening_terms", 24))
        ),
        "max_listening_term_length": max(
            1, int(config.get("resident_controls.max_listening_term_length", 80))
        ),
        "min_listening_cooldown_seconds": min_cooldown,
        "max_listening_cooldown_seconds": max_cooldown,
    }


def _load_requested(
    config: ResolvedConfig,
    db: ContinuityDB,
    resident_id: str,
) -> dict[str, Any]:
    requested = default_resident_controls(config)
    try:
        with db.connect() as connection:
            row = connection.execute(
                """
                SELECT config_json FROM resident_jobs
                WHERE resident_id=? AND kind='resident_controls'
                """,
                (resident_id,),
            ).fetchone()
    except Exception:
        row = None
    if row:
        stored = json.loads(str(row["config_json"]) or "{}")
        if isinstance(stored, dict):
            requested.update(stored)
    requested["quickdraw_pockets"] = _string_list(
        requested.get("quickdraw_pockets", [])
    )
    requested["listening_aliases"] = _string_list(
        requested.get("listening_aliases", [])
    )
    requested["listening_watch_phrases"] = _string_list(
        requested.get("listening_watch_phrases", [])
    )
    return requested


def load_resident_controls_verbose(
    config: ResolvedConfig,
    db: ContinuityDB,
    resident_id: str,
) -> dict[str, Any]:
    requested = _load_requested(config, db, resident_id)
    limits = _operator_limits(config)

    private_mode = str(requested.get("private_image_mode") or "challenge").strip().lower()
    if private_mode not in limits["allowed_private_image_modes"]:
        private_mode = "challenge"

    listening_mode = str(requested.get("listening_mode") or "direct_only").strip().lower()
    if listening_mode not in limits["allowed_listening_modes"]:
        listening_mode = "direct_only"

    listening_on_match = str(
        requested.get("listening_on_match") or "queue_only"
    ).strip().lower()
    if listening_on_match not in limits["allowed_listening_on_match"]:
        listening_on_match = "queue_only"

    term_limit = limits["max_listening_terms"]
    length_limit = limits["max_listening_term_length"]
    aliases = [
        item
        for item in requested.get("listening_aliases", [])
        if len(item) <= length_limit
    ][:term_limit]
    remaining = max(0, term_limit - len(aliases))
    watch_phrases = [
        item
        for item in requested.get("listening_watch_phrases", [])
        if len(item) <= length_limit
    ][:remaining]

    cooldown = int(requested.get("listening_cooldown_seconds", 20))
    cooldown = max(limits["min_listening_cooldown_seconds"], cooldown)
    cooldown = min(limits["max_listening_cooldown_seconds"], cooldown)

    effective = {
        "private_image_mode": private_mode,
        "quickdraw_pockets": list(requested.get("quickdraw_pockets", []))[
            : limits["max_quickdraw_pockets"]
        ],
        "listening_mode": listening_mode,
        "listening_aliases": aliases,
        "listening_watch_phrases": watch_phrases,
        "listening_on_match": listening_on_match,
        "listening_cooldown_seconds": cooldown,
        "allow_non_allowlisted_turns": limits["allow_non_allowlisted_turns"],
    }
    return {
        "requested": requested,
        "operator_limits": limits,
        "effective": effective,
    }


def load_resident_controls(
    config: ResolvedConfig,
    db: ContinuityDB,
    resident_id: str,
) -> dict[str, Any]:
    return load_resident_controls_verbose(config, db, resident_id)["effective"]


def save_resident_controls(
    db: ContinuityDB,
    resident_id: str,
    controls: dict[str, Any],
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO resident_jobs
            (id, resident_id, kind, status, config_json, updated_at)
            VALUES ('resident-controls:' || ?, ?, 'resident_controls', 'active', ?, ?)
            ON CONFLICT(resident_id, kind) DO UPDATE SET
              status='active', config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (resident_id, resident_id, stable_json(controls), utc_now_iso()),
        )


def configure_resident_controls(
    config: ResolvedConfig,
    db: ContinuityDB,
    resident_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    mode = str(payload.get("mode") or "inspect").strip().lower()
    if mode not in {"inspect", "configure", "reset"}:
        raise ValueError("resident.control mode must be inspect, configure, or reset")
    if mode == "reset":
        save_resident_controls(db, resident_id, default_resident_controls(config))
        return load_resident_controls_verbose(config, db, resident_id)
    if mode == "inspect":
        return load_resident_controls_verbose(config, db, resident_id)

    report = load_resident_controls_verbose(config, db, resident_id)
    requested = dict(report["requested"])
    limits = report["operator_limits"]

    scalar_enums = {
        "private_image_mode": PRIVATE_IMAGE_MODES,
        "listening_mode": LISTENING_MODES,
        "listening_on_match": LISTENING_ON_MATCH,
    }
    for field, choices in scalar_enums.items():
        if field not in payload:
            continue
        value = str(payload[field]).strip().lower()
        if value not in choices:
            raise ValueError(f"{field} must be one of: {', '.join(sorted(choices))}")
        requested[field] = value

    if "quickdraw_pockets" in payload:
        pockets = _string_list(payload["quickdraw_pockets"])
        if len(pockets) > limits["max_quickdraw_pockets"]:
            raise ValueError(
                "quickdraw_pockets exceeds the operator maximum of "
                f"{limits['max_quickdraw_pockets']}"
            )
        requested["quickdraw_pockets"] = pockets

    max_terms = limits["max_listening_terms"]
    max_length = limits["max_listening_term_length"]
    aliases = (
        _string_list(payload["listening_aliases"])
        if "listening_aliases" in payload
        else list(requested.get("listening_aliases", []))
    )
    watch = (
        _string_list(payload["listening_watch_phrases"])
        if "listening_watch_phrases" in payload
        else list(requested.get("listening_watch_phrases", []))
    )
    if len(aliases) + len(watch) > max_terms:
        raise ValueError(
            f"listening terms exceed the operator maximum of {max_terms}"
        )
    if any(len(item) > max_length for item in [*aliases, *watch]):
        raise ValueError(
            f"listening terms may not exceed {max_length} characters"
        )
    requested["listening_aliases"] = aliases
    requested["listening_watch_phrases"] = watch

    if "listening_cooldown_seconds" in payload:
        requested["listening_cooldown_seconds"] = int(
            payload["listening_cooldown_seconds"]
        )

    save_resident_controls(db, resident_id, requested)
    return load_resident_controls_verbose(config, db, resident_id)


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _literal_phrase_match(content: str, phrase: str) -> bool:
    normalized_content = _normalize_text(content)
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    if normalized_phrase[0].isalnum() or normalized_phrase[0] == "_":
        pattern = r"(?<!\w)" + pattern
    if normalized_phrase[-1].isalnum() or normalized_phrase[-1] == "_":
        pattern = pattern + r"(?!\w)"
    return re.search(pattern, normalized_content, flags=re.UNICODE) is not None


def find_listening_match(
    content: str,
    controls: dict[str, Any],
    *,
    author_allowlisted: bool,
) -> dict[str, str] | None:
    mode = str(controls.get("listening_mode") or "direct_only")
    if mode == "direct_only":
        return None
    if mode == "all_allowlisted":
        if author_allowlisted:
            return {
                "match_kind": "all_allowlisted",
                "matched_term": "*",
                "matched_term_hash": sha256_text("*"),
            }
        return None
    terms: list[tuple[str, str]] = [
        ("alias", str(item)) for item in controls.get("listening_aliases", [])
    ]
    if mode == "watchlist":
        terms.extend(
            ("watch_phrase", str(item))
            for item in controls.get("listening_watch_phrases", [])
        )
    for kind, term in terms:
        if _literal_phrase_match(content, term):
            normalized = _normalize_text(term)
            return {
                "match_kind": kind,
                "matched_term": term,
                "matched_term_hash": sha256_text(normalized),
            }
    return None


def listening_consequence(
    controls: dict[str, Any],
    *,
    author_allowlisted: bool,
) -> str:
    requested = str(controls.get("listening_on_match") or "queue_only")
    if requested != "invite_turn":
        return "queue_only"
    if author_allowlisted:
        return "invite_turn"
    return "queue_only"


def ensure_listening_schema(db: ContinuityDB) -> None:
    with db.connect() as connection:
        connection.executescript(LISTENING_SCHEMA)


def record_listening_event(
    db: ContinuityDB,
    *,
    resident_id: str,
    room_id: str,
    interface: str,
    channel_id: str,
    message_id: str,
    author_id: str,
    author_trust: str,
    content: str,
    match: dict[str, str],
    consequence: str,
    cooldown_seconds: int,
) -> dict[str, Any]:
    ensure_listening_schema(db)
    now = datetime.now(UTC)
    now_text = now.isoformat()
    cutoff = (now - timedelta(seconds=max(0, int(cooldown_seconds)))).isoformat()
    term_hash = str(match["matched_term_hash"])
    with db.connect() as connection:
        existing_message = connection.execute(
            """
            SELECT id, status FROM resident_listening_events
            WHERE resident_id=? AND interface=? AND channel_id=?
              AND message_id=? AND matched_term_hash=?
            """,
            (resident_id, interface, channel_id, message_id, term_hash),
        ).fetchone()
        if existing_message:
            return {
                "accepted": False,
                "reason": "duplicate",
                "event_id": str(existing_message["id"]),
                "status": str(existing_message["status"]),
            }
        recent = connection.execute(
            """
            SELECT id, status FROM resident_listening_events
            WHERE resident_id=? AND channel_id=? AND matched_term_hash=?
              AND author_trust=? AND consequence=? AND created_at>=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (
                resident_id,
                channel_id,
                term_hash,
                author_trust,
                consequence,
                cutoff,
            ),
        ).fetchone()
        if recent:
            return {
                "accepted": False,
                "reason": "cooldown",
                "event_id": str(recent["id"]),
                "status": str(recent["status"]),
            }
        event_id = new_id("listen")
        status = "invited" if consequence == "invite_turn" else "queued"
        connection.execute(
            """
            INSERT INTO resident_listening_events
            (id, resident_id, room_id, interface, channel_id, message_id,
             author_trust, author_id_hash, match_kind, matched_term_hash,
             content_hash, consequence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                resident_id,
                room_id,
                interface,
                channel_id,
                message_id,
                author_trust,
                sha256_text(author_id),
                str(match["match_kind"]),
                term_hash,
                sha256_text(content),
                consequence,
                status,
                now_text,
                now_text,
            ),
        )
    return {
        "accepted": True,
        "reason": "recorded",
        "event_id": event_id,
        "status": status,
        "consequence": consequence,
        "match_kind": str(match["match_kind"]),
        "matched_term_hash": term_hash,
    }


def mark_listening_event(
    db: ContinuityDB,
    event_id: str,
    *,
    status: str,
) -> None:
    ensure_listening_schema(db)
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE resident_listening_events
            SET status=?, updated_at=? WHERE id=?
            """,
            (status, utc_now_iso(), event_id),
        )


def list_listening_events(
    db: ContinuityDB,
    resident_id: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    ensure_listening_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, room_id, interface, channel_id, message_id, author_trust,
                   match_kind, matched_term_hash, content_hash, consequence,
                   status, created_at, updated_at
            FROM resident_listening_events
            WHERE resident_id=? ORDER BY created_at DESC LIMIT ?
            """,
            (resident_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(row) for row in rows]
