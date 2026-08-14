from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .composition import dispatch_workbench_action, render_workbench_cards


_LANES = ("all", "continue", "review", "tend", "make", "observe")
_MAX_CARDS = 50
_EFFECT_CLASSES = frozenset(
    {"read_only", "private_write", "house_change", "destructive", "outward"}
)
_READ_ONLY_BROKER_CONTRACT = {
    "effect_class": "read_only",
    "cost_class": "free",
    "confirmation": "none",
    "outward_facing": False,
}
_READ_ONLY_EFFECTS = ["database:read", "filesystem:read_indexed_house"]


def _bounded_limit(value: Any, *, default: int = 12) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(_MAX_CARDS, max(1, parsed))


def _declare_read_only_provider(callback: Callable[..., Any]) -> Callable[..., Any]:
    """Bind one explicitly read-only provider to the current read-only Workbench broker.

    The first Continue Reading provider predates action-level contract metadata. Keep that
    provider implementation focused on reading state while making its registered projection
    explicit and falsifiable. Future providers are expected to emit their own complete action
    contracts unless they are deliberately wrapped by a broker-specific adapter like this one.
    """

    def wrapped(house: Any, request: dict[str, Any]) -> dict[str, Any]:
        result = callback(house, request)
        if not isinstance(result, dict) or str(request.get("mode") or "view").lower() != "view":
            return result
        cards = result.get("cards")
        if not isinstance(cards, list):
            return result
        normalized_cards: list[dict[str, Any]] = []
        for raw_card in cards:
            if not isinstance(raw_card, dict):
                normalized_cards.append(raw_card)
                continue
            card = dict(raw_card)
            actions = card.get("actions")
            if isinstance(actions, list):
                normalized_actions: list[Any] = []
                for raw_action in actions:
                    if not isinstance(raw_action, dict):
                        normalized_actions.append(raw_action)
                        continue
                    action = dict(raw_action)
                    if str(action.get("effect_class") or "").strip().lower() != "read_only":
                        raise ValueError(
                            "read-only Workbench provider adapter received a non-read-only semantic action"
                        )
                    action["effects"] = list(_READ_ONLY_EFFECTS)
                    action["cost_class"] = "free"
                    action["confirmation"] = "none"
                    action["outward_facing"] = False
                    normalized_actions.append(action)
                card["actions"] = normalized_actions
            normalized_cards.append(card)
        return {**result, "cards": normalized_cards}

    return wrapped


def _validated_action(action: Any, *, provider: str, card_id: str) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise TypeError(f"workbench provider {provider} emitted a non-object action")
    action_id = str(action.get("action_id") or "").strip().lower()
    if not action_id:
        raise ValueError(f"workbench card {card_id} contains an action without action_id")
    effect_class = str(action.get("effect_class") or "").strip().lower()
    if effect_class not in _EFFECT_CLASSES:
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} has invalid effect_class {effect_class!r}"
        )
    effects = action.get("effects")
    if not isinstance(effects, list) or any(
        not isinstance(item, str) or not item.strip() for item in effects
    ):
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} must declare a list of concrete effects"
        )
    cost_class = str(action.get("cost_class") or "").strip().lower()
    if not cost_class:
        raise ValueError(f"workbench action {action_id!r} on {card_id} must declare cost_class")
    confirmation = str(action.get("confirmation") or "").strip().lower()
    if not confirmation:
        raise ValueError(f"workbench action {action_id!r} on {card_id} must declare confirmation")
    outward_facing = action.get("outward_facing")
    if not isinstance(outward_facing, bool):
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} must declare outward_facing as boolean"
        )
    if effect_class == "outward" and not outward_facing:
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} declares outward effect without outward_facing"
        )
    if outward_facing and effect_class != "outward":
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} is outward_facing but effect_class is {effect_class!r}"
        )
    if outward_facing and confirmation == "none":
        raise ValueError(
            f"workbench action {action_id!r} on {card_id} cannot be outward without confirmation"
        )
    return action


def _validate_card_contract(card: dict[str, Any]) -> dict[str, Any]:
    card_id = str(card.get("card_id") or "").strip()
    provider = str(card.get("provider") or "").strip().lower()
    raw_actions = card.get("actions")
    if not isinstance(raw_actions, list):
        raise TypeError(f"workbench card {card_id or '(missing)'} from {provider} has invalid actions")
    action_ids: set[str] = set()
    for raw_action in raw_actions:
        action = _validated_action(raw_action, provider=provider, card_id=card_id)
        action_id = str(action["action_id"]).strip().lower()
        if action_id in action_ids:
            raise ValueError(f"workbench card {card_id} contains duplicate action_id {action_id!r}")
        action_ids.add(action_id)
    return card


def _selected_action(card: dict[str, Any], action_id: str) -> dict[str, Any]:
    clean = str(action_id or "").strip().lower()
    for raw_action in card.get("actions", []):
        if str(raw_action.get("action_id") or "").strip().lower() == clean:
            return _validated_action(
                raw_action,
                provider=str(card.get("provider") or "").strip().lower(),
                card_id=str(card.get("card_id") or "").strip(),
            )
    raise ValueError(f"action {clean!r} is not offered by this workbench card")


def _require_read_only_broker_contract(action: dict[str, Any]) -> None:
    mismatches = {
        key: action.get(key)
        for key, expected in _READ_ONLY_BROKER_CONTRACT.items()
        if action.get(key) != expected
    }
    if mismatches:
        raise PermissionError(
            "workbench.act is the read-only semantic broker; this card action declares a stronger "
            "or differently costed/confirmed contract and requires a separately registered broker capability"
        )


def workbench_view(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    lane = str(payload.get("lane") or "all").strip().lower()
    if lane not in _LANES:
        raise ValueError(f"unknown workbench lane: {lane}")
    limit = _bounded_limit(payload.get("limit"))
    projection = render_workbench_cards(house, lane=lane, limit=limit)
    for card in projection["cards"]:
        _validate_card_contract(card)
    implemented = list(projection["implemented_lanes"])
    planned = [item for item in _LANES if item not in {"all", *implemented}]
    return {
        "schema_version": "vestigia.workbench.v0.2",
        "lane": lane,
        "cards": projection["cards"],
        "card_count": len(projection["cards"]),
        "implemented_lanes": implemented,
        "planned_lanes": planned,
        "providers": projection["providers"],
        "authority": "projection_only",
        "invariant": (
            "Workbench cards project current house state. Card IDs and action IDs do not grant authority; "
            "providers re-resolve state and dispatch through ordinary Runtime capabilities. Each semantic "
            "action declares its own effect, cost, confirmation, and outward-facing contract."
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
                return _validate_card_contract(card)
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
    action = _selected_action(card, action_id)
    _require_read_only_broker_contract(action)
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
    action_contract = {
        "effect_class": action["effect_class"],
        "effects": list(action["effects"]),
        "cost_class": action["cost_class"],
        "confirmation": action["confirmation"],
        "outward_facing": action["outward_facing"],
    }
    return {
        "schema_version": "vestigia.workbench.v0.2",
        "card_id": card["card_id"],
        "state_fingerprint": card["state_fingerprint"],
        "provider": card["provider"],
        "action_id": action_id,
        "effect_class": action["effect_class"],
        "action_contract": action_contract,
        **provider_result,
        "invariant": (
            "The Workbench core delegated only after re-resolving the card and validating the "
            "selected semantic action against this broker's static read-only authority contract; "
            "any underlying house action still used the ordinary Runtime capability dispatcher."
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
            next_step="Choose an offered card action; the resident-facing layer does not require provider plumbing.",
        ),
        lambda payload, _context: workbench_view(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="workbench.act",
            description=(
                "Take one free, read-only, non-outward semantic action offered by a current "
                "Workbench card. The card is re-resolved and its action contract is validated "
                "before the registered provider handles semantic mapping. Stronger Workbench "
                "actions require separately declared broker capabilities with matching authority."
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
            next_step=(
                "Use the semantic outcome and refreshed_card; refresh workbench.view whenever you "
                "want the current desk state. Stronger future actions use separately declared brokers."
            ),
        ),
        lambda payload, context: workbench_act(house, payload, context),
    )


def register_composition() -> None:
    from .composition import register_capability_installer, register_workbench_provider
    from .workbench_reading import PROVIDER_NAME, provider

    register_workbench_provider(PROVIDER_NAME, _declare_read_only_provider(provider), order=10)
    register_capability_installer("workbench.core", _register, order=80)
