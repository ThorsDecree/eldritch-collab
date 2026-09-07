from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .composition import register_capability_installer
from .mcp_context_source import ContextSourceError, VestigiaArchiveMcpSource, _factory


SOURCE = {"type": "string", "enum": ["live", "snapshot"]}
PATH = {"type": "string", "minLength": 1, "maxLength": 500}
PREFIX = {"type": "string", "maxLength": 500}
AFTER = {"type": "string", "enum": ["continue", "finish"]}


def _payload(
    source: VestigiaArchiveMcpSource,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = source.call_tool(tool, arguments)
    structured = result.get("structured_content")
    if isinstance(structured, dict):
        data: dict[str, Any] = dict(structured)
    else:
        text_blocks = [str(item) for item in result.get("text_blocks", []) if str(item)]
        data = {"text": "\n".join(text_blocks)}
        if len(text_blocks) == 1:
            try:
                parsed = json.loads(text_blocks[0])
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict):
                    data = parsed
    data["mcp"] = {
        "tool": tool,
        "transport": "stdio_child",
        "server_name": result.get("server_name"),
        "server_version": result.get("server_version"),
        "protocol_version": result.get("protocol_version"),
        "archive_mutated": False,
        "memory_or_identity_changed": False,
    }
    return data


def _read_media(
    house: Any,
    source: VestigiaArchiveMcpSource,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if house.images is None:
        raise RuntimeError("MCP Archive media reads require the Runtime image shelf")
    result = source.call_tool("archive.read_media", arguments)
    images = result.get("image_blocks", [])
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ContextSourceError("archive.read_media did not return exactly one image block")
    try:
        data = base64.b64decode(str(images[0].get("data") or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ContextSourceError("archive.read_media returned invalid base64 image data") from exc

    metadata: dict[str, Any] = {}
    text_blocks = [str(item) for item in result.get("text_blocks", []) if str(item)]
    if text_blocks:
        try:
            parsed = json.loads(text_blocks[0])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            metadata = parsed
    expected_hash = str(metadata.get("sha256") or "")
    actual_hash = hashlib.sha256(data).hexdigest()
    if expected_hash and expected_hash != actual_hash:
        raise ContextSourceError("archive.read_media bytes do not match MCP metadata SHA-256")

    archive_path = str(metadata.get("path") or arguments["path"])
    asset = house.images.ingest_bytes(
        data,
        filename=Path(archive_path).name,
        source_kind="mcp_archive",
        source={
            "transport": "stdio_child",
            "archive_source": str(arguments["source"]),
            "archive_path": archive_path,
            "archive_sha256": actual_hash,
            "mcp_server_name": result.get("server_name"),
            "mcp_server_version": result.get("server_version"),
            "mcp_protocol_version": result.get("protocol_version"),
        },
        privacy="private",
    )
    return {
        "source": str(arguments["source"]),
        "archive_path": archive_path,
        "archive_sha256": actual_hash,
        "mime_type": str(metadata.get("mime_type") or images[0].get("mime_type") or ""),
        "size": len(data),
        "image_id": str(asset.get("id") or ""),
        "citation": f"archive://{arguments['source']}/{archive_path}",
        "privacy": "private",
        "reused": bool(asset.get("reused", False)),
        "next_step": (
            "Use image.inspect with the returned image_id to perceive its contents, or "
            "image.drawer to name, annotate, or pocket it. Archive bytes were not changed."
        ),
        "mcp": {
            "tool": "archive.read_media",
            "transport": "stdio_child",
            "server_name": result.get("server_name"),
            "server_version": result.get("server_version"),
            "protocol_version": result.get("protocol_version"),
            "archive_mutated": False,
            "local_private_image_imported": True,
            "memory_or_identity_changed": False,
        },
    }


def _bound_text_content(data: dict[str, Any], maximum: int) -> dict[str, Any]:
    content = data.get("content")
    if not isinstance(content, str):
        return data
    source_characters = len(content)
    bounded = dict(data)
    bounded["source_characters"] = source_characters
    bounded["returned_characters"] = min(source_characters, maximum)
    bounded["content_truncated"] = source_characters > maximum
    if source_characters > maximum:
        bounded["content"] = content[:maximum]
        bounded["truncation_reason"] = "runtime_mcp_archive_character_ceiling"
    return bounded


def _register_one(
    house: Any,
    source: VestigiaArchiveMcpSource | None,
    *,
    name: str,
    server_tool: str,
    description: str,
    fields: dict[str, dict[str, Any]],
    required: tuple[str, ...],
    example: dict[str, Any],
    media: bool = False,
    bound_text: bool = False,
) -> None:
    properties = {"action": {"type": "string", "const": name}, **fields, "after": AFTER}

    def handler(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        if source is None:
            raise RuntimeError("VESTIGIA Archive MCP client is not configured")
        arguments = {
            key: value
            for key, value in payload.items()
            if key not in {"action", "after"}
        }
        if media:
            return _read_media(house, source, arguments)
        maximum: int | None = None
        if bound_text:
            default_maximum = min(
                50_000,
                max(
                    500,
                    int(house.config.get("house.max_result_tokens", 6000)) * 4,
                ),
            )
            maximum = int(arguments.pop("max_chars", default_maximum))
        result = _payload(source, server_tool, arguments)
        return _bound_text_content(result, maximum) if maximum is not None else result

    effects = ("filesystem:read", "process:spawn_local")
    if media:
        effects += ("filesystem:write_private_image_cache", "database:audit_write")
    house.registry.register(
        CapabilitySpec(
            name=name,
            description=description,
            effects=effects,
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="context_sources.mcp_archive.enabled",
            forgeable=False,
            group="pictures" if media else "archive",
            input_schema=object_schema(
                properties,
                required=("action", *required),
            ),
            example_envelopes=({"action": name, **example, "after": "continue"},),
            next_step=(
                "Inspect the returned private image_id with image.inspect."
                if media
                else "Use another mcp.archive capability to continue bounded Archive browsing."
            ),
        ),
        handler,
    )


def _register(house: Any) -> None:
    source = _factory(house.config, house.db)

    _register_one(
        house,
        source,
        name="mcp.archive.status",
        server_tool="archive.status",
        description="Check the read-only live and snapshot Archive sources through local MCP.",
        fields={},
        required=(),
        example={},
    )
    _register_one(
        house,
        source,
        name="mcp.archive.list",
        server_tool="archive.list",
        description="Browse bounded Archive-relative paths through local MCP.",
        fields={
            "source": SOURCE,
            "prefix": PREFIX,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        required=("source",),
        example={"source": "live", "prefix": "Liora/pics", "limit": 50},
    )
    _register_one(
        house,
        source,
        name="mcp.archive.search",
        server_tool="archive.search_text",
        description="Search Archive text literally through local MCP without changing it.",
        fields={
            "source": SOURCE,
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "prefix": PREFIX,
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "case_sensitive": {"type": "boolean"},
        },
        required=("source", "query"),
        example={"source": "live", "query": "lantern", "limit": 20},
    )
    _register_one(
        house,
        source,
        name="mcp.archive.read_text",
        server_tool="archive.read_text",
        description="Read one bounded UTF-8 Archive file through local MCP.",
        fields={
            "source": SOURCE,
            "path": PATH,
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 50_000},
        },
        required=("source", "path"),
        example={
            "source": "live",
            "path": "00_Bootloader/house_index.json",
            "max_chars": 20_000,
        },
        bound_text=True,
    )
    _register_one(
        house,
        source,
        name="mcp.archive.read_media",
        server_tool="archive.read_media",
        description=(
            "Read one verified Archive image through local MCP into the resident's private "
            "content-addressed image shelf. The Archive remains unchanged."
        ),
        fields={"source": SOURCE, "path": PATH},
        required=("source", "path"),
        example={"source": "live", "path": "Liora/pics/example.png"},
        media=True,
    )
    _register_one(
        house,
        source,
        name="mcp.archive.health",
        server_tool="archive.health",
        description="Run bounded Archive structural health checks through local MCP.",
        fields={"source": SOURCE},
        required=("source",),
        example={"source": "live"},
    )
    _register_one(
        house,
        source,
        name="mcp.archive.diff_detail",
        server_tool="archive.diff_detail",
        description="Compare one Archive-relative path between live and snapshot through MCP.",
        fields={"path": PATH},
        required=("path",),
        example={"path": "00_Bootloader/house_index.json"},
    )


def register_composition() -> None:
    register_capability_installer("mcp.archive_tools", _register, order=320)
