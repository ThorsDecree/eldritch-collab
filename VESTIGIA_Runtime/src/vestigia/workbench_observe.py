from __future__ import annotations

from typing import Any

from .utils import sha256_text, stable_json


PROVIDER_NAME = "observe.runtime"
_EFFECTS = ["database:read"]


def _snapshot(house: Any) -> dict[str, Any]:
    state = house.db.current_state(house.resident_id) or "ORIENTATION"
    budget = house.private_turn_budget()
    capabilities = house.registry.describe()
    return {
        "runtime_state": state,
        "private_turn_budget": budget,
        "capability_count": len(capabilities),
        "enabled_capability_count": sum(
            1 for item in capabilities if bool(item.get("enabled")) and bool(item.get("callable_now"))
        ),
    }


def runtime_status_card(house: Any) -> dict[str, Any]:
    snapshot = _snapshot(house)
    fingerprint = sha256_text(stable_json(snapshot))
    return {
        "card_id": f"wb_{fingerprint[:24]}",
        "state_fingerprint": fingerprint,
        "provider": PROVIDER_NAME,
        "projection_kind": "observe.runtime_status",
        "lane": "observe",
        "kind": "runtime_status",
        "title": "Runtime status",
        "summary": (
            f"State {snapshot['runtime_state']} · "
            f"{snapshot['enabled_capability_count']}/{snapshot['capability_count']} capabilities callable."
        ),
        "why_now": "live house status",
        "last_touched": "",
        "effect_class": "read_only",
        "snapshot": snapshot,
        "actions": [
            {
                "action_id": "inspect",
                "label": "Inspect status",
                "description": "Open the resident-private Runtime status view.",
                "effect_class": "read_only",
                "effects": list(_EFFECTS),
                "cost_class": "free",
                "confirmation": "none",
                "outward_facing": False,
            }
        ],
    }


def provider(house: Any, request: dict[str, Any]) -> dict[str, Any]:
    mode = str(request.get("mode") or "view").strip().lower()
    if mode == "view":
        lane = str(request.get("lane") or "all").strip().lower()
        limit = int(request.get("limit") or 0)
        cards = [runtime_status_card(house)] if limit > 0 and lane in {"all", "observe"} else []
        return {
            "implemented_lanes": ["observe"],
            "cards": cards,
        }
    if mode != "act":
        raise ValueError(f"unsupported Workbench provider mode: {mode}")

    card = request.get("card")
    if not isinstance(card, dict) or card.get("provider") != PROVIDER_NAME:
        raise ValueError("Observe provider received a foreign Workbench card")
    action_id = str(request.get("action_id") or "").strip().lower()
    if action_id != "inspect":
        raise ValueError(f"unsupported Observe semantic action: {action_id}")

    context = request.get("context") if isinstance(request.get("context"), dict) else {}
    inner_context = dict(context)
    inner_context["source_envelope"] = "WORKBENCH"
    turn_id = str(context.get("turn_id") or "") or None
    result = house.dispatch(
        {"action": "status"},
        turn_id=turn_id,
        context=inner_context,
    )
    return {
        "underlying_action": "status",
        "underlying_receipt_id": result.get("receipt_id"),
        "outcome": result,
        "underlying_result": result,
        "refreshed_card": runtime_status_card(house),
        "outward_effect": "none",
        "memory_promotion": False,
        "identity_effect": False,
    }
