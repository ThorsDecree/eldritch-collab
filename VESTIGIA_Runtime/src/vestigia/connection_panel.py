from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .connection_controls import load_disabled_context_sources
from .context_controls import load_context_controls_verbose
from .models import RuntimeState
from .resident_controls import load_resident_controls_verbose


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set, dict)) else 0


def connection_panel(house: Any) -> dict[str, Any]:
    """Return a redacted snapshot of configured and attached Runtime connections."""
    config = house.config
    provider = getattr(house, "_runtime_provider", None)
    context_sources = getattr(house, "_runtime_context_sources", None)
    disabled_sources = load_disabled_context_sources(house.db, house.resident_id)

    provider_kind = str(config.get("provider.kind", "openai")).strip().lower()
    routes = {
        route: str(config.get(f"models.{route}", "")).strip()
        for route in ("default", "big", "thinking", "image", "vision")
        if str(config.get(f"models.{route}", "")).strip()
    }
    credentials = {
        "openai": "present" if config.secret("OPENAI_API_KEY") else "absent",
        "anthropic": "present" if config.secret("ANTHROPIC_API_KEY") else "absent",
        "gemini": "present" if config.secret("GEMINI_API_KEY") else "absent",
        "discord_bot": "present" if config.secret("DISCORD_BOT_TOKEN") else "absent",
    }
    if provider_kind == "fake":
        active_provider_key = "not_required"
    else:
        key_name = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }.get(provider_kind)
        active_provider_key = (
            "present" if key_name and config.secret(key_name) else "absent"
        )

    sources: list[dict[str, Any]] = []
    if context_sources is not None:
        for source in context_sources:
            name = str(getattr(source, "name", "")).strip().lower()
            required = bool(getattr(source, "required", False))
            sources.append(
                {
                    "name": name,
                    "required": required,
                    "resident_toggle_available": not required,
                    "enabled_for_future_context": required or name not in disabled_sources,
                }
            )
        sources.sort(key=lambda item: item["name"])

    context_preferences = load_context_controls_verbose(
        config, house.db, house.resident_id
    )["effective"]
    resident_preferences = load_resident_controls_verbose(
        config, house.db, house.resident_id
    )["effective"]

    capabilities = house.registry.describe()
    outward_actions = [
        {
            "name": item["name"],
            "confirmation": item["confirmation"],
            "effects": item["effects"],
        }
        for item in capabilities
        if item.get("outward_facing")
    ]
    with house.db.connect() as connection:
        job_rows = connection.execute(
            """
            SELECT status, config_json FROM resident_jobs
            WHERE resident_id=? AND kind LIKE 'resident_task:%'
              AND status IN ('active','queued','paused')
            """,
            (house.resident_id,),
        ).fetchall()
    pending = {"active": 0, "queued": 0, "paused": 0}
    for row in job_rows:
        job_config = json.loads(str(row["config_json"]) or "{}")
        expires_at = str(job_config.get("expires_at") or "").strip()
        if expires_at:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                raise ValueError("resident task expiry must include a timezone")
            if expiry <= datetime.now(UTC):
                continue
        pending[str(row["status"])] += 1
    pending["total"] = sum(pending.values())

    return {
        "schema_version": "vestigia.connection-panel.v1",
        "runtime": {
            "lifecycle_state": house.db.current_state(house.resident_id)
            or RuntimeState.ORIENTATION.value,
        },
        "provider": {
            "configured_kind": provider_kind,
            "routes": routes,
            "adapter": type(provider).__name__ if provider is not None else "not_attached",
            "key_status": active_provider_key,
            "reachability": "not_reported",
        },
        "credentials": credentials,
        "interfaces": {
            "discord": {
                "enabled": bool(config.get("interface.discord.enabled", False)),
                "token_configured": credentials["discord_bot"],
                "allowed_user_count": _count(config.get("discord.allowed_user_ids", [])),
                "allowed_channel_count": _count(config.get("discord.allowed_channel_ids", [])),
                "dms_allowed": bool(config.get("discord.allow_dms", True)),
            }
        },
        "context_sources": {"attached": context_sources is not None, "sources": sources},
        "resident_preferences": {
            "private_image_mode": str(resident_preferences["private_image_mode"]),
            "ambient_visibility": str(context_preferences["ambient_visibility"]),
            "listening_mode": str(resident_preferences["listening_mode"]),
            "listening_on_match": str(resident_preferences["listening_on_match"]),
            "listening_cooldown_seconds": int(
                resident_preferences["listening_cooldown_seconds"]
            ),
        },
        "capabilities": {
            "registered": len(capabilities),
            "enabled": sum(bool(item.get("enabled")) for item in capabilities),
            "callable": sum(bool(item.get("callable_now")) for item in capabilities),
            "outward_actions": outward_actions,
        },
        "pending_work": pending,
        "boundaries": {
            "reachability": "not_reported",
            "source_toggle_effect": "future_context_assembly_only",
            "provider_and_transport_shutdown": "operator_managed",
        },
    }
