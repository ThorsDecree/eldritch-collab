from __future__ import annotations

from typing import Any

from .utils import sha256_text, stable_json


READ_EFFECTS = frozenset({"filesystem:read", "database:read"})
PROJECTION_SCHEMA_VERSION = "vestigia.mcp-read-projection.v0.1"


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

    digest_payload = [
        {
            "name": item["name"],
            "schema_version": item.get("schema_version"),
            "effects": item.get("effects", []),
            "confirmation": item.get("confirmation"),
            "input_schema": item.get("input_schema", {}),
        }
        for item in sorted(projectable, key=lambda value: str(value["name"]))
    ]

    if target:
        capabilities = projectable
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
