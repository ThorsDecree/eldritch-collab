from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .utils import stable_json, utc_now_iso


ATTENTION_MODES = {"present", "peeking", "digest_only", "asleep", "deaf"}
RETENTION_MODES = {"live_context", "short_digest", "receipt_only", "none"}
INGRESS_SIGNALS = {"mention", "reply", "dm", "command", "ambient_text"}
SENSORY_FIELDS = {
    "attention_mode",
    "attention_expires_at",
    "attention_after_expiry",
    "listening_retention",
    "listening_ingress_signals",
    "listening_channel_ids",
    "listening_excluded_channel_ids",
    "listening_allow_dms",
}


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
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("expected a boolean")


def aware(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("attention timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def defaults(config: Any) -> dict[str, Any]:
    return {
        "attention_mode": str(
            config.get("discord.attention_mode", "present")
        ).strip().lower(),
        "attention_expires_at": None,
        "attention_after_expiry": "present",
        "listening_retention": str(
            config.get("discord.listening_retention", "live_context")
        ).strip().lower(),
        "listening_ingress_signals": _string_list(
            config.get(
                "discord.listening_ingress_signals",
                ["mention", "reply", "dm", "command", "ambient_text"],
            )
        ),
        "listening_channel_ids": _string_list(
            config.get("discord.listening_channel_ids", [])
        ),
        "listening_excluded_channel_ids": _string_list(
            config.get("discord.listening_excluded_channel_ids", [])
        ),
        "listening_allow_dms": bool(
            config.get("discord.listening_allow_dms", True)
        ),
    }


def operator_limits(config: Any) -> dict[str, Any]:
    attention = [
        item
        for item in _string_list(
            config.get(
                "resident_controls.allowed_attention_modes",
                sorted(ATTENTION_MODES),
            )
        )
        if item in ATTENTION_MODES
    ] or ["present"]
    retention = [
        item
        for item in _string_list(
            config.get(
                "resident_controls.allowed_listening_retention_modes",
                sorted(RETENTION_MODES),
            )
        )
        if item in RETENTION_MODES
    ] or ["receipt_only"]
    signals = [
        item
        for item in _string_list(
            config.get(
                "resident_controls.allowed_listening_ingress_signals",
                sorted(INGRESS_SIGNALS),
            )
        )
        if item in INGRESS_SIGNALS
    ] or ["mention"]
    return {
        "allowed_attention_modes": attention,
        "allowed_listening_retention_modes": retention,
        "allowed_listening_ingress_signals": signals,
        "max_listening_channels": max(
            0, int(config.get("resident_controls.max_listening_channels", 64))
        ),
        "max_attention_window_seconds": max(
            60,
            int(
                config.get(
                    "resident_controls.max_attention_window_seconds", 604800
                )
            ),
        ),
        "max_listening_digest_chars": max(
            40,
            int(config.get("resident_controls.max_listening_digest_chars", 280)),
        ),
        "participant_scopes_implemented": False,
        "implemented_ingress_signals": sorted(INGRESS_SIGNALS),
    }


def ensure_store(db: Any) -> None:
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
    ensure_store(db)
    result = defaults(config)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT config_json FROM resident_jobs "
            "WHERE resident_id=? AND kind='sensory_controls'",
            (resident_id,),
        ).fetchone()
    if row:
        stored = json.loads(str(row["config_json"]) or "{}")
        if isinstance(stored, dict):
            result.update(stored)
    for field in (
        "listening_ingress_signals",
        "listening_channel_ids",
        "listening_excluded_channel_ids",
    ):
        result[field] = _string_list(result.get(field, []))
    return result


def effective(config: Any, values: dict[str, Any]) -> dict[str, Any]:
    limits = operator_limits(config)
    allowed_attention = list(limits["allowed_attention_modes"])
    attention_fallback = (
        "present" if "present" in allowed_attention else allowed_attention[0]
    )
    mode = str(values.get("attention_mode") or attention_fallback).lower()
    if mode not in allowed_attention:
        mode = attention_fallback
    after = str(values.get("attention_after_expiry") or attention_fallback).lower()
    if after not in allowed_attention:
        after = attention_fallback

    expiry_text = str(values.get("attention_expires_at") or "").strip()
    expiry: str | None = None
    expired = False
    clamped = False
    now = datetime.now(UTC)
    if expiry_text:
        try:
            parsed = aware(expiry_text)
        except ValueError:
            parsed = now
        maximum = now + timedelta(
            seconds=int(limits["max_attention_window_seconds"])
        )
        if parsed > maximum:
            parsed = maximum
            clamped = True
        expiry = parsed.isoformat()
        if parsed <= now:
            mode = after
            expired = True

    allowed_retention = list(limits["allowed_listening_retention_modes"])
    retention_fallback = (
        "receipt_only" if "receipt_only" in allowed_retention else allowed_retention[0]
    )
    retention = str(values.get("listening_retention") or retention_fallback).lower()
    if retention not in allowed_retention:
        retention = retention_fallback
    if mode in {"peeking", "asleep"}:
        retention = "receipt_only"
    elif mode == "digest_only":
        retention = (
            "short_digest"
            if "short_digest" in allowed_retention
            else retention_fallback
        )
    elif mode == "deaf":
        retention = "none" if "none" in allowed_retention else retention_fallback

    allowed_signals = set(limits["allowed_listening_ingress_signals"])
    signals = [
        item
        for item in _string_list(values.get("listening_ingress_signals", []))
        if item in allowed_signals
    ]
    if not signals:
        signals = [str(limits["allowed_listening_ingress_signals"][0])]
    maximum_channels = int(limits["max_listening_channels"])
    return {
        "attention_mode": mode,
        "attention_expires_at": expiry,
        "attention_after_expiry": after,
        "attention_expired": expired,
        "attention_expiry_clamped": clamped,
        "listening_retention": retention,
        "listening_ingress_signals": signals,
        "listening_channel_ids": _string_list(
            values.get("listening_channel_ids", [])
        )[:maximum_channels],
        "listening_excluded_channel_ids": _string_list(
            values.get("listening_excluded_channel_ids", [])
        )[:maximum_channels],
        "listening_allow_dms": bool(values.get("listening_allow_dms", True)),
        "listening_digest_chars": int(limits["max_listening_digest_chars"]),
    }


def report(config: Any, db: Any, resident_id: str) -> dict[str, Any]:
    wanted = requested(config, db, resident_id)
    return {
        "requested": wanted,
        "operator_limits": operator_limits(config),
        "effective": effective(config, wanted),
    }


def save(db: Any, resident_id: str, values: dict[str, Any]) -> None:
    ensure_store(db)
    now = utc_now_iso()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO resident_jobs
            (id, resident_id, kind, status, config_json, updated_at)
            VALUES ('sensory-controls:' || ?, ?, 'sensory_controls', 'active', ?, ?)
            ON CONFLICT(resident_id, kind) DO UPDATE SET
              status='active', config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (resident_id, resident_id, stable_json(values), now),
        )


def configure(
    config: Any,
    db: Any,
    resident_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    mode = str(payload.get("mode") or "inspect").strip().lower()
    if mode == "inspect":
        return report(config, db, resident_id)
    if mode == "reset":
        save(db, resident_id, defaults(config))
        return report(config, db, resident_id)
    if mode != "configure":
        raise ValueError("sensory controls accept inspect, configure, or reset")

    values = requested(config, db, resident_id)
    limits = operator_limits(config)
    for field, choices in (
        ("attention_mode", ATTENTION_MODES),
        ("attention_after_expiry", ATTENTION_MODES),
        ("listening_retention", RETENTION_MODES),
    ):
        if field in payload:
            value = str(payload[field]).strip().lower()
            if value not in choices:
                raise ValueError(
                    f"{field} must be one of: {', '.join(sorted(choices))}"
                )
            values[field] = value
    if "attention_expires_at" in payload:
        raw = str(payload.get("attention_expires_at") or "").strip()
        values["attention_expires_at"] = aware(raw).isoformat() if raw else None
    if "listening_ingress_signals" in payload:
        signals = _string_list(payload["listening_ingress_signals"])
        unknown = [item for item in signals if item not in INGRESS_SIGNALS]
        if unknown:
            raise ValueError("unsupported ingress signals: " + ", ".join(unknown))
        values["listening_ingress_signals"] = signals
    maximum_channels = int(limits["max_listening_channels"])
    for field in ("listening_channel_ids", "listening_excluded_channel_ids"):
        if field in payload:
            channels = _string_list(payload[field])
            if len(channels) > maximum_channels:
                raise ValueError(
                    f"{field} exceeds the operator maximum of {maximum_channels}"
                )
            values[field] = channels
    if "listening_allow_dms" in payload:
        values["listening_allow_dms"] = _as_bool(payload["listening_allow_dms"])
    save(db, resident_id, values)
    return report(config, db, resident_id)
