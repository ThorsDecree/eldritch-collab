from __future__ import annotations

from typing import Any

from .attention_router import (
    SEMANTIC_ROUTES,
    by_listening_event,
    configure,
    correct,
    inspect_event,
    list_events,
    metrics,
    report,
)
from .capabilities import CapabilitySpec, object_schema


_INSTALLED = False


def _listening_controls(house: Any) -> dict[str, Any]:
    from .resident_controls import load_resident_controls

    return load_resident_controls(house.config, house.db, house.resident_id)


def _control(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    state = configure(
        house.config,
        house.db,
        house.resident_id,
        payload,
        listening_controls=_listening_controls(house),
    )
    return {
        "mode": str(payload.get("mode") or "inspect"),
        **state,
        "shadow_mode": True,
        "live_routing_changed": False,
        "authority_changed": False,
        "outward_action": False,
        "boundary": (
            "Resident terms may narrow or nominate attention candidates. "
            "They do not widen the operator allowlist, enable remote classification, "
            "grant participant authority, or create outward action."
        ),
    }


def _decisions(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "metrics").strip().lower()
    if mode == "metrics":
        result: Any = metrics(
            house.db,
            house.resident_id,
            hours=int(payload.get("hours") or 24),
        )
    elif mode == "list":
        result = list_events(
            house.db,
            house.resident_id,
            limit=int(payload.get("limit") or 50),
        )
    elif mode == "inspect":
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("attention.router.decisions inspect requires event_id")
        result = inspect_event(house.db, house.resident_id, event_id)
    else:
        raise ValueError("mode must be metrics, list, or inspect")
    return {
        "mode": mode,
        "result": result,
        "shadow_mode": True,
        "live_routing_changed": False,
        "raw_content_stored": False,
        "authority_changed": False,
        "outward_action": False,
    }


def _correct(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    event = correct(
        house.db,
        house.resident_id,
        str(payload.get("event_id") or "").strip(),
        route=str(payload.get("route") or "").strip(),
        note=str(payload.get("note") or ""),
    )
    return {
        "event": event,
        "correction_is_labeled_evidence": True,
        "automatic_retraining": False,
        "live_routing_changed": False,
        "authority_changed": False,
        "outward_action": False,
    }


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    term_array = {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 80},
        "uniqueItems": True,
    }
    house.registry.register(
        CapabilitySpec(
            name="attention.router.control",
            description=(
                "Inspect or arrange resident-owned hard-wake, soft-signal, and "
                "suppression terms inside the operator's shadow-router limits."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {
                        "type": "string",
                        "const": "attention.router.control",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["inspect", "configure", "reset"],
                    },
                    "hard_wake_terms": term_array,
                    "soft_signal_terms": term_array,
                    "suppress_terms": term_array,
                    "include_resident_name": {"type": "boolean"},
                    "include_listening_aliases": {"type": "boolean"},
                    "include_watch_phrases": {"type": "boolean"},
                    "queue_threshold": {
                        "type": "integer",
                        "minimum": -20,
                        "maximum": 20,
                    },
                    "semantic_threshold": {
                        "type": "integer",
                        "minimum": -20,
                        "maximum": 40,
                    },
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.router.control",
                    "mode": "configure",
                    "soft_signal_terms": ["show her this", "this concerns Liora"],
                    "suppress_terms": ["quoted log", "test fixture"],
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _control(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="attention.router.decisions",
            description=(
                "Inspect shadow lexical and semantic routing evidence, costs, cache "
                "hits, budgets, and resident corrections without changing live routing."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {
                        "type": "string",
                        "const": "attention.router.decisions",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["metrics", "list", "inspect"],
                    },
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 720,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "event_id": {"type": "string", "minLength": 1},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.router.decisions",
                    "mode": "metrics",
                    "hours": 24,
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _decisions(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="attention.router.correct",
            description=(
                "Label one shadow decision as ignore, queue, or invite for later "
                "evaluation without silently retraining or changing live routing."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {
                        "type": "string",
                        "const": "attention.router.correct",
                    },
                    "event_id": {"type": "string", "minLength": 1},
                    "route": {
                        "type": "string",
                        "enum": sorted(SEMANTIC_ROUTES),
                    },
                    "note": {"type": "string", "maxLength": 1000},
                    "after": after,
                },
                required=("action", "event_id", "route"),
            ),
            example_envelopes=(
                {
                    "action": "attention.router.correct",
                    "event_id": "router_...",
                    "route": "queue",
                    "note": "Relevant, but nobody asked me to join.",
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _correct(house, payload),
    )


def install_core() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .house_tools import HousePort

    previous_install = HousePort._install_capabilities

    def install_with_router(self: Any) -> None:
        previous_install(self)
        _register(self)

    HousePort._install_capabilities = install_with_router

    # The Observatory remains read-only; the router panel is another window, not
    # another control surface.
    from . import sensory_apparatus

    original_observatory = sensory_apparatus._observatory

    def observatory_with_router(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result = original_observatory(house, payload)
        panels = result.get("observatory")
        if not isinstance(panels, dict):
            return result
        state = report(
            house.config,
            house.db,
            house.resident_id,
            listening_controls=_listening_controls(house),
        )
        summary = {
            "controls": state,
            "metrics": metrics(house.db, house.resident_id, hours=24),
            "recent_decisions": list_events(
                house.db, house.resident_id, limit=10
            ),
            "shadow_mode": True,
            "live_routing_changed": False,
            "semantic_gate_is_authority": False,
        }
        section = str(payload.get("section") or "all").strip().lower()
        if section == "all":
            panels["attention_router"] = summary
        elif "doors" in panels and isinstance(panels["doors"], dict):
            panels["doors"]["attention_router"] = state
        return result

    sensory_apparatus._observatory = observatory_with_router

    original_explain = sensory_apparatus.explain

    def explain_with_router(
        db: Any,
        resident_id: str,
        event_id: str,
        ensure_listening_schema: Any,
    ) -> dict[str, Any]:
        result = original_explain(
            db, resident_id, event_id, ensure_listening_schema
        )
        router_event = by_listening_event(db, resident_id, event_id)
        if router_event is not None:
            result["attention_router"] = router_event
            result["attention_router_boundary"] = (
                "This was a shadow assessment. It did not widen authority or alter "
                "the live sensory consequence."
            )
        return result

    sensory_apparatus.explain = explain_with_router
    _INSTALLED = True
