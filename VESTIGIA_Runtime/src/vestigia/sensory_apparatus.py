from __future__ import annotations

import contextvars
from datetime import UTC, datetime, timedelta
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .sensory_controls import (
    ATTENTION_MODES,
    INGRESS_SIGNALS,
    RETENTION_MODES,
    SENSORY_FIELDS,
    aware,
    configure,
    defaults,
    report,
)
from .sensory_events import explain, forget, list_events
from .utils import sha256_text, utc_now_iso


_NOTHING_TURN: contextvars.ContextVar[str] = contextvars.ContextVar(
    "vestigia_make_nothing_happen_turn", default=""
)


def _table_exists(connection: Any, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _safe_rows(
    house: Any, table: str, sql: str, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    try:
        with house.db.connect() as connection:
            if not _table_exists(connection, table):
                return []
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception:
        return []


def _observatory_core(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from . import resident_controls

    limit = max(1, min(int(payload.get("limit", 20)), 100))
    controls = report(house.config, house.db, house.resident_id)
    listening = list_events(
        house.db,
        house.resident_id,
        resident_controls.ensure_listening_schema,
        limit=limit,
    )
    bells = _safe_rows(
        house,
        "bells",
        """
        SELECT id, title, purpose, strength, status, next_fire_at,
               no_response_required, choose_nothing, updated_at
        FROM bells WHERE resident_id=?
        ORDER BY updated_at DESC LIMIT ?
        """,
        (house.resident_id, limit),
    )
    challenges = _safe_rows(
        house,
        "image_share_challenges",
        """
        SELECT id, image_id, destination_kind, destination_id, status,
               expires_at, consumed_at
        FROM image_share_challenges
        WHERE resident_id=? AND status='pending'
        ORDER BY rowid DESC LIMIT ?
        """,
        (house.resident_id, limit),
    )
    jobs = _safe_rows(
        house,
        "resident_jobs",
        """
        SELECT id, kind, status, updated_at FROM resident_jobs
        WHERE resident_id=? ORDER BY updated_at DESC LIMIT ?
        """,
        (house.resident_id, limit),
    )
    memory_states = _safe_rows(
        house,
        "memories",
        """
        SELECT status, authority_state, COUNT(*) AS count FROM memories
        WHERE resident_id=? GROUP BY status, authority_state
        ORDER BY status, authority_state
        """,
        (house.resident_id,),
    )
    image_jobs: list[dict[str, Any]] = []
    if house.images is not None:
        for item in house.images.jobs(limit=limit):
            image_jobs.append(
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "operation",
                        "status",
                        "created_at",
                        "updated_at",
                        "error_type",
                    )
                }
            )
    panels: dict[str, Any] = {
        "doors": {
            **controls,
            "default_if_untouched": "nothing",
            "authorization_changed": False,
        },
        "listening": listening,
        "bells": bells,
        "pending_challenges": challenges,
        "resident_jobs": jobs,
        "image_jobs": image_jobs,
        "memory_states": memory_states,
        "receipts": house.legible.list_receipts(limit=limit),
        "unresolved_breadcrumbs": house.legible.list_breadcrumbs(limit=limit),
        "outward_action_boundary": {
            "default": "none",
            "creation_is_not_release": True,
            "reading_is_not_authorization": True,
            "silence_is_valid": True,
        },
    }
    section = str(payload.get("section") or "all").strip().lower()
    aliases = {
        "challenges": "pending_challenges",
        "jobs": "resident_jobs",
        "memory": "memory_states",
    }
    if section != "all":
        key = aliases.get(section, section)
        if key not in panels:
            raise ValueError("unknown observatory section")
        panels = {key: panels[key]}
    return {
        "generated_at": utc_now_iso(),
        "observatory": panels,
        "surveillance": False,
        "outward_action": False,
        "instruction": (
            "Inspecting this surface does not acknowledge, resolve, remember, "
            "send, retry, consume, or publish anything."
        ),
    }


def _observatory(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from .composition import render_observatory

    return render_observatory(house, payload, _observatory_core)


def _sensory_control(
    house: Any, payload: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    from . import resident_controls

    mode = str(payload.get("mode") or "inspect").strip().lower()
    if mode in {"inspect", "reset"}:
        state = configure(
            house.config,
            house.db,
            house.resident_id,
            {"mode": mode},
        )
    elif mode == "configure":
        changes = {
            key: value for key, value in payload.items() if key in SENSORY_FIELDS
        }
        state = configure(
            house.config,
            house.db,
            house.resident_id,
            {"mode": "configure", **changes},
        )
    elif mode == "not_this_channel":
        state = report(house.config, house.db, house.resident_id)
        destination = context.get("delivery_target") or {}
        channel_id = str(
            payload.get("channel_id") or destination.get("id") or ""
        ).strip()
        if not channel_id:
            raise ValueError(
                "not_this_channel requires the current authenticated channel or channel_id"
            )
        if str(destination.get("kind") or "").endswith("dm"):
            changes = {"listening_allow_dms": False}
        else:
            excluded = list(
                state["requested"].get("listening_excluded_channel_ids", [])
            )
            if channel_id not in excluded:
                excluded.append(channel_id)
            changes = {"listening_excluded_channel_ids": excluded}
        state = configure(
            house.config,
            house.db,
            house.resident_id,
            {"mode": "configure", **changes},
        )
    elif mode == "listen_until":
        until = str(payload.get("until") or "").strip()
        if not until:
            seconds = int(payload.get("duration_seconds") or 0)
            if seconds <= 0:
                raise ValueError(
                    "listen_until requires until or a positive duration_seconds"
                )
            until = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
        state = configure(
            house.config,
            house.db,
            house.resident_id,
            {
                "mode": "configure",
                "attention_mode": str(
                    payload.get("attention_mode") or "present"
                ).strip().lower(),
                "attention_expires_at": aware(until).isoformat(),
                "attention_after_expiry": str(
                    payload.get("attention_after_expiry") or "deaf"
                ).strip().lower(),
            },
        )
    elif mode == "forget_event":
        forgotten = forget(
            house.db,
            house.resident_id,
            str(payload.get("event_id") or "").strip(),
            resident_controls.ensure_listening_schema,
        )
        return {
            "mode": mode,
            "forgotten": forgotten,
            "effective": report(
                house.config, house.db, house.resident_id
            )["effective"],
            "authorization_changed": False,
            "outward_action": False,
        }
    else:
        raise ValueError(
            "sensory.control mode must be inspect, configure, reset, "
            "not_this_channel, listen_until, or forget_event"
        )
    return {
        "mode": mode,
        **state,
        "recent_events": list_events(
            house.db,
            house.resident_id,
            resident_controls.ensure_listening_schema,
            limit=20,
        ),
        "authorization_changed": False,
        "outward_action": False,
        "boundary": (
            "Resident sensory controls may narrow ingress or reduce consequence. "
            "They cannot widen the operator platform allowlist or grant tool authority."
        ),
    }


def _make_nothing(
    payload: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    turn_id = str(context.get("turn_id") or "").strip()
    if turn_id:
        _NOTHING_TURN.set(turn_id)
    note = str(payload.get("note") or "")
    return {
        "status": "observed_and_left_untouched",
        "disposition": "observe",
        "reference": str(payload.get("reference") or "") or None,
        "note_hash": sha256_text(note) if note else None,
        "outward_action": False,
        "memory_adoption": False,
        "automatic_memory_extraction": False,
        "automatic_curation": False,
        "follow_up_job": False,
        "artifact_export": False,
        "publication": False,
        "authority_changed": False,
        "continuation_required": False,
        "receipt_scope": "minimal_private_durable_receipt",
        "ui_hint": {
            "label": "MAKE NOTHING HAPPEN",
            "style": "big_red_decorated_button",
        },
    }


def _explain_source(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from . import resident_controls
    from .composition import enrich_source_explain

    event_id = str(payload.get("event_id") or "")
    result = explain(
        house.db,
        house.resident_id,
        event_id,
        resident_controls.ensure_listening_schema,
    )
    return enrich_source_explain(house.db, house.resident_id, event_id, result)


def _register_capabilities(house: Any) -> None:
    from . import resident_controls

    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="house.observatory",
            description=(
                "Inspect open doors, attention state, pending challenges, jobs, "
                "receipts, bells, and memory candidates without resolving them."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "house.observatory"},
                    "section": {
                        "type": "string",
                        "enum": [
                            "all",
                            "doors",
                            "listening",
                            "bells",
                            "challenges",
                            "jobs",
                            "image_jobs",
                            "memory",
                            "receipts",
                            "unresolved_breadcrumbs",
                            "outward_action_boundary",
                        ],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {"action": "house.observatory", "section": "all", "after": "continue"},
            ),
        ),
        lambda payload, _context: _observatory(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="source.explain",
            description=(
                "Explain why a listening event reached the resident, what it "
                "retained, and whether it caused a response."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "source.explain"},
                    "event_id": {"type": "string", "minLength": 1},
                    "after": after,
                },
                required=("action", "event_id"),
            ),
            example_envelopes=(
                {"action": "source.explain", "event_id": "listen_...", "after": "continue"},
            ),
        ),
        lambda payload, _context: _explain_source(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="sensory.control",
            description=(
                "Arrange resident attention, retention, signal, DM, and Discord "
                "channel scopes inside operator limits."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "sensory.control"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "inspect",
                            "configure",
                            "reset",
                            "not_this_channel",
                            "listen_until",
                            "forget_event",
                        ],
                    },
                    "attention_mode": {
                        "type": "string",
                        "enum": sorted(ATTENTION_MODES),
                    },
                    "attention_expires_at": {"type": "string"},
                    "attention_after_expiry": {
                        "type": "string",
                        "enum": sorted(ATTENTION_MODES),
                    },
                    "listening_retention": {
                        "type": "string",
                        "enum": sorted(RETENTION_MODES),
                    },
                    "listening_ingress_signals": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(INGRESS_SIGNALS)},
                        "uniqueItems": True,
                    },
                    "listening_channel_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "listening_excluded_channel_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "listening_allow_dms": {"type": "boolean"},
                    "channel_id": {"type": "string"},
                    "until": {"type": "string"},
                    "duration_seconds": {"type": "integer", "minimum": 1},
                    "event_id": {"type": "string"},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "sensory.control",
                    "mode": "configure",
                    "attention_mode": "digest_only",
                    "listening_retention": "short_digest",
                    "after": "continue",
                },
                {
                    "action": "sensory.control",
                    "mode": "not_this_channel",
                    "after": "finish",
                },
            ),
        ),
        lambda payload, context: _sensory_control(house, payload, context),
    )
    house.registry.register(
        CapabilitySpec(
            name="make.nothing.happen",
            description=(
                "Observe and leave untouched without memory adoption, automatic "
                "curation, follow-up work, export, publication, or outward action."
            ),
            effects=("none",),
            confirmation="none",
            default_after="finish",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="attention",
            input_schema=object_schema(
                {
                    "action": {
                        "type": "string",
                        "const": "make.nothing.happen",
                    },
                    "reference": {"type": "string"},
                    "note": {"type": "string", "maxLength": 1000},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "make.nothing.happen",
                    "note": "Seen. Leave it untouched.",
                    "after": "finish",
                },
            ),
        ),
        lambda payload, context: _make_nothing(payload, context),
    )


def _memory_extract_veto(_service: Any, _text: str, turn_id: str) -> bool:
    return _NOTHING_TURN.get() == str(turn_id)


def _curation_veto(_runtime: Any, values: dict[str, Any]) -> bool:
    return _NOTHING_TURN.get() == str(values.get("input_turn_id") or "")


def _receipt_filter(receipts: list[str], *, compact: bool) -> list[str]:
    del compact
    return [
        item
        for item in receipts
        if not item.startswith("tool_action:ok:make.nothing.happen:")
    ]


def register_composition() -> None:
    from .composition import (
        register_capability_installer,
        register_curation_veto,
        register_memory_extract_veto,
        register_receipt_filter,
    )

    register_capability_installer("sensory", _register_capabilities, order=10)
    register_memory_extract_veto(
        "sensory.make_nothing_happen", _memory_extract_veto, order=10
    )
    register_curation_veto(
        "sensory.make_nothing_happen", _curation_veto, order=10
    )
    register_receipt_filter(
        "sensory.make_nothing_happen", _receipt_filter, order=10
    )
