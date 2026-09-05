from __future__ import annotations

from typing import Any

from .capabilities import CapabilitySpec, object_schema, validate_instance
from .composition import register_capability_installer
from .utils import sha256_text, stable_json, utc_now_iso


KEYRING_SCHEMA_VERSION = "vestigia.capability-keyring.v0.1"

AUTHORITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_authority_state (
    resident_id TEXT PRIMARY KEY,
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

READ_EFFECTS = {
    "filesystem:read",
    "database:read",
}
PREPARE_EFFECTS = {
    "database:write_pending_draft",
    "database:write_low_authority",
    "database:read_write_low_authority",
    "database:cache_write",
    "database:audit_write",
}
ACT_EFFECTS = {
    "filesystem:write_workspace",
    "filesystem:write",
    "database:write",
    "database:control",
    "network:metered",
    "network:conditional",
    "outward_reaction",
    "outward_attachment",
}
SAFE_TARGET_KEYS = (
    "operation",
    "path",
    "destination",
    "scope",
    "reference",
    "object_id",
    "image_id",
    "memory_id",
    "note_id",
    "receipt_id",
    "bookmark_id",
    "batch_id",
    "job_id",
    "draft_id",
    "bell_id",
    "target",
)


def _effect_class(effects: list[str] | tuple[str, ...]) -> tuple[str, list[str]]:
    normalized = {str(item) for item in effects}
    unknown = sorted(
        item
        for item in normalized
        if item not in READ_EFFECTS
        and item not in PREPARE_EFFECTS
        and item not in ACT_EFFECTS
        and not item.startswith("outward")
    )
    if any(item in ACT_EFFECTS or item.startswith("outward") for item in normalized):
        return "act", unknown
    if any(item in PREPARE_EFFECTS for item in normalized):
        return "prepare", unknown
    if normalized and normalized <= READ_EFFECTS:
        return "perceive", unknown
    if not normalized:
        return "unknown", []
    return "unknown", unknown


def _authority_epoch(house: Any) -> int:
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT authority_epoch FROM capability_authority_state WHERE resident_id=?",
            (house.resident_id,),
        ).fetchone()
    if not row:
        raise RuntimeError("capability authority state is unavailable")
    return int(row["authority_epoch"])


def _principal(house: Any, context: dict[str, Any]) -> dict[str, Any]:
    deployment_id = str(context.get("mcp_deployment_id") or "").strip() or None
    interface = str(context.get("interface") or "runtime").strip().lower() or "runtime"
    return {
        "kind": "resident",
        "principal_id": f"resident:{house.resident_id}",
        "resident_id": house.resident_id,
        "room_id": house.room_id,
        "route": {
            "interface": interface,
            "mcp_deployment_id": deployment_id,
        },
    }


def _target_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in SAFE_TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value not in (None, ""):
                result[key] = value
    path = str(result.get("path") or "")
    if path:
        result["scope_class"] = "workspace" if path.startswith("workspace/") else "named_path"
    return result


def _surface_digest(house: Any) -> tuple[int, str]:
    contracts = house.registry.describe()
    payload = [
        {
            "name": item.get("name"),
            "schema_version": item.get("schema_version"),
            "effects": item.get("effects", []),
            "confirmation": item.get("confirmation"),
            "outward_facing": item.get("outward_facing", False),
            "callable_now": item.get("callable_now", False),
            "dispatchable_via_tool_action": item.get("dispatchable_via_tool_action", True),
            "input_schema": item.get("input_schema", {}),
        }
        for item in sorted(contracts, key=lambda value: str(value.get("name", "")))
    ]
    return len(payload), sha256_text(stable_json(payload))


def _contract_preflight(
    house: Any,
    capability: str,
    arguments: dict[str, Any] | None = None,
    *,
    validate_payload: bool = False,
) -> dict[str, Any]:
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return {
            "capability": "",
            "known": False,
            "decision": "deny",
            "decision_layer": "runtime_contract_preflight",
            "reasons": ["capability_name_missing"],
            "schema_valid": False if validate_payload else None,
        }

    try:
        contract = house.registry.describe(normalized)[0]
    except ValueError:
        return {
            "capability": normalized,
            "known": False,
            "decision": "deny",
            "decision_layer": "runtime_contract_preflight",
            "reasons": ["unknown_runtime_capability"],
            "schema_valid": False if validate_payload else None,
        }

    effects = [str(item) for item in contract.get("effects", [])]
    effect_class, unknown_effects = _effect_class(effects)
    reasons: list[str] = []
    if not bool(contract.get("enabled", False)):
        decision = "deny"
        reasons.append("capability_disabled")
    elif not bool(contract.get("schema_complete", False)):
        decision = "deny"
        reasons.append("capability_schema_incomplete")
    elif not bool(contract.get("callable_now", False)):
        decision = "deny"
        reasons.append("capability_not_callable_now")
    elif not bool(contract.get("dispatchable_via_tool_action", True)):
        decision = "deny"
        reasons.append("capability_uses_non_tool_action_envelope")
    elif str(contract.get("confirmation") or "none") != "none":
        decision = "confirm"
        reasons.append("capability_declares_focused_confirmation_policy")
    else:
        decision = "allow"
        reasons.append("current_runtime_contract_is_confirmation_free")

    supplied = dict(arguments or {})
    schema_valid: bool | None = None
    schema_error: str | None = None
    candidate: dict[str, Any] | None = None
    if validate_payload or arguments is not None:
        if "action" in supplied or "after" in supplied:
            schema_valid = False
            schema_error = "Keyring owns the target action and after fields"
        else:
            candidate = {
                "action": normalized,
                **supplied,
                "after": str(contract.get("default_after") or "continue"),
            }
            try:
                validate_instance(candidate, contract.get("input_schema", {}))
                schema_valid = True
            except ValueError as exc:
                schema_valid = False
                schema_error = str(exc)
        if schema_valid is False:
            decision = "deny"
            reasons.append("candidate_payload_failed_runtime_schema")

    confirmation = str(contract.get("confirmation") or "none")
    legacy_unkeyed_authority = bool(
        decision == "allow" and effect_class == "act" and confirmation == "none"
    )
    keyring_gap = bool(legacy_unkeyed_authority or (decision == "allow" and effect_class == "unknown"))
    if legacy_unkeyed_authority:
        reasons.append("act_effect_is_currently_admitted_without_general_keyring_gate")
    elif keyring_gap:
        reasons.append("effect_classification_requires_future_keyring_migration")

    if effect_class == "perceive":
        reversible_boundary = "read_dispatch"
    elif effect_class == "prepare":
        reversible_boundary = "runtime_private_staged_state"
    elif bool(contract.get("outward_facing", False)):
        reversible_boundary = "before_external_dispatch"
    elif effect_class == "act":
        reversible_boundary = "before_target_mutation"
    else:
        reversible_boundary = "unclassified_effect_boundary"

    result: dict[str, Any] = {
        "capability": normalized,
        "known": True,
        "decision": decision,
        "decision_layer": "runtime_contract_preflight",
        "reasons": reasons,
        "effect_class": effect_class,
        "effects": effects,
        "unknown_effects": unknown_effects,
        "confirmation": confirmation,
        "outward_facing": bool(contract.get("outward_facing", False)),
        "callable_now": bool(contract.get("callable_now", False)),
        "dispatchable_via_tool_action": bool(contract.get("dispatchable_via_tool_action", True)),
        "schema_valid": schema_valid,
        "schema_error": schema_error,
        "target": _target_summary(supplied),
        "legacy_unkeyed_authority": legacy_unkeyed_authority,
        "keyring_gap": keyring_gap,
        "last_reversible_boundary": reversible_boundary,
        "focused_authorizer_executed": False,
        "target_handler_executed": False,
        "preview_is_authorization": False,
        "approval_created": False,
        "final_dispatch_recheck_implemented": False,
        "final_dispatch_recheck_required_for_future_act": effect_class == "act",
    }
    if candidate is not None:
        result["candidate_payload_sha256"] = sha256_text(stable_json(candidate))
    return result


def _whoami(house: Any, context: dict[str, Any]) -> dict[str, Any]:
    count, digest = _surface_digest(house)
    return {
        "schema_version": KEYRING_SCHEMA_VERSION,
        "principal": _principal(house, context),
        "authority_epoch": _authority_epoch(house),
        "authority_source": "runtime_capability_registry_plus_existing_authorizers",
        "mode": "descriptive_preflight",
        "general_keyring_enforcement_active": False,
        "authority_epoch_mutation_surface_available": False,
        "capability_surface": {
            "count": count,
            "digest_sha256": digest,
        },
        "invariants": {
            "preview_is_authorization": False,
            "receipt_is_authority": False,
            "transport_is_authority": False,
            "existing_runtime_dispatch_semantics_changed_by_keyring_v0_1": False,
        },
    }


def _can(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    supplied = arguments if isinstance(arguments, dict) else None
    return {
        "schema_version": KEYRING_SCHEMA_VERSION,
        "principal": _principal(house, context),
        "authority_epoch": _authority_epoch(house),
        **_contract_preflight(
            house,
            str(payload.get("capability") or ""),
            supplied,
            validate_payload=arguments is not None,
        ),
    }


def _explain(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    capability = str(payload.get("capability") or "").strip().lower()
    arguments = payload.get("arguments")
    supplied = arguments if isinstance(arguments, dict) else None
    preflight = _contract_preflight(
        house,
        capability,
        supplied,
        validate_payload=arguments is not None,
    )
    contract = None
    if preflight.get("known"):
        contract = house.registry.describe(capability)[0]
    return {
        "schema_version": KEYRING_SCHEMA_VERSION,
        "principal": _principal(house, context),
        "authority_epoch": _authority_epoch(house),
        "preflight": preflight,
        "runtime_contract": contract,
        "explanation": (
            "Keyring v0.1 describes the live Runtime contract and validates an optional exact "
            "candidate payload. It does not execute focused authorizers or handlers and creates "
            "no approval. Existing Runtime dispatch remains authoritative until a later enforcing "
            "Keyring phase is explicitly migrated capability-by-capability."
        ),
    }


def _preview(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    capability = str(payload.get("capability") or "").strip().lower()
    arguments = payload.get("arguments")
    supplied = arguments if isinstance(arguments, dict) else {}
    preflight = _contract_preflight(
        house,
        capability,
        supplied,
        validate_payload=True,
    )
    return {
        "schema_version": KEYRING_SCHEMA_VERSION,
        "previewed_at": utc_now_iso(),
        "principal": _principal(house, context),
        "authority_epoch": _authority_epoch(house),
        "preflight": preflight,
        "expected_receipt_chain": [
            "runtime_keyring_preview_receipt",
            "runtime_dispatch_receipt_if_later_executed",
            "mcp_receipt_if_crossed_through_mcp",
            "external_receipt_only_if_a_future_external_adapter_executes",
        ],
        "preview_is_authorization": False,
        "approval_created": False,
        "target_executed": False,
        "canonical_changed": False,
        "external_effect": False,
    }


def _register(house: Any) -> None:
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.executescript(AUTHORITY_SCHEMA)
        connection.execute(
            """
            INSERT OR IGNORE INTO capability_authority_state
            (resident_id, authority_epoch, created_at, updated_at)
            VALUES (?, 1, ?, ?)
            """,
            (house.resident_id, now, now),
        )

    after = {"type": "string", "enum": ["continue", "finish"]}
    arguments_schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": True,
    }
    capability_name = {"type": "string", "minLength": 1, "maxLength": 200}

    house.registry.register(
        CapabilitySpec(
            name="policy.whoami",
            description=(
                "Inspect the current resident principal, authority epoch, and live Runtime "
                "capability-surface digest. This is descriptive and grants no authority."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="policy",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "policy.whoami"},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {"action": "policy.whoami", "after": "continue"},
            ),
        ),
        lambda _payload, context: _whoami(house, context),
    )
    house.registry.register(
        CapabilitySpec(
            name="policy.can",
            description=(
                "Preflight one Runtime capability contract for the current resident principal. "
                "Optional arguments are schema-checked, but no target authorizer or handler runs."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="policy",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "policy.can"},
                    "capability": capability_name,
                    "arguments": arguments_schema,
                    "after": after,
                },
                required=("action", "capability"),
            ),
            example_envelopes=(
                {
                    "action": "policy.can",
                    "capability": "fs.stage_patch",
                    "arguments": {
                        "operation": "edit",
                        "path": "workspace/notes.md",
                        "content": "candidate text",
                    },
                    "after": "continue",
                },
            ),
        ),
        lambda payload, context: _can(house, payload, context),
    )
    house.registry.register(
        CapabilitySpec(
            name="policy.explain",
            description=(
                "Explain the live Runtime contract and Keyring preflight evidence for one "
                "capability without invoking the target capability."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="policy",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "policy.explain"},
                    "capability": capability_name,
                    "arguments": arguments_schema,
                    "after": after,
                },
                required=("action", "capability"),
            ),
            example_envelopes=(
                {
                    "action": "policy.explain",
                    "capability": "file.write",
                    "after": "continue",
                },
            ),
        ),
        lambda payload, context: _explain(house, payload, context),
    )
    house.registry.register(
        CapabilitySpec(
            name="capability.preview",
            description=(
                "Validate and hash one exact candidate Runtime capability payload, then report "
                "its present contract/effect/authority boundary without executing it or creating "
                "an approval."
            ),
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="policy",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "capability.preview"},
                    "capability": capability_name,
                    "arguments": arguments_schema,
                    "after": after,
                },
                required=("action", "capability"),
            ),
            example_envelopes=(
                {
                    "action": "capability.preview",
                    "capability": "fs.stage_patch",
                    "arguments": {
                        "operation": "create",
                        "path": "workspace/keyring-demo.md",
                        "content": "proposal only",
                    },
                    "after": "continue",
                },
            ),
        ),
        lambda payload, context: _preview(house, payload, context),
    )


def register_composition() -> None:
    register_capability_installer(
        "capability_keyring",
        _register,
        order=340,
    )
