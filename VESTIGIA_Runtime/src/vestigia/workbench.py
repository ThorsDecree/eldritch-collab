from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .utils import sha256_text, stable_json


_LANES = ("all", "continue", "review", "tend", "make", "observe")
_EFFECT_READ_ONLY = "read_only"
_PROVIDER = "reading.bookmark"
_MAX_CARDS = 50


def _bounded_limit(value: Any, *, default: int = 12) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(_MAX_CARDS, max(1, parsed))


def _reading_summary(locator: str, location: dict[str, Any], note: str) -> str:
    if note.strip():
        return note.strip()
    heading = str(location.get("heading") or "").strip()
    chunk = location.get("chunk")
    if heading and chunk is not None:
        return f"Saved at {heading!r}, chunk {chunk}."
    if heading:
        return f"Saved at {heading!r}."
    if chunk is not None:
        return f"Saved at chunk {chunk}."
    if location.get("cursor"):
        return "Saved reading position."
    return f"Saved reading bookmark for {Path(locator).name}."


def _reading_card(house: Any, bookmark: dict[str, Any]) -> dict[str, Any] | None:
    if str(bookmark.get("object_type") or "") != "document":
        return None
    obj = house.legible.object_by_reference(str(bookmark.get("object_id") or ""))
    if not obj:
        return None
    evidence_state = str(obj.get("evidence_state") or bookmark.get("evidence_state") or "")
    if not evidence_state.startswith("verified"):
        return None

    location = bookmark.get("location") or {}
    if not isinstance(location, dict):
        location = {}
    locator = str(obj.get("locator") or bookmark.get("locator") or "")
    bookmark_id = str(bookmark.get("id") or "")
    state = {
        "provider": _PROVIDER,
        "resident_id": house.resident_id,
        "bookmark_id": bookmark_id,
        "bookmark_updated_at": str(bookmark.get("updated_at") or ""),
        "object_id": str(obj.get("id") or ""),
        "locator": locator,
        "content_hash": str(obj.get("content_hash") or ""),
        "evidence_state": evidence_state,
        "location": location,
    }
    fingerprint = sha256_text(stable_json(state))
    label = str(bookmark.get("label") or "").strip()
    title = label or Path(locator).name or "Saved reading"
    provenance = obj.get("provenance") if isinstance(obj.get("provenance"), dict) else {}

    return {
        "card_id": f"wb_{fingerprint[:24]}",
        "state_fingerprint": fingerprint,
        "provider": _PROVIDER,
        "lane": "continue",
        "kind": "reading",
        "title": title,
        "summary": _reading_summary(locator, location, str(bookmark.get("note") or "")),
        "why_now": "saved reading position",
        "last_touched": str(bookmark.get("updated_at") or bookmark.get("created_at") or ""),
        "effect_class": _EFFECT_READ_ONLY,
        "source": {
            "bookmark_id": bookmark_id,
            "object_id": str(obj.get("id") or ""),
            "locator": locator,
            "evidence_state": evidence_state,
            "content_hash": str(obj.get("content_hash") or ""),
            "provenance": provenance,
        },
        "position": dict(location),
        "actions": [
            {
                "action_id": "continue",
                "label": "Continue",
                "effect_class": _EFFECT_READ_ONLY,
                "description": "Open the saved reading position.",
            },
            {
                "action_id": "start_over",
                "label": "Start over",
                "effect_class": _EFFECT_READ_ONLY,
                "description": "Open the same document from its beginning.",
            },
            {
                "action_id": "provenance",
                "label": "Inspect provenance",
                "effect_class": _EFFECT_READ_ONLY,
                "description": "Inspect where the source came from and how it is represented here.",
            },
        ],
    }


def reading_cards(house: Any, *, limit: int) -> list[dict[str, Any]]:
    # Index refresh is the same evidence-refresh path used by list/search/read. It lets
    # a changed or missing file alter the projected card rather than allowing an old
    # card ID to stand in for current state.
    house.refresh_index()
    requested = _bounded_limit(limit)
    bookmarks = house.legible.list_bookmarks(limit=min(200, requested * 4))
    cards: list[dict[str, Any]] = []
    for bookmark in bookmarks:
        card = _reading_card(house, bookmark)
        if card is None:
            continue
        cards.append(card)
        if len(cards) >= requested:
            break
    return cards


def workbench_view(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    lane = str(payload.get("lane") or "all").strip().lower()
    if lane not in _LANES:
        raise ValueError(f"unknown workbench lane: {lane}")
    limit = _bounded_limit(payload.get("limit"))
    cards = reading_cards(house, limit=limit) if lane in {"all", "continue"} else []
    return {
        "schema_version": "vestigia.workbench.v0.1",
        "lane": lane,
        "cards": cards,
        "card_count": len(cards),
        "implemented_lanes": ["continue"],
        "planned_lanes": ["review", "tend", "make", "observe"],
        "authority": "projection_only",
        "invariant": (
            "Workbench cards project current house state. Card IDs and action IDs do not grant authority; "
            "actions are re-resolved and dispatched through ordinary Runtime capabilities."
        ),
    }


def _current_card(house: Any, card_id: str) -> dict[str, Any]:
    clean = str(card_id or "").strip()
    if not clean:
        raise ValueError("card_id is required")
    # Search the bounded active bookmark set rather than accepting hidden locator or
    # capability parameters from the caller. If the underlying state changed, the
    # fingerprint changes and the old card deliberately stops resolving.
    for card in reading_cards(house, limit=_MAX_CARDS):
        if card["card_id"] == clean:
            return card
    raise KeyError(
        "workbench card is stale or unavailable; refresh workbench.view before acting"
    )


def _dispatch_semantic_action(
    house: Any,
    card: dict[str, Any],
    action_id: str,
    *,
    max_tokens: int,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    source = card["source"]
    inner_context = dict(context)
    inner_context["source_envelope"] = "WORKBENCH"
    turn_id = str(context.get("turn_id") or "") or None

    if action_id == "continue":
        underlying_action = "bookmark.open"
        result = house.dispatch(
            {
                "action": underlying_action,
                "bookmark_id": source["bookmark_id"],
                "max_tokens": max_tokens,
            },
            turn_id=turn_id,
            context=inner_context,
        )
        return underlying_action, result
    if action_id == "start_over":
        underlying_action = "read"
        result = house.dispatch(
            {
                "action": underlying_action,
                "path": source["locator"],
                "chunk": 0,
                "max_tokens": max_tokens,
            },
            turn_id=turn_id,
            context=inner_context,
        )
        return underlying_action, result
    if action_id == "provenance":
        underlying_action = "object.provenance"
        result = house.dispatch(
            {
                "action": underlying_action,
                "reference": source["object_id"],
            },
            turn_id=turn_id,
            context=inner_context,
        )
        return underlying_action, result
    raise ValueError(f"unsupported semantic workbench action: {action_id}")


def workbench_act(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    card = _current_card(house, str(payload.get("card_id") or ""))
    action_id = str(payload.get("action_id") or "").strip().lower()
    available = {str(item["action_id"]) for item in card["actions"]}
    if action_id not in available:
        raise ValueError(f"action {action_id!r} is not offered by this workbench card")
    try:
        max_tokens = int(payload.get("max_tokens", 3000))
    except (TypeError, ValueError):
        max_tokens = 3000
    max_tokens = min(4000, max(100, max_tokens))

    underlying_action, result = _dispatch_semantic_action(
        house,
        card,
        action_id,
        max_tokens=max_tokens,
        context=context,
    )
    return {
        "schema_version": "vestigia.workbench.v0.1",
        "card_id": card["card_id"],
        "state_fingerprint": card["state_fingerprint"],
        "action_id": action_id,
        "effect_class": card["effect_class"],
        "underlying_action": underlying_action,
        "underlying_receipt_id": result.get("receipt_id"),
        "result": result,
        "outward_effect": "none",
        "memory_promotion": False,
        "identity_effect": False,
        "invariant": "The semantic action used the ordinary Runtime capability dispatcher.",
    }


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="workbench.view",
            description=(
                "Show a bounded resident-facing Workbench of current useful affordances. "
                "The initial slice projects durable reading bookmarks into Continue cards."
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
            next_step="Choose an offered card action with workbench.act; no raw bookmark syntax is required.",
        ),
        lambda payload, _context: workbench_view(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="workbench.act",
            description=(
                "Take one semantic action offered by a current Workbench card. The card is "
                "re-resolved first, then the underlying operation goes through the ordinary capability dispatcher."
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
                    "action_id": {
                        "type": "string",
                        "enum": ["continue", "start_over", "provenance"],
                    },
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
            related_actions=("workbench.view", "bookmark.open", "read", "object.provenance"),
            next_step="Use the returned material, then refresh workbench.view whenever you want the current desk state.",
        ),
        lambda payload, context: workbench_act(house, payload, context),
    )


def register_composition() -> None:
    from .composition import register_capability_installer

    register_capability_installer("workbench.core", _register, order=80)
