from __future__ import annotations

import re
from typing import Any

from .models import ProviderReply


def _display_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._:/+-]", "?", value[:128]).replace("://", ":∕∕")


def observed_model_change(
    route: str, previous_turn: tuple[str, dict[str, Any]] | None, reply: ProviderReply
) -> dict[str, Any] | None:
    if previous_turn is None:
        return None
    previous_id, metadata = previous_turn
    current = {
        "provider": reply.provider,
        "model": reply.model,
        "reported_model": reply.reported_model,
    }
    previous = {field: metadata.get(field) for field in current}
    labels = {"provider": "provider", "model": "model ID", "reported_model": "reported model"}
    changes: dict[str, str] = {}
    for field, new in current.items():
        old = previous[field]
        if isinstance(old, str) and isinstance(new, str) and old and new and old != new:
            changes[field] = f"{labels[field]}: {_display_id(old)} → {_display_id(new)}"
    if not changes:
        return None
    headline = "Model provider switched" if set(changes) == {"provider"} else "Model switched"
    return {
        "route": route,
        "previous_assistant_turn_id": previous_id,
        "previous": previous,
        "current": current,
        "changed": list(changes),
        "notice": f"⚠️ {headline} ({_display_id(route)} route) · " + " · ".join(changes.values()),
    }
