from __future__ import annotations

from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .private_messages import PrivateMessageService


def _require_private_curation(context: dict[str, Any]) -> None:
    if (
        context.get("interface") != "curation"
        or context.get("invocation") != "private_curation"
    ):
        raise PermissionError("private message actions are available only in private curation")


def _authorize_grant(_spec: CapabilitySpec, _payload: dict[str, Any], context: dict[str, Any]) -> None:
    _require_private_curation(context)


def _authorize_draft(_spec: CapabilitySpec, _payload: dict[str, Any], context: dict[str, Any]) -> None:
    _require_private_curation(context)


def _authorize_submit(
    _spec: CapabilitySpec,
    payload: dict[str, Any],
    context: dict[str, Any],
    service: PrivateMessageService,
) -> None:
    _require_private_curation(context)
    draft = service.inspect_draft(str(payload["draft_id"]))
    if draft["created_turn_id"] != str(context.get("turn_id") or ""):
        raise PermissionError("draft must be submitted in the same private turn")
    provider_round = int(context.get("house_tool_round", -1))
    if provider_round <= int(draft["provider_round"]):
        raise PermissionError("submission requires a later provider round")


def _string(description: str, *, enum: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "description": description}
    if enum:
        result["enum"] = enum
    return result


def _install(house: Any) -> None:
    service = PrivateMessageService(house.db, house.resident_id)
    registry = house.registry
    registry.register(
        CapabilitySpec(
            name="message.grant",
            description="Inspect, accept the exact terms of, decline, or relinquish a private-message grant.",
            effects=("database:write",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="communication",
            related_actions=("message.draft", "message.submit"),
            next_step="Review the grant terms and hash before accepting; acceptance does not send a message.",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "message.grant"},
                    "mode": _string("Grant operation", enum=["inspect", "accept", "decline", "relinquish"]),
                    "grant_id": _string("Operator-created grant ID"),
                    "terms_hash": _string("Exact grant terms hash required for acceptance"),
                    "after": {"type": "string", "enum": ["continue", "finish"]},
                },
                required=("action", "mode", "grant_id"),
            ),
            example_envelopes=(
                {"action": "message.grant", "mode": "inspect", "grant_id": "grant_..."},
            ),
        ),
        lambda payload, _context: service.grant_action(
            str(payload["grant_id"]),
            str(payload["mode"]),
            str(payload["terms_hash"]) if payload.get("terms_hash") else None,
        ),
        authorizer=_authorize_grant,
    )
    registry.register(
        CapabilitySpec(
            name="message.draft",
            description="Create a private plain-text message draft against an accepted grant.",
            effects=("database:write",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="communication",
            related_actions=("message.grant", "message.submit"),
            next_step="Inspect the returned draft ID and hash; only a later private provider round can submit it.",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "message.draft"},
                    "grant_id": _string("Accepted grant ID; destination is fixed by the grant"),
                    "body": _string("Exact plain-text message, up to the grant's UTF-16 code-unit limit"),
                    "after": {"type": "string", "enum": ["continue", "finish"]},
                },
                required=("action", "grant_id", "body"),
            ),
            example_envelopes=(
                {"action": "message.draft", "grant_id": "grant_...", "body": "Exact text"},
            ),
        ),
        lambda payload, context: service.draft(
            str(payload["grant_id"]),
            str(payload["body"]),
            turn_id=str(context.get("turn_id") or ""),
            provider_round=int(context.get("house_tool_round", -1)),
        ),
        authorizer=_authorize_draft,
    )

    def submit_authorizer(
        spec: CapabilitySpec, payload: dict[str, Any], context: dict[str, Any]
    ) -> None:
        _authorize_submit(spec, payload, context, service)

    registry.register(
        CapabilitySpec(
            name="message.submit",
            description="Authorize one exact earlier draft for delivery through its grant-bound Discord destination.",
            effects=("outward_message:queued", "database:write"),
            confirmation="resident_private_curation_submission",
            default_after="finish",
            result_visibility="resident_private",
            outward_facing=True,
            group="communication",
            related_actions=("message.grant", "message.draft"),
            next_step="Inspect the queued receipt. Queued means accepted for a single delivery attempt, not delivered.",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "message.submit"},
                    "draft_id": _string("Earlier private draft ID"),
                    "draft_hash": _string("Exact SHA-256 hash returned when the draft was created"),
                    "after": {"type": "string", "enum": ["continue", "finish"]},
                },
                required=("action", "draft_id", "draft_hash"),
            ),
            example_envelopes=(
                {"action": "message.submit", "draft_id": "msgdraft_...", "draft_hash": "<exact hash>"},
            ),
        ),
        lambda payload, context: service.submit(
            str(payload["draft_id"]),
            str(payload["draft_hash"]),
            turn_id=str(context.get("turn_id") or ""),
            provider_round=int(context.get("house_tool_round", -1)),
            source_invocation=str(context.get("invocation") or ""),
        ),
        authorizer=submit_authorizer,
    )


def register_composition() -> None:
    from .composition import register_capability_installer

    register_capability_installer("private_message.capabilities", _install, order=90)
