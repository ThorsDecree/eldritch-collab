from __future__ import annotations

from copy import deepcopy
from typing import Any

from .utils import sha256_text, stable_json


READ_EFFECTS = frozenset({"filesystem:read", "database:read"})
LOCAL_MUTATION_EFFECTS = frozenset(
    {
        "filesystem:write_workspace",
        "database:audit_write",
        "database:write_pending_draft",
        "database:write_low_authority",
    }
)
MUTATION_ALLOWED_EFFECTS = READ_EFFECTS | LOCAL_MUTATION_EFFECTS
PROJECTION_SCHEMA_VERSION = "vestigia.mcp-read-projection.v0.2"
MUTATION_PROJECTION_SCHEMA_VERSION = "vestigia.mcp-mutation-projection.v0.2"
WRAPPER_OWNED_FIELDS = ("action", "after")


def _wrapper_arguments_schema(contract: dict[str, Any]) -> dict[str, Any]:
    """Return the schema accepted inside MCP's nested ``arguments`` object."""

    schema = deepcopy(contract.get("input_schema", {}))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {
            key: value
            for key, value in properties.items()
            if key not in WRAPPER_OWNED_FIELDS
        }
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [
            key for key in required if key not in WRAPPER_OWNED_FIELDS
        ]
    schema["description"] = (
        "Arguments accepted inside the MCP wrapper's arguments object. "
        "The wrapper supplies action and after."
    )
    return schema


def _project_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Preserve Runtime truth while publishing MCP's effective call grammar."""

    projected = deepcopy(contract)
    native_schema = deepcopy(contract.get("input_schema", {}))
    projected["runtime_input_schema"] = native_schema
    projected["input_schema"] = _wrapper_arguments_schema(contract)
    projected["wrapper_owned_fields"] = list(WRAPPER_OWNED_FIELDS)

    native_examples = deepcopy(contract.get("example_envelopes", []))
    projected["runtime_example_envelopes"] = native_examples
    projected["argument_examples"] = [
        {
            key: value
            for key, value in example.items()
            if key not in WRAPPER_OWNED_FIELDS
        }
        for example in native_examples
        if isinstance(example, dict)
    ]
    return projected


def _is_projectable_contract(contract: dict[str, Any]) -> bool:
    effects = {str(item) for item in contract.get("effects", [])}
    return bool(
        contract.get("dispatchable_via_tool_action", True)
        and contract.get("callable_now", False)
        and contract.get("confirmation") == "none"
        and not bool(contract.get("outward_facing", False))
        and effects
        and effects <= READ_EFFECTS
    )


def _is_projectable_mutation_contract(
    contract: dict[str, Any],
    allowed_actions: frozenset[str],
) -> bool:
    name = str(contract.get("name") or "").strip().lower()
    effects = {str(item) for item in contract.get("effects", [])}
    return bool(
        name in allowed_actions
        and contract.get("dispatchable_via_tool_action", True)
        and contract.get("callable_now", False)
        and contract.get("confirmation") == "none"
        and not bool(contract.get("outward_facing", False))
        and effects & LOCAL_MUTATION_EFFECTS
        and effects <= MUTATION_ALLOWED_EFFECTS
    )


def read_projection(house: Any, target: str | None = None) -> dict[str, Any]:
    """Project Runtime's existing executable registry into a read-only MCP view.

    This module deliberately does not define a second capability ontology. Runtime
    CapabilitySpec metadata remains authoritative; this function only selects the
    already-callable, non-outward, confirmation-free read subset.
    """

    contracts = house.registry.describe(target)
    projectable = [item for item in contracts if _is_projectable_contract(item)]
    if target and not projectable:
        raise PermissionError(
            f"Runtime capability is not available through the read-only MCP projection: {target}"
        )

    projected_contracts = [_project_contract(item) for item in projectable]
    digest_payload = [
        {
            "name": item["name"],
            "schema_version": item.get("schema_version"),
            "effects": item.get("effects", []),
            "confirmation": item.get("confirmation"),
            "input_schema": item.get("input_schema", {}),
            "runtime_input_schema": item.get("runtime_input_schema", {}),
            "wrapper_owned_fields": item.get("wrapper_owned_fields", []),
        }
        for item in sorted(projected_contracts, key=lambda value: str(value["name"]))
    ]

    if target:
        capabilities = projected_contracts
    else:
        capabilities = [
            {
                "name": item["name"],
                "description": item["description"],
                "effects": item.get("effects", []),
                "group": item.get("group"),
                "schema_version": item.get("schema_version"),
                "callable_now": item.get("callable_now", False),
            }
            for item in sorted(projectable, key=lambda value: str(value["name"]))
        ]

    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "authority": "runtime_capability_registry",
        "projection": "read_only",
        "allowed_effects": sorted(READ_EFFECTS),
        "capability_count": len(projectable),
        "capability_digest_sha256": sha256_text(stable_json(digest_payload)),
        "capabilities": capabilities,
    }


def dispatch_read_projection(
    house: Any,
    *,
    action: str,
    arguments: dict[str, Any] | None,
    request_id: str,
    deployment_id: str = "",
) -> dict[str, Any]:
    """Dispatch one projected read through HousePort, preserving Runtime receipts."""

    normalized = str(action).strip().lower()
    if not normalized:
        raise ValueError("Runtime action must not be blank")
    supplied = dict(arguments or {})
    if "action" in supplied or "after" in supplied:
        raise ValueError("Runtime projection owns the action and after fields")

    projection = read_projection(house, normalized)
    payload = {"action": normalized, **supplied, "after": "finish"}
    result = house.dispatch(
        payload,
        turn_id=request_id,
        context={
            "interface": "mcp",
            "source_envelope": "MCP",
            "request_id": request_id,
            "mcp_deployment_id": deployment_id,
        },
    )
    return {
        "request_id": request_id,
        "projection": {
            "schema_version": projection["schema_version"],
            "authority": projection["authority"],
            "capability_digest_sha256": projection["capability_digest_sha256"],
            "action": normalized,
        },
        "runtime": result,
    }


def mutation_projection(
    house: Any,
    allowed_actions: tuple[str, ...],
    target: str | None = None,
) -> dict[str, Any]:
    """Project an operator-granted subset of Runtime-local mutation contracts.

    Runtime remains the authority for capability metadata, input validation, workspace roots,
    byte ceilings, optimistic hashes, and receipts. The caller-supplied allowlist is a second,
    deployment-scoped gate; an empty allowlist grants nothing.
    """
    grants = frozenset(
        str(item).strip().lower()
        for item in allowed_actions
        if str(item).strip()
    )
    contracts = house.registry.describe(target)
    projectable = [
        item for item in contracts if _is_projectable_mutation_contract(item, grants)
    ]
    if target and not projectable:
        raise PermissionError(
            "Runtime capability is not available through the configured MCP mutation "
            f"projection: {target}"
        )

    projected_contracts = [_project_contract(item) for item in projectable]
    digest_payload = [
        {
            "name": item["name"],
            "schema_version": item.get("schema_version"),
            "effects": item.get("effects", []),
            "confirmation": item.get("confirmation"),
            "input_schema": item.get("input_schema", {}),
            "runtime_input_schema": item.get("runtime_input_schema", {}),
            "wrapper_owned_fields": item.get("wrapper_owned_fields", []),
        }
        for item in sorted(projected_contracts, key=lambda value: str(value["name"]))
    ]
    capabilities = (
        projected_contracts
        if target
        else [
            {
                "name": item["name"],
                "description": item["description"],
                "effects": item.get("effects", []),
                "group": item.get("group"),
                "schema_version": item.get("schema_version"),
                "callable_now": item.get("callable_now", False),
            }
            for item in sorted(projectable, key=lambda value: str(value["name"]))
        ]
    )
    return {
        "schema_version": MUTATION_PROJECTION_SCHEMA_VERSION,
        "authority": "runtime_capability_registry_plus_mcp_deployment_allowlist",
        "projection": "bounded_local_mutation",
        "configured_actions": sorted(grants),
        "allowed_effects": sorted(MUTATION_ALLOWED_EFFECTS),
        "capability_count": len(projectable),
        "capability_digest_sha256": sha256_text(stable_json(digest_payload)),
        "capabilities": capabilities,
    }


def dispatch_mutation_projection(
    house: Any,
    *,
    action: str,
    arguments: dict[str, Any] | None,
    request_id: str,
    allowed_actions: tuple[str, ...],
    deployment_id: str = "",
) -> dict[str, Any]:
    """Dispatch one explicitly granted local mutation through Runtime HousePort."""
    normalized = str(action).strip().lower()
    if not normalized:
        raise ValueError("Runtime action must not be blank")
    supplied = dict(arguments or {})
    if "action" in supplied or "after" in supplied:
        raise ValueError("Runtime projection owns the action and after fields")

    projection = mutation_projection(house, allowed_actions, normalized)
    result = house.dispatch(
        {"action": normalized, **supplied, "after": "finish"},
        turn_id=request_id,
        context={
            "interface": "mcp",
            "source_envelope": "MCP",
            "request_id": request_id,
            "mcp_deployment_id": deployment_id,
            "mcp_projection": "bounded_local_mutation",
        },
    )
    return {
        "request_id": request_id,
        "projection": {
            "schema_version": projection["schema_version"],
            "authority": projection["authority"],
            "capability_digest_sha256": projection["capability_digest_sha256"],
            "action": normalized,
        },
        "runtime": result,
    }
