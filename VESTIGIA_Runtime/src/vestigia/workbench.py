from __future__ import annotations

from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .composition import dispatch_workbench_action, render_workbench_cards


_LANES = ("all", "continue", "review", "tend", "make", "observe")
_MAX_CARDS = 50


def _bounded_limit(value: Any, *, default: int = 12) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(_MAX_CARDS, max(1, parsed))


def workbench_view(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    lane = str(payload.get("lane") or "all").strip().lower()
    if lane not in _LANES:
        raise ValueError(f"unknown workbench lane: {lane}")
    limit = _bounded_limit(payload.get("limit"))
    projection = render_workbench_cards(house, lane=lane, limit=limit)
    implemented = list(projection["implemented_lanes"])
    planned = [item for item in _LANES if item not in {"all", *implemented}]
    return {
        "schema_version": "vestigia.workbench.v0.1",
        "lane": lane,
        "cards": projection["cards"],
        "card_count": len(projection["cards"]),
        "implemented_lanes": implemented,
        "planned_lanes": planned,
        "providers": projection["providers"],
        "authority": "projection_only",
        "invariant": (
            "Workbench cards project current house state. Card IDs and action IDs do not grant authority; "
            "providers re-resolve state and dispatch through ordinary Runtime capabilities."
        ),
    }


def _current_card(house: Any, card_id: str) -> dict[str, Any]:
    clean = str(card_id or "").strip()
    if not clean:
        raise ValueError("card_id is required")

    # Search every bounded semantic lane independently. This avoids one busy lane
    # starving card resolution in another while still refusing hidden caller-supplied
    # locators, capability payloads, or authority claims.
    for lane in _LANES:
        if lane == "all":
            continue
        projection = render_workbench_cards(house, lane=lane, limit=_MAX_CARDS)
        for card in projection["cards"]:
            if card.get("card_id") == clean:
                return card
    raise KeyError(
        "workbench card is stale or unavailable; refresh workbench.view before acting"
    )


def workbench_act(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    card = _current_card(house, str(payload.get("card_id") or ""))
    action_id = str(payload.get("action_id") or "").strip().lower()
    available = {str(item.get("action_id") or "") for item in card.get("actions", [])}
    if action_id not in available:
        raise ValueError(f"action {action_id!r} is not offered by this workbench card")
    try:
        max_tokens = int(payload.get("max_tokens", 3000))
    except (TypeError, ValueError):
        max_tokens = 3000
    max_tokens = min(4000, max(100, max_tokens))

    provider_result = dispatch_workbench_action(
        house,
        card=card,
        action_id=action_id,
        max_tokens=max_tokens,
        context=context,
    )
    return {
        "schema_version": "vestigia.workbench.v0.1",
        "card_id": card["card_id"],
        "state_fingerprint": card["state_fingerprint"],
        "provider": card["provider"],
        "action_id": action_id,
        "effect_class": card["effect_class"],
        **provider_result,
        "invariant": (
            "The Workbench core delegated to the card's registered provider; any underlying "
            "house action still used the ordinary Runtime capability dispatcher."
        ),
    }


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="workbench.view",
            description=(
                "Show a bounded resident-facing Workbench of current useful affordances. "
                "Registered providers project authoritative house state into semantic cards."
            ),
            effects=("database:read", "filesystem:read_indexed_house"),
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="workbench",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "workbench.view"},
                    "lane": {"type": "string", "enum": list(_LANES)},
                    "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_CARDS},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {"action": "workbench.view", "lane": "continue", "limit": 12, "after": "continue"},
            ),
            next_step="Choose an offered card action with workbench.act; provider plumbing is intentionally hidden.",
        ),
        lambda payload, _context: workbench_view(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="workbench.act",
            description=(
                "Take one semantic action offered by a current Workbench card. The card is "
                "re-resolved first, then its registered provider handles the semantic mapping."
            ),
            effects=("database:read", "filesystem:read_indexed_house"),
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="workbench",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "workbench.act"},
                    "card_id": {"type": "string", "minLength": 4, "maxLength": 80},
                    "action_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "max_tokens": {"type": "integer", "minimum": 100, "maximum": 4000},
                    "after": after,
                },
                required=("action", "card_id", "action_id"),
            ),
            example_envelopes=(
                {
                    "action": "workbench.act",
                    "card_id": "wb_...",
                    "action_id": "continue",
                    "after": "continue",
                },
            ),
            related_actions=("workbench.view",),
            next_step="Use the semantic outcome and refreshed_card; refresh workbench.view whenever you want the current desk state.",
        ),
        lambda payload, context: workbench_act(house, payload, context),
    )


def register_composition() -> None:
    from .composition import register_capability_installer, register_workbench_provider
    from .workbench_reading import PROVIDER_NAME, provider

    register_workbench_provider(PROVIDER_NAME, provider, order=10)
    register_capability_installer("workbench.core", _register, order=80)
