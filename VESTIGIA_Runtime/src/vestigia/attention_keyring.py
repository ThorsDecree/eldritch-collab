from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .attention_keyring_discord import consume_wake_context
from .attention_keyring_store import (
    CORRECTION_KINDS,
    PREFERENCE_KINDS,
    QUIET_PRESETS,
    activate_quiet,
    cancel_quiet,
    create_preference,
    delete_preference,
    inspect_wake_receipt,
    list_corrections,
    list_keyring_receipts,
    list_preferences,
    list_wake_receipts,
    open_wake_receipt,
    quiet_state,
    record_correction,
    release_quiet,
    review_correction,
    semantic_budget_snapshot,
    update_preference,
    complete_wake_receipt,
)
from .attention_router import (
    by_listening_event,
    correct as correct_router_event,
    list_events as list_router_events,
    metrics as router_metrics,
    operator_settings,
    report as router_report,
)
from .capabilities import CapabilitySpec, object_schema
from .sensory_controls import report as sensory_report



def _controls(house: Any) -> dict[str, Any]:
    from .resident_controls import load_resident_controls

    values = dict(load_resident_controls(house.config, house.db, house.resident_id))
    values.update(sensory_report(house.config, house.db, house.resident_id)["effective"])
    return values


def _quiet(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "inspect").strip().lower()
    controls = _controls(house)
    actor = f"resident:{house.resident_id}"
    if mode == "inspect":
        result = {"quiet": quiet_state(house.db, house.resident_id, controls), "receipt": None}
    elif mode == "activate":
        expires_at = str(payload.get("expires_at") or "").strip() or None
        if expires_at is None and payload.get("duration_seconds") is not None:
            seconds = max(1, int(payload["duration_seconds"]))
            maximum = max(
                60,
                int(house.config.get("attention_keyring.max_quiet_seconds", 604800)),
            )
            seconds = min(seconds, maximum)
            expires_at = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
        custom = {
            key: payload[key]
            for key in (
                "ambient_open",
                "mention_open",
                "reply_open",
                "command_open",
                "dm_open",
            )
            if key in payload
        }
        result = activate_quiet(
            house.db,
            house.resident_id,
            controls,
            preset=str(payload.get("preset") or "ambient_closed"),
            expires_at=expires_at,
            custom=custom,
            actor=actor,
            reason=str(payload.get("reason") or "explicit resident quiet request"),
        )
    elif mode == "cancel":
        result = cancel_quiet(
            house.db, house.resident_id, controls, actor=actor
        )
    elif mode == "release":
        result = release_quiet(
            house.db, house.resident_id, controls, actor=actor
        )
    else:
        raise ValueError("attention.quiet mode must be inspect, activate, cancel, or release")
    return {
        "mode": mode,
        **result,
        "authority_changed": False,
        "outward_action": False,
        "boundary": (
            "Quiet mode narrows attention. Expiry or cancellation restores only the "
            "captured baseline and keeps that restoration locked until explicit release."
        ),
    }


def _preference(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "list").strip().lower()
    actor = f"resident:{house.resident_id}"
    limits = operator_settings(house.config)
    if mode == "list":
        result: Any = list_preferences(
            house.db,
            house.resident_id,
            status=str(payload.get("status") or "active") or None,
            limit=int(payload.get("limit") or 200),
        )
    elif mode == "create":
        active_count = len(
            list_preferences(house.db, house.resident_id, status="active", limit=500)
        )
        maximum = max(
            1,
            int(house.config.get("attention_keyring.max_preferences", 128)),
        )
        if active_count >= maximum:
            raise ValueError("attention preference ledger is at the operator maximum")
        term = str(payload.get("term") or "")
        if len(term) > int(limits["max_term_length"]):
            raise ValueError("attention preference term is too long")
        result = create_preference(
            house.db,
            house.resident_id,
            kind=str(payload.get("kind") or ""),
            term=term,
            interface=str(payload.get("interface") or "all"),
            channel_id=str(payload.get("channel_id") or "").strip() or None,
            expires_at=str(payload.get("expires_at") or "").strip() or None,
            actor=actor,
            reason=str(payload.get("reason") or "explicit resident preference"),
        )
    elif mode == "update":
        term = payload.get("term")
        if term is not None and len(str(term)) > int(limits["max_term_length"]):
            raise ValueError("attention preference term is too long")
        result = update_preference(
            house.db,
            house.resident_id,
            str(payload.get("preference_id") or "").strip(),
            term=None if term is None else str(term),
            kind=str(payload.get("kind") or "").strip() or None,
            interface=str(payload.get("interface") or "").strip() or None,
            channel_id=(
                str(payload.get("channel_id")).strip()
                if "channel_id" in payload
                else None
            ),
            expires_at=(
                str(payload.get("expires_at"))
                if "expires_at" in payload
                else None
            ),
            status=str(payload.get("status") or "").strip() or None,
            actor=actor,
            reason=str(payload.get("reason") or "explicit resident revision"),
        )
    elif mode == "delete":
        result = delete_preference(
            house.db,
            house.resident_id,
            str(payload.get("preference_id") or "").strip(),
            actor=actor,
        )
    else:
        raise ValueError("attention.preference mode must be list, create, update, or delete")
    return {
        "mode": mode,
        "result": result,
        "explicit_preference": True,
        "inferred_from_memory": False,
        "authority_changed": False,
        "outward_action": False,
    }


def _wake_receipts(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "list").strip().lower()
    if mode == "list":
        result: Any = list_wake_receipts(
            house.db, house.resident_id, limit=int(payload.get("limit") or 50)
        )
    elif mode == "inspect":
        result = inspect_wake_receipt(
            house.db,
            house.resident_id,
            str(payload.get("wake_id") or "").strip(),
        )
    else:
        raise ValueError("attention.wake.receipts mode must be list or inspect")
    return {
        "mode": mode,
        "result": result,
        "included_is_influenced": False,
        "authority_changed": False,
        "outward_action": False,
    }


def _correction(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "list").strip().lower()
    if mode == "list":
        result: Any = list_corrections(
            house.db,
            house.resident_id,
            status=str(payload.get("status") or "awaiting_review") or None,
            limit=int(payload.get("limit") or 100),
        )
    elif mode == "label":
        kind = str(payload.get("kind") or "").strip().lower()
        route_map = {
            "should_ignore": "ignore",
            "worth_inviting": "invite",
            "fixture_or_quote": "ignore",
            "keyword_too_broad": None,
        }
        route = route_map.get(kind)
        router_event_id = str(payload.get("event_id") or "").strip()
        result = record_correction(
            house.db,
            house.resident_id,
            router_event_id=router_event_id,
            kind=kind,
            proposed_route=route,
            term=str(payload.get("term") or "").strip() or None,
            note=str(payload.get("note") or ""),
        )
        if route:
            correct_router_event(
                house.db,
                house.resident_id,
                router_event_id,
                route=route,
                note=str(payload.get("note") or ""),
            )
    elif mode == "review":
        result = review_correction(
            house.db,
            house.resident_id,
            str(payload.get("correction_id") or "").strip(),
            status=str(payload.get("status") or "reviewed"),
        )
    else:
        raise ValueError("attention.correction mode must be list, label, or review")
    return {
        "mode": mode,
        "result": result,
        "correction_is_labeled_evidence": True,
        "automatic_retraining": False,
        "live_routing_changed": False,
        "authority_changed": False,
        "outward_action": False,
    }


def _dashboard(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(payload.get("limit") or 20), 100))
    controls = _controls(house)
    sensory = sensory_report(house.config, house.db, house.resident_id)
    quiet = quiet_state(house.db, house.resident_id, controls)
    preferences = list_preferences(
        house.db, house.resident_id, status="active", limit=200
    )
    router = router_report(
        house.config,
        house.db,
        house.resident_id,
        listening_controls=controls,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "current_sensory_state": sensory,
        "quiet_mode": quiet,
        "platform_reachability": {
            "allowed_channel_ids": list(
                house.config.get("discord.allowed_channel_ids", []) or []
            ),
            "allow_dms": bool(house.config.get("discord.allow_dms", True)),
            "meaning": "Platform reachability is not resident mental availability.",
        },
        "resident_attention_scope": {
            "included_channel_ids": list(
                sensory["effective"].get("listening_channel_ids", [])
            ),
            "excluded_channel_ids": list(
                sensory["effective"].get("listening_excluded_channel_ids", [])
            ),
            "ingress_signals": list(
                sensory["effective"].get("listening_ingress_signals", [])
            ),
            "allow_dms": bool(
                sensory["effective"].get("listening_allow_dms", True)
            ),
        },
        "attention_preferences": preferences,
        "router_controls": router,
        "router_metrics_24h": router_metrics(
            house.db, house.resident_id, hours=24
        ),
        "semantic_budget": semantic_budget_snapshot(
            house.db, house.resident_id, house.config
        ),
        "recent_shadow_decisions": list_router_events(
            house.db, house.resident_id, limit=limit
        ),
        "recent_wake_receipts": list_wake_receipts(
            house.db, house.resident_id, limit=limit
        ),
        "corrections_awaiting_review": list_corrections(
            house.db,
            house.resident_id,
            status="awaiting_review",
            limit=limit,
        ),
        "recent_keyring_receipts": list_keyring_receipts(
            house.db, house.resident_id, limit=limit
        ),
        "read_only": True,
        "outward_action": False,
        "authority_changed": False,
        "instruction": (
            "This panel explains reachability and routing evidence. It does not claim "
            "that included context influenced the resident."
        ),
    }


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="attention.quiet",
            description=(
                "Inspect, activate, restore, or explicitly release a resident quiet "
                "window without widening platform authority."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "attention.quiet"},
                    "mode": {
                        "type": "string",
                        "enum": ["inspect", "activate", "cancel", "release"],
                    },
                    "preset": {"type": "string", "enum": sorted(QUIET_PRESETS)},
                    "expires_at": {"type": "string"},
                    "duration_seconds": {"type": "integer", "minimum": 1},
                    "ambient_open": {"type": "boolean"},
                    "mention_open": {"type": "boolean"},
                    "reply_open": {"type": "boolean"},
                    "command_open": {"type": "boolean"},
                    "dm_open": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 500},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.quiet",
                    "mode": "activate",
                    "preset": "ambient_closed",
                    "duration_seconds": 1800,
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _quiet(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="attention.preference",
            description=(
                "Maintain explicit resident-owned always-notice, usually-ignore, and "
                "semantic-check-only attention preferences with provenance and scope."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "attention.preference"},
                    "mode": {
                        "type": "string",
                        "enum": ["list", "create", "update", "delete"],
                    },
                    "preference_id": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(PREFERENCE_KINDS)},
                    "term": {"type": "string", "minLength": 1, "maxLength": 80},
                    "interface": {"type": "string", "enum": ["all", "discord"]},
                    "channel_id": {"type": "string"},
                    "expires_at": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["active", "disabled", "deleted"],
                    },
                    "reason": {"type": "string", "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.preference",
                    "mode": "create",
                    "kind": "semantic_check_only",
                    "term": "show Liora this",
                    "interface": "discord",
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _preference(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="attention.wake.receipts",
            description=(
                "Inspect concise evidence explaining why live resident turns opened, "
                "without claiming that included context influenced the response."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "attention.wake.receipts"},
                    "mode": {"type": "string", "enum": ["list", "inspect"]},
                    "wake_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.wake.receipts",
                    "mode": "list",
                    "limit": 20,
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _wake_receipts(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="attention.correction",
            description=(
                "Label router decisions as ignore, invite, broad-keyword, or fixture "
                "evidence without automatic retraining or rule mutation."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "attention.correction"},
                    "mode": {"type": "string", "enum": ["list", "label", "review"]},
                    "event_id": {"type": "string"},
                    "correction_id": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(CORRECTION_KINDS)},
                    "term": {"type": "string", "maxLength": 80},
                    "note": {"type": "string", "maxLength": 1000},
                    "status": {
                        "type": "string",
                        "enum": ["awaiting_review", "reviewed", "dismissed"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "attention.correction",
                    "mode": "label",
                    "event_id": "router_...",
                    "kind": "fixture_or_quote",
                    "note": "This was quoted test data.",
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _correction(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="house.attention_dashboard",
            description=(
                "Inspect current sensory state, quiet locks, preference ledger, wake "
                "receipts, router budgets, decisions, and pending corrections."
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
                        "const": "house.attention_dashboard",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "house.attention_dashboard",
                    "limit": 20,
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _dashboard(house, payload),
    )


def _chat_middleware(
    runtime: Any, message: Any, model_route: str, next_call: Any
) -> Any:
    wake = consume_wake_context()
    if message.interface != "discord" or not wake:
        return next_call(message, model_route)
    listening_event_id = str(message.metadata.get("listening_event_id") or "").strip()
    router_event = (
        by_listening_event(runtime.db, runtime.resident_id, listening_event_id)
        if listening_event_id
        else None
    )
    context_ids = [
        str(item)
        for item in [
            message.external_id,
            *list(message.metadata.get("ambient_message_ids") or []),
        ]
        if item
    ]
    opened = open_wake_receipt(
        runtime.db,
        resident_id=runtime.resident_id,
        room_id=runtime.room_id,
        interface="discord",
        channel_id=str(
            message.metadata.get("channel_id") or wake.get("channel_id") or ""
        ),
        message_id=str(
            message.external_id
            or message.metadata.get("triggering_message_id")
            or ""
        ),
        listening_event_id=listening_event_id or None,
        signal_kind=str(wake.get("signal_kind") or "unknown"),
        reason_code=str(wake.get("reason_code") or "inherited_live_route"),
        live_route=str(wake.get("live_route") or "invite"),
        router_event=router_event,
        included_context_ids=context_ids,
    )
    try:
        result = next_call(message, model_route)
    except Exception:
        complete_wake_receipt(
            runtime.db,
            runtime.resident_id,
            str(opened["id"]),
            turn_id=None,
            status="runtime_error",
            response_prepared=None,
        )
        raise
    prepared = bool(
        result.text or result.outbound_attachments or result.outbound_reactions
    )
    complete_wake_receipt(
        runtime.db,
        runtime.resident_id,
        str(opened["id"]),
        turn_id=str(result.turn_id),
        status="runtime_suppressed" if result.suppressed else "completed",
        response_prepared=prepared,
    )
    return result


def _observatory_panel(
    house: Any, payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    panels = result.get("observatory")
    if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":
        panels["attention_dashboard"] = _dashboard(house, {"limit": 10})
    return result


def register_composition() -> None:
    from .composition import (
        register_capability_installer,
        register_chat_middleware,
        register_observatory_panel,
    )

    register_capability_installer("attention.keyring", _register, order=30)
    register_chat_middleware(
        "attention.keyring.wake_receipt", _chat_middleware, order=30
    )
    register_observatory_panel(
        "attention.keyring", _observatory_panel, order=30
    )
