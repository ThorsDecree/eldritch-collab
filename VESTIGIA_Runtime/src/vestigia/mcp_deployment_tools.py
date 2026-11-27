from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .mcp_stdio import McpStdioClient, McpToolRejected


def require_private_mcp_turn(
    _spec: CapabilitySpec, _payload: dict[str, Any], context: dict[str, Any],
) -> None:
    if context.get("invocation") != "conversation" or not context.get("turn_id"):
        raise PermissionError("This MCP capability requires a private conversation turn")
    interface = context.get("interface")
    target = context.get("delivery_target")
    if interface in {"cli", "web"} or (
        interface == "discord"
        and isinstance(target, dict)
        and target.get("kind") == "discord_dm"
        and target.get("id")
        and not context.get("is_group_dm")
    ):
        return
    raise PermissionError("Use the local CLI, web UI, or a one-to-one Discord DM for this MCP capability")


def _save_images(
    house: Any, tool: str, blobs: list[dict[str, str]], provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    if blobs and house.images is None:
        raise RuntimeError("MCP images require the Runtime image shelf")
    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    images = []
    for index, blob in enumerate(blobs, 1):
        suffix = extensions.get(blob["mime_type"])
        if suffix is None:
            raise ValueError("MCP returned an unsupported image type")
        data = base64.b64decode(blob["data"], validate=True)
        digest = hashlib.sha256(data).hexdigest()
        asset = house.images.ingest_bytes(
            data,
            filename=f"mcp-{tool}-{index}{suffix}",
            source_kind="mcp_deployment",
            source={**provenance, "tool": tool, "image_sha256": digest},
            privacy="private",
        )
        images.append({"image_id": asset["id"], "sha256": digest, "mime_type": blob["mime_type"]})
    return images


def register_deployment_tools(house: Any) -> None:
    raw_profile = str(house.config.get("context_sources.mcp_archive.deployment_env_file", "")).strip()
    if not raw_profile:
        return
    profile = Path(raw_profile).expanduser()
    if not profile.is_absolute() or not profile.is_file():
        raise ValueError("VESTIGIA_CONTEXT_MCP_DEPLOYMENT_ENV_FILE must identify an existing absolute file")
    client = McpStdioClient(
        float(house.config.get("context_sources.mcp_archive.timeout_seconds", 30)),
        args=("--deployment-env", str(profile.resolve())),
    )

    from .gramophone_tools import register_gramophone_tools

    register_gramophone_tools(house, client)

    tool_name = {"type": "string", "maxLength": 128, "pattern": "[a-zA-Z0-9_.-]+"}
    after = {"type": "string", "enum": ["continue", "finish"]}

    def discover(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        tools = client.list_tools()
        name = payload.get("tool")
        if name:
            tool = next((entry for entry in tools if entry["name"] == name), None)
            if tool is None:
                raise ValueError(f"The configured MCP deployment does not advertise {name}")
            return {"tool_contract": tool}
        prefix = payload.get("prefix", "")
        names = sorted(entry["name"] for entry in tools if entry["name"].startswith(prefix))
        offset = payload.get("offset", 0)
        limit = payload.get("limit", 30)
        end = offset + limit
        return {
            "tools": names[offset:end],
            "total": len(names),
            "next_offset": end if end < len(names) else None,
        }

    def call(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        tool = payload["tool"]
        arguments = payload.get("arguments", {})
        try:
            response = client.call_tool(tool, arguments)
        except McpToolRejected as exc:
            message = str(exc)
            seat_token = arguments.get("seat_token")
            if isinstance(seat_token, str) and seat_token:
                message = message.replace(seat_token, "[seat credential]")
            raise McpToolRejected(message) from None
        metadata = {
            key: response[key]
            for key in ("tool", "server_name", "server_version", "protocol_version")
        }
        structured = response["structured_content"]
        data = structured if structured is not None else {"text_blocks": response["text_blocks"]}
        for key in ("request_id", "game_id", "stage_id", "scan_id"):
            if isinstance(data.get(key), str):
                metadata[key] = data[key][:128]
        return {
            "mcp": metadata,
            "images": _save_images(house, tool, response["image_blocks"], metadata),
            "mcp_result": data,
        }

    house.registry.register(
        CapabilitySpec(
            name="mcp.tools",
            description="Discover tools or get one exact input contract from the dedicated MCP deployment. Search prefix lanternslide. or game. to find photos and tabletop games.",
            effects=("process:spawn_local",),
            confirmation="resident_authenticated_doorway",
            group="mcp",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "mcp.tools"},
                    "tool": tool_name,
                    "prefix": {"type": "string", "maxLength": 128},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=({"action": "mcp.tools", "prefix": "lanternslide."},),
            next_step="Look up one exact tool, then pass its documented inputs to mcp.call.",
        ),
        discover,
        authorizer=require_private_mcp_turn,
    )
    house.registry.register(
        CapabilitySpec(
            name="mcp.call",
            description="Call one tool on the dedicated MCP deployment under its native permissions. Results are private to the current turn; returned pictures also enter the private image shelf.",
            effects=("process:spawn_local", "mcp:delegated", "filesystem:write_private_image_cache"),
            confirmation="resident_authenticated_doorway",
            group="mcp",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "mcp.call"},
                    "tool": tool_name,
                    "arguments": object_schema({}, additional=True),
                    "after": {"type": "string", "const": "continue"},
                },
                required=("action", "tool"),
            ),
            example_envelopes=({"action": "mcp.call", "tool": "lanternslide.deal", "arguments": {"count": 3, "seed": "evening"}},),
            next_step="Use mcp.tools for exact contracts and image.inspect for returned private image IDs.",
        ),
        call,
        authorizer=require_private_mcp_turn,
    )
