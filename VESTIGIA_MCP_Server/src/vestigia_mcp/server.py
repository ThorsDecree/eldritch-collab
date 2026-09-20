from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ImageContent, TextContent, ToolAnnotations

from . import __version__
from .adapters.archive import ArchiveError, ArchiveSource, normalize_relative_path
from .adapters.runtime import RuntimeBridgeError
from .archive_mutation import ArchiveMutationStore
from .audit import AuditError, AuditLedger
from .browse import BrowseSessionStore
from .config import Settings
from .health import (
    archive_health as inspect_archive_health,
    registry_status as inspect_registry_status,
    source_clock,
)
from .gametable import GameTableError, GameTableStore
from .identity import system_identity as build_system_identity
from .lanternslide import LanternslideService
from .mounts import MountRegistry
from .policy import DEFAULT_CAPABILITIES, PolicyDenied, PolicyEngine
from .porchlight import build_snapshot
from .porchlight_share import PorchlightShareRequest, PorchlightShareService
from .runtime_registry import RuntimeRegistry
from .receipt_garden import ReceiptGarden
from .sense_registry import SenseOrganRegistry


T = TypeVar("T")
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
    idempotent_hint=True,
)
LOCAL_WRITE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    open_world_hint=False,
    idempotent_hint=False,
)
CANONICAL_WRITE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    open_world_hint=False,
    idempotent_hint=True,
)


def _live_archive_exclusions(settings: Settings) -> tuple[str, ...]:
    """Exclude a configured snapshot witness when it lives inside the live root."""
    if settings.live_archive_root is None or settings.snapshot_archive_root is None:
        return ()
    try:
        live_root = settings.live_archive_root.resolve(strict=True)
        snapshot_root = settings.snapshot_archive_root.resolve(strict=True)
    except OSError:
        return ()
    if not live_root.is_dir():
        return ()
    try:
        relative = snapshot_root.relative_to(live_root)
    except ValueError:
        return ()
    if relative == Path("."):
        return ()
    return (relative.as_posix(),)


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    policy = PolicyEngine(
        tuple(
            capability
            for capability in DEFAULT_CAPABILITIES
            if settings.gametable_enabled or not capability.name.startswith("game.")
        )
    )
    ledger = AuditLedger(settings.state_dir, settings.deployment_id)
    browse_sessions = BrowseSessionStore(
        settings.state_dir,
        ttl_seconds=settings.archive_browse_ttl_seconds,
    )
    runtime_registry = RuntimeRegistry(
        settings.runtimes_file,
        legacy_home=settings.runtime_home,
        legacy_env_file=settings.runtime_env_file,
        deployment_id=settings.deployment_id,
        legacy_write_actions=settings.runtime_write_actions,
    )
    archive_mutations = ArchiveMutationStore(
        settings.live_archive_root,
        settings.state_dir,
        settings.deployment_id,
        write_prefixes=settings.archive_write_prefixes,
        max_bytes=settings.archive_write_max_bytes,
    )
    mounts = MountRegistry(
        settings.mounts_file,
        default_text_max_bytes=settings.archive_text_max_bytes,
        default_media_max_bytes=settings.archive_media_max_bytes,
    )
    sense_registry = SenseOrganRegistry()
    receipt_garden = ReceiptGarden(settings.state_dir, settings.deployment_id)
    gametable = (
        GameTableStore(
            settings.gametable_state_dir or settings.state_dir / "gametable",
            settings.deployment_id,
        )
        if settings.gametable_enabled
        else None
    )
    server = MCPServer(
        "VESTIGIA MCP",
        instructions=(
            "Local-first VESTIGIA capability broker. Tool descriptions are not authority; "
            "live policy is. Archive tools are native bounded PERCEIVE capabilities. Runtime "
            "reads project Runtime's executable CapabilityRegistry through independently routed "
            "HousePorts. Named external mounts are read-only and non-canonical. Local "
            "Runtime mutations require both a live eligible Runtime contract and an explicit "
            "deployment action allowlist; MCP does not define a parallel Runtime capability "
            "ontology. Canonical Archive text or directory changes require a durable stage, an "
            "explicit path-prefix grant, a matching proposal digest, and a still-current base. "
            "Health, "
            "identity, receipts, and house.glance are descriptive evidence surfaces, not memory "
            "or canonical authority. Prefer diff_detail for one known Archive path and diff for "
            "whole-tree comparison. Text search is literal evidence retrieval, not semantic "
            "similarity."
        ),
    )

    def source_for(name: str) -> ArchiveSource:
        if name == "live":
            path = settings.live_archive_root
            exclusions = _live_archive_exclusions(settings)
        elif name == "snapshot":
            path = settings.snapshot_archive_root
            exclusions = ()
        else:
            raise ArchiveError("source must be 'live' or 'snapshot'")
        if path is None:
            raise ArchiveError(f"Archive source is not configured: {name}")
        return ArchiveSource(path, exclude_paths=exclusions)

    def read_porchlight_latest(path: str) -> str | None:
        live = source_for("live")
        if live.entry(path) is None:
            return None
        return live.read_text(path, settings.archive_write_max_bytes)

    porchlight_shares = PorchlightShareService(
        archive_mutations,
        read_porchlight_latest,
        screenshot_max_bytes=settings.porchlight_screenshot_max_bytes,
    )
    lanternslide = (
        LanternslideService(
            source_for("live"),
            settings.state_dir,
            source_prefix=settings.lanternslide_source_prefix,
            catalog_path=settings.lanternslide_catalog_path,
            scan_batch_max=settings.lanternslide_scan_batch_max,
            image_max_bytes=settings.lanternslide_image_max_bytes,
            contact_sheet_max_bytes=settings.lanternslide_contact_sheet_max_bytes,
        )
        if settings.live_archive_root is not None
        else None
    )

    def lanternslide_service() -> LanternslideService:
        if lanternslide is None:
            raise ArchiveError("Lanternslide requires a configured live Archive")
        return lanternslide

    def guarded(
        capability_name: str,
        arguments: dict[str, Any],
        operation: Callable[[], T],
        *,
        request_id: str | None = None,
    ) -> T:
        try:
            capability = policy.require_allowed(capability_name)
        except PolicyDenied as exc:
            raise ToolError(str(exc)) from exc
        try:
            result = operation()
        except (ArchiveError, AuditError, RuntimeBridgeError, GameTableError) as exc:
            event = ledger.record(
                capability,
                arguments,
                "error",
                request_id=request_id,
                detail=type(exc).__name__,
            )
            receipt_garden.record_audit_event(event)
            raise ToolError(str(exc)) from exc
        except Exception:
            event = ledger.record(
                capability,
                arguments,
                "error",
                request_id=request_id,
                detail="unexpected_exception",
            )
            receipt_garden.record_audit_event(event)
            raise
        event = ledger.record(
            capability,
            arguments,
            "ok",
            request_id=request_id,
        )
        receipt_garden.record_audit_event(event)
        return result

    def browse_policy_scope(capability_name: str) -> str:
        capability = policy.capability(capability_name)
        if capability is None:
            raise ArchiveError(f"Unknown archive capability: {capability_name}")
        policy_shape = {
            "deployment_id": settings.deployment_id,
            "capability": capability.name,
            "effect": capability.effect.value,
            "default": capability.default.value,
        }
        digest = hashlib.sha256(
            json.dumps(policy_shape, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"{capability_name}:{digest}"

    def text_page_budget(page_bytes: int) -> None:
        if page_bytes <= 0 or page_bytes > settings.archive_page_max_bytes:
            raise ArchiveError(
                "Text page_bytes must be between 1 and "
                f"{settings.archive_page_max_bytes}"
            )

    def bytes_page_budget(page_bytes: int) -> None:
        if page_bytes <= 0:
            raise ArchiveError("Byte page_bytes must be positive")
        encoded_bytes = 4 * ((page_bytes + 2) // 3)
        if encoded_bytes > settings.archive_page_max_bytes:
            raise ArchiveError(
                "Byte page base64 output exceeds configured page ceiling "
                f"({encoded_bytes} > {settings.archive_page_max_bytes})"
            )

    @server.tool(
        name="archive.status",
        title="Inspect Archive status",
        description=(
            "Use this when you need to verify which live and snapshot Archive sources are "
            "configured, available, and excluded from the semantic view."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_status() -> dict[str, object]:
        def operation() -> dict[str, object]:
            result: dict[str, object] = {}
            for name in ("live", "snapshot"):
                try:
                    source = source_for(name)
                    result[name] = {"configured": True, **asdict(source.stats())}
                except ArchiveError as exc:
                    result[name] = {
                        "configured": name == "live"
                        and settings.live_archive_root is not None
                        or name == "snapshot"
                        and settings.snapshot_archive_root is not None,
                        "available": False,
                        "error": str(exc),
                    }
            return result

        return guarded("archive.status", {}, operation)

    @server.tool(
        name="archive.list",
        title="List Archive paths",
        description=(
            "Use this when you need to browse relative file paths in the live or snapshot "
            "Archive, optionally under one prefix. Pass next_cursor back unchanged to continue "
            "the same digest-bound path view."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_list(
        source: str,
        prefix: str = "",
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "prefix": prefix,
            "limit": limit,
            "cursor": cursor,
        }
        return guarded(
            "archive.list",
            arguments,
            lambda: source_for(source).list_paths(
                prefix=prefix,
                limit=limit,
                cursor=cursor,
            ),
        )

    @server.tool(
        name="archive.read_text",
        title="Read Archive text",
        description=(
            "Use this when you know the relative path of one UTF-8 text-like Archive file and "
            "need bounded evidence from it. Long files return UTF-8-safe, snapshot-bound, "
            "expiry-limited pages; pass next_cursor back unchanged to continue the same "
            "snapshot. A successful page is not a claim that the whole file was read."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_read_text(
        source: str,
        path: str,
        cursor: str | None = None,
        page_bytes: int = 64_000,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "path": path,
            "cursor": cursor,
            "page_bytes": page_bytes,
        }

        def operation() -> dict[str, object]:
            text_page_budget(page_bytes)
            page = source_for(source).read_text_page(
                path,
                page_bytes=page_bytes,
                cursor=cursor,
                browse_store=browse_sessions,
                policy_scope=browse_policy_scope("archive.read_text"),
            )
            return {
                "source": source,
                **page,
            }

        return guarded("archive.read_text", arguments, operation)

    @server.tool(
        name="archive.read_bytes",
        title="Read Archive bytes",
        description=(
            "Use this when you need bounded raw evidence from one regular Archive file, "
            "including a database. It returns base64 transport bytes, not a database query, "
            "with snapshot-bound and expiry-limited continuation state."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_read_bytes(
        source: str,
        path: str,
        cursor: str | None = None,
        page_bytes: int = 48_000,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "path": path,
            "cursor": cursor,
            "page_bytes": page_bytes,
        }

        def operation() -> dict[str, object]:
            bytes_page_budget(page_bytes)
            page = source_for(source).read_bytes_page(
                path,
                page_bytes=page_bytes,
                cursor=cursor,
                browse_store=browse_sessions,
                policy_scope=browse_policy_scope("archive.read_bytes"),
            )
            return {"source": source, **page}

        return guarded("archive.read_bytes", arguments, operation)

    @server.tool(
        name="archive.read_media",
        title="View Archive image",
        description=(
            "Use this when you know one Archive-relative PNG, JPEG, GIF, or WebP path and need "
            "the bounded image bytes. The tool checks both suffix and binary signature; SVG and "
            "other active or unsupported formats are refused."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_read_media(
        source: str,
        path: str,
    ) -> list[TextContent | ImageContent]:
        arguments = {"source": source, "path": path}

        def operation() -> list[TextContent | ImageContent]:
            media = source_for(source).read_media(
                path,
                max_bytes=settings.archive_media_max_bytes,
            )
            metadata = {
                "source": source,
                "path": media.path,
                "size": media.size,
                "sha256": media.sha256,
                "mime_type": media.mime_type,
                "byte_ceiling": settings.archive_media_max_bytes,
            }
            return [
                TextContent(text=json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
                ImageContent(
                    data=base64.b64encode(media.data).decode("ascii"),
                    mimeType=media.mime_type,
                ),
            ]

        return guarded("archive.read_media", arguments, operation)

    @server.tool(
        name="archive.search_text",
        title="Search Archive text literally",
        description=(
            "Use this when you need literal line-level evidence from UTF-8 text-like Archive "
            "files. This is not semantic or fuzzy search; skipped oversized/non-UTF-8 files are "
            "reported explicitly. Pass next_cursor back unchanged to continue the same "
            "digest-bound result view."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_search_text(
        source: str,
        query: str,
        prefix: str = "",
        limit: int = 50,
        case_sensitive: bool = False,
        cursor: str | None = None,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "query": query,
            "prefix": prefix,
            "limit": limit,
            "case_sensitive": case_sensitive,
            "cursor": cursor,
        }

        def operation() -> dict[str, object]:
            result = source_for(source).search_text(
                query,
                prefix=prefix,
                limit=limit,
                max_bytes=settings.archive_text_max_bytes,
                case_sensitive=case_sensitive,
                cursor=cursor,
            )
            result["hits"] = [
                {
                    "source": source,
                    "provenance": "configured_archive_source",
                    **hit,
                }
                for hit in result["hits"]
            ]
            return {
                "source": source,
                "provenance": "configured_archive_source",
                **result,
            }

        return guarded("archive.search_text", arguments, operation)

    @server.tool(
        name="archive.diff",
        title="Compare live Archive to snapshot",
        description=(
            "Use this when you need a whole-tree live-versus-snapshot comparison by relative "
            "path and SHA-256. This hashes all included files and may be expensive."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_diff(limit: int = 250) -> dict[str, object]:
        arguments = {"limit": limit}

        def operation() -> dict[str, object]:
            live = source_for("live")
            snapshot = source_for("snapshot")
            return live.compare(snapshot).limited(limit)

        return guarded("archive.diff", arguments, operation)

    @server.tool(
        name="archive.diff_detail",
        title="Compare one Archive path",
        description=(
            "Use this when you know one relative path and need its live-versus-snapshot status, "
            "size, and SHA-256 without hashing unrelated files."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_diff_detail(path: str) -> dict[str, object]:
        arguments = {"path": path}

        def operation() -> dict[str, object]:
            normalized = normalize_relative_path(path)
            live_entry = source_for("live").entry(normalized)
            snapshot_entry = source_for("snapshot").entry(normalized)
            if live_entry is None and snapshot_entry is None:
                status = "absent"
            elif live_entry is None:
                status = "removed"
            elif snapshot_entry is None:
                status = "added"
            elif live_entry.sha256 == snapshot_entry.sha256:
                status = "unchanged"
            else:
                status = "changed"
            return {
                "path": normalized,
                "status": status,
                "live": asdict(live_entry) if live_entry is not None else None,
                "snapshot": (
                    asdict(snapshot_entry) if snapshot_entry is not None else None
                ),
            }

        return guarded("archive.diff_detail", arguments, operation)

    @server.tool(
        name="archive.registry_status",
        title="Check canonical Archive registry",
        description=(
            "Use this when you need to verify that the canonical 00_Bootloader/house_index.json "
            "targets actually exist in a selected Archive source and to surface missing or "
            "duplicate registered targets without repairing anything."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_registry_status(source: str = "live") -> dict[str, object]:
        arguments = {"source": source}
        return guarded(
            "archive.registry_status",
            arguments,
            lambda: {
                "source": source,
                **inspect_registry_status(
                    source_for(source),
                    settings.archive_text_max_bytes,
                ),
            },
        )

    @server.tool(
        name="archive.health",
        title="Inspect Archive mechanical health",
        description=(
            "Use this when you need mechanical health evidence: missing registered targets, "
            "routing aliases, coverage canaries, case-fold collisions, and bounded local "
            "Markdown link checks. Findings are descriptive and never repair the Archive."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_health(
        source: str = "live",
        issue_limit: int = 100,
        check_links: bool = True,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "issue_limit": issue_limit,
            "check_links": check_links,
        }
        return guarded(
            "archive.health",
            arguments,
            lambda: {
                "source_name": source,
                **inspect_archive_health(
                    source_for(source),
                    max_bytes=settings.archive_text_max_bytes,
                    issue_limit=issue_limit,
                    check_links=check_links,
                ),
            },
        )

    @server.tool(
        name="archive.write_capabilities",
        title="Inspect canonical Archive write grants",
        description=(
            "Use this before staging or promotion to inspect the exact path prefixes, text "
            "suffixes, byte ceiling, and two-phase authority configured for this deployment."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_write_capabilities() -> dict[str, object]:
        return guarded(
            "archive.write_capabilities",
            {},
            archive_mutations.capabilities,
        )

    @server.tool(
        name="archive.stage_text",
        title="Stage a canonical Archive text change",
        description=(
            "Create a durable MCP-owned create/replace proposal under an operator-granted "
            "Archive prefix. This does not modify the live Archive. The proposal captures its "
            "content digest and the target's current base hash for later promotion. For a "
            "replacement, pass the SHA-256 returned by archive.read_text; for a create, pass "
            "'absent'."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def archive_stage_text(
        path: str,
        content: str,
        expected_base_sha256: str | None = None,
        reason: str = "",
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {
            "path": path,
            "content": content,
            "expected_base_sha256": expected_base_sha256,
            "reason": reason,
        }

        def operation() -> dict[str, object]:
            return {
                "request_id": request_id,
                **archive_mutations.stage_text(
                    path,
                    content,
                    expected_base_sha256=expected_base_sha256,
                    reason=reason,
                ),
            }

        return guarded(
            "archive.stage_text",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="archive.stage_porchlight",
        title="Stage a Porchlight warm snapshot",
        description=(
            "Create durable MCP-owned stages for one searchable Porchlight latest body, one "
            "immutable history body, and one JSON provenance receipt. This does not modify the "
            "live Archive; promote each returned stage explicitly with archive.promote. The "
            "latest body is written under Porchlight/latest so ordinary retrieval can ignore "
            "historical versions by using that prefix."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def archive_stage_porchlight(
        url: str,
        title: str,
        content: str,
        mode: str,
        captured_at: str | None = None,
        previous_snapshot_sha256: str | None = None,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"

        def operation() -> dict[str, object]:
            try:
                artifact = build_snapshot(
                    url,
                    title,
                    content,
                    mode,
                    captured_at,
                    previous_snapshot_sha256,
                )
            except ValueError as exc:
                raise ArchiveError(str(exc)) from exc

            live = source_for("live")
            if live.entry(artifact.history_path) is not None:
                raise ArchiveError(
                    f"Porchlight history path already exists: {artifact.history_path}"
                )
            if live.entry(artifact.receipt_path) is not None:
                raise ArchiveError(
                    f"Porchlight receipt path already exists: {artifact.receipt_path}"
                )

            latest_stage = archive_mutations.stage_text(
                artifact.latest_path,
                artifact.body,
                expected_base_sha256=previous_snapshot_sha256,
                reason=f"Porchlight {artifact.capture_id} latest snapshot",
            )
            history_stage = archive_mutations.stage_text(
                artifact.history_path,
                artifact.body,
                expected_base_sha256="absent",
                reason=f"Porchlight {artifact.capture_id} immutable history",
            )
            receipt_stage = archive_mutations.stage_text(
                artifact.receipt_path,
                artifact.receipt_body,
                expected_base_sha256="absent",
                reason=f"Porchlight {artifact.capture_id} provenance receipt",
            )
            return {
                "request_id": request_id,
                "capture_id": artifact.capture_id,
                "source_key": artifact.source_key,
                "latest_path": artifact.latest_path,
                "history_path": artifact.history_path,
                "receipt_path": artifact.receipt_path,
                "artifacts": [latest_stage, history_stage, receipt_stage],
                "canonical_changed": False,
                "next_step": (
                    "Promote each returned artifact with archive.promote using its stage_id "
                    "and proposal_sha256; staging has not changed live Archive bytes."
                ),
            }

        audit_arguments = {
            "url": url,
            "title": title,
            "mode": mode,
            "captured_at": captured_at,
            "previous_snapshot_sha256": previous_snapshot_sha256,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_bytes": len(content.encode("utf-8")),
        }
        return guarded(
            "archive.stage_porchlight",
            audit_arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="archive.share_porchlight",
        title="Share a Porchlight capture directly",
        description=(
            "Directly share an explicitly selected readable Porchlight capture into the "
            "canonical Modules/Porchlight namespace. The resident action is the consent "
            "gate; the result is an atomic latest/history/receipt bundle with no promotion "
            "step. Optional screenshot data must be a base64-encoded visible viewport PNG."
        ),
        annotations=CANONICAL_WRITE_ANNOTATIONS,
    )
    def archive_share_porchlight(
        url: str,
        title: str,
        content: str,
        mode: str,
        captured_at: str | None = None,
        previous_snapshot_sha256: str | None = None,
        screenshot_base64: str | None = None,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"

        def operation() -> dict[str, object]:
            screenshot = None
            if screenshot_base64 is not None:
                try:
                    screenshot = base64.b64decode(screenshot_base64, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ArchiveError("Porchlight screenshot_base64 is invalid") from exc
            result = porchlight_shares.share(
                PorchlightShareRequest(
                    url=url,
                    title=title,
                    content=content,
                    mode=mode,
                    captured_at=captured_at,
                    previous_snapshot_sha256=previous_snapshot_sha256,
                    screenshot_png=screenshot,
                )
            )
            return {"request_id": request_id, **result}

        arguments = {
            "url": url,
            "title": title,
            "mode": mode,
            "captured_at": captured_at,
            "previous_snapshot_sha256": previous_snapshot_sha256,
            "screenshot_included": screenshot_base64 is not None,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_bytes": len(content.encode("utf-8")),
        }
        return guarded(
            "archive.share_porchlight",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="archive.stage_directory",
        title="Stage a canonical Archive directory",
        description=(
            "Create a durable MCP-owned proposal for one nested directory tree under an "
            "operator-granted Archive prefix. This does not modify the live Archive. The "
            "proposal records the nearest existing parent and every directory that must remain "
            "absent until promotion."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def archive_stage_directory(
        path: str,
        reason: str = "",
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {"path": path, "reason": reason}

        def operation() -> dict[str, object]:
            return {
                "request_id": request_id,
                **archive_mutations.stage_directory(path, reason=reason),
            }

        return guarded(
            "archive.stage_directory",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="archive.stage_list",
        title="List canonical Archive stages",
        description=(
            "List durable Archive proposal metadata without returning staged content. "
            "Listing never changes the Archive or proposal state."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_stage_list(
        status: str = "staged",
        limit: int = 50,
    ) -> dict[str, object]:
        arguments = {"status": status, "limit": limit}
        return guarded(
            "archive.stage_list",
            arguments,
            lambda: archive_mutations.list_stages(status=status, limit=limit),
        )

    @server.tool(
        name="archive.stage_inspect",
        title="Inspect a canonical Archive stage",
        description=(
            "Inspect one durable proposal and revalidate its captured base against the live "
            "Archive. Staged content is omitted unless include_content is true."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_stage_inspect(
        stage_id: str,
        include_content: bool = False,
    ) -> dict[str, object]:
        arguments = {"stage_id": stage_id, "include_content": include_content}
        return guarded(
            "archive.stage_inspect",
            arguments,
            lambda: archive_mutations.inspect_stage(
                stage_id,
                include_content=include_content,
            ),
        )

    @server.tool(
        name="archive.stage_discard",
        title="Discard a canonical Archive stage",
        description=(
            "Discard one still-staged proposal while preserving its durable record. This never "
            "changes the canonical Archive."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def archive_stage_discard(
        stage_id: str,
        reason: str = "",
    ) -> dict[str, object]:
        arguments = {"stage_id": stage_id, "reason": reason}
        return guarded(
            "archive.stage_discard",
            arguments,
            lambda: archive_mutations.discard_stage(stage_id, reason=reason),
        )

    @server.tool(
        name="archive.promote",
        title="Promote a staged canonical Archive change",
        description=(
            "Atomically create or replace one live Archive text file from a durable stage. "
            "Promotion requires the exact proposal digest, an active deployment prefix grant, "
            "and an unchanged captured base hash; otherwise it refuses without writing."
        ),
        annotations=CANONICAL_WRITE_ANNOTATIONS,
    )
    def archive_promote(
        stage_id: str,
        proposal_sha256: str,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {
            "stage_id": stage_id,
            "proposal_sha256": proposal_sha256,
        }

        def operation() -> dict[str, object]:
            return {
                "request_id": request_id,
                **archive_mutations.promote(stage_id, proposal_sha256),
            }

        return guarded(
            "archive.promote",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="archive.promote_directory",
        title="Promote a staged canonical Archive directory",
        description=(
            "Create the nested directory tree captured by one durable stage. Promotion requires "
            "the exact proposal digest, an active deployment prefix grant, and every planned "
            "directory to remain absent. Each mkdir is atomic; a failed tree is rolled back where "
            "the newly created directories remain empty."
        ),
        annotations=CANONICAL_WRITE_ANNOTATIONS,
    )
    def archive_promote_directory(
        stage_id: str,
        proposal_sha256: str,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {
            "stage_id": stage_id,
            "proposal_sha256": proposal_sha256,
        }

        def operation() -> dict[str, object]:
            return {
                "request_id": request_id,
                **archive_mutations.promote_directory(stage_id, proposal_sha256),
            }

        return guarded(
            "archive.promote_directory",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="mount.status",
        title="Inspect named filesystem mounts",
        description=(
            "Inspect the operator-configured external read-only roots available by mount ID. "
            "Named mounts expose bounded relative-path perception and never acquire canonical "
            "Archive semantics."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def mount_status(include_stats: bool = False) -> dict[str, object]:
        arguments = {"include_stats": include_stats}
        return guarded(
            "mount.status",
            arguments,
            lambda: mounts.status(include_stats=include_stats),
        )

    @server.tool(
        name="mount.list",
        title="List paths in a named mount",
        description=(
            "List a cursor-paged relative path view inside one operator-named read-only root. "
            "Pass next_cursor back unchanged to continue the same stable view."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def mount_list(
        mount_id: str,
        prefix: str = "",
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, object]:
        arguments = {
            "mount_id": mount_id,
            "prefix": prefix,
            "limit": limit,
            "cursor": cursor,
        }

        def operation() -> dict[str, object]:
            mount = mounts.get(mount_id)
            return {
                "mount_id": mount_id,
                "provenance": "operator_named_read_only_root",
                **mount.source().list_paths(prefix=prefix, limit=limit, cursor=cursor),
            }

        return guarded("mount.list", arguments, operation)

    @server.tool(
        name="mount.read_text",
        title="Read text from a named mount",
        description=(
            "Read one cursor-paged UTF-8 text-like file from a named read-only root. The "
            "response includes the selected mount ID and content hash as explicit provenance."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def mount_read_text(
        mount_id: str,
        path: str,
        cursor: str | None = None,
        page_bytes: int = 64_000,
    ) -> dict[str, object]:
        arguments = {
            "mount_id": mount_id,
            "path": path,
            "cursor": cursor,
            "page_bytes": page_bytes,
        }

        def operation() -> dict[str, object]:
            mount = mounts.get(mount_id)
            return {
                "mount_id": mount_id,
                "provenance": "operator_named_read_only_root",
                **mount.source().read_text_page(
                    path,
                    mount.text_max_bytes,
                    page_bytes=page_bytes,
                    cursor=cursor,
                ),
            }

        return guarded("mount.read_text", arguments, operation)

    @server.tool(
        name="mount.read_media",
        title="View an image from a named mount",
        description=(
            "Read one bounded, signature-checked PNG, JPEG, GIF, or WebP from a named read-only "
            "root. Absolute paths, symlinks, active formats, and unsupported signatures are refused."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def mount_read_media(
        mount_id: str,
        path: str,
    ) -> list[TextContent | ImageContent]:
        arguments = {"mount_id": mount_id, "path": path}

        def operation() -> list[TextContent | ImageContent]:
            mount = mounts.get(mount_id)
            media = mount.source().read_media(path, max_bytes=mount.media_max_bytes)
            metadata = {
                "mount_id": mount_id,
                "provenance": "operator_named_read_only_root",
                "path": media.path,
                "size": media.size,
                "sha256": media.sha256,
                "mime_type": media.mime_type,
                "byte_ceiling": mount.media_max_bytes,
            }
            return [
                TextContent(text=json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
                ImageContent(
                    data=base64.b64encode(media.data).decode("ascii"),
                    mimeType=media.mime_type,
                ),
            ]

        return guarded("mount.read_media", arguments, operation)

    @server.tool(
        name="mount.search_text",
        title="Search text in a named mount",
        description=(
            "Search cursor-paged literal line evidence inside one named read-only root. This is "
            "bounded retrieval, not semantic search or canonical Archive interpretation."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def mount_search_text(
        mount_id: str,
        query: str,
        prefix: str = "",
        limit: int = 50,
        case_sensitive: bool = False,
        cursor: str | None = None,
    ) -> dict[str, object]:
        arguments = {
            "mount_id": mount_id,
            "query": query,
            "prefix": prefix,
            "limit": limit,
            "case_sensitive": case_sensitive,
            "cursor": cursor,
        }

        def operation() -> dict[str, object]:
            mount = mounts.get(mount_id)
            result = mount.source().search_text(
                query,
                prefix=prefix,
                limit=limit,
                max_bytes=mount.text_max_bytes,
                case_sensitive=case_sensitive,
                cursor=cursor,
            )
            result["hits"] = [
                {
                    "mount_id": mount_id,
                    "provenance": "operator_named_read_only_root",
                    **hit,
                }
                for hit in result["hits"]
            ]
            return {
                "mount_id": mount_id,
                "provenance": "operator_named_read_only_root",
                **result,
            }

        return guarded("mount.search_text", arguments, operation)

    if gametable is not None:

        @server.tool(
            name="game.profiles",
            title="Inspect GameTable profiles",
            description=(
                "Inspect the installed tabletop profiles before creating a game. Profiles define "
                "zones, starting resources, and turn flow; they do not claim to adjudicate a "
                "complete card game's rules."
            ),
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def game_profiles() -> dict[str, object]:
            return guarded("game.profiles", {}, gametable.profiles)

        @server.tool(
            name="game.status",
            title="Inspect GameTable module status",
            description=(
                "Inspect aggregate non-secret GameTable state and the module's explicit privacy "
                "qualification. This does not enumerate games or expose seats, decks, or hands."
            ),
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def game_status() -> dict[str, object]:
            return guarded("game.status", {}, gametable.status)

        @server.tool(
            name="game.create",
            title="Create a private GameTable lobby",
            description=(
                "Create an MCP-owned tabletop lobby from a named profile and explicit seats. The "
                "response returns one bearer development seat token per seat; deliver each token "
                "privately, then have each seat load its own deck. This creates game state only, "
                "not canonical Archive or external-platform state."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_create(
            title: str,
            seats: list[dict[str, Any]],
            profile_id: str = "magic.commander.v0.1",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "title": title,
                "profile_id": profile_id,
                "seats": seats,
            }
            return guarded(
                "game.create",
                arguments,
                lambda: {"request_id": request_id, **gametable.create_game(title=title, profile_id=profile_id, seats=seats)},
                request_id=request_id,
            )

        @server.tool(
            name="game.load_deck",
            title="Load a private GameTable deck",
            description=(
                "Load one seat's deck into an open lobby using that seat's development token and "
                "an exact expected revision. The decklist is not returned in public table views "
                "or public events; every seat must load a deck before game.start."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_load_deck(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            deck: list[str],
            command: list[str] | None = None,
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
                "deck": deck,
                "command": command,
            }
            return guarded(
                "game.load_deck",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.load_deck(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                        deck=deck,
                        command=command,
                    ),
                },
                request_id=request_id,
            )

        @server.tool(
            name="game.start",
            title="Start a GameTable lobby",
            description=(
                "Start a lobby with a seated development token and an exact expected revision. "
                "The server shuffles each library, emits public shuffle commitments, and returns "
                "opening-hand card details only to the seat that supplied the token."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_start(
            game_id: str,
            seat_token: str,
            expected_revision: int,
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
            }
            return guarded(
                "game.start",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.start_game(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                    ),
                },
                request_id=request_id,
            )

        @server.tool(name="game.mulligan", title="Mulligan a private opening hand", annotations=LOCAL_WRITE_ANNOTATIONS)
        def game_mulligan(game_id: str, seat_token: str, expected_revision: int, reason: str | None = None) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            return guarded("game.mulligan", {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "reason": reason}, lambda: {"request_id": request_id, **gametable.mulligan(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, reason=reason)}, request_id=request_id)

        @server.tool(name="game.keep", title="Keep a private opening hand", annotations=LOCAL_WRITE_ANNOTATIONS)
        def game_keep(game_id: str, seat_token: str, expected_revision: int) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            return guarded("game.keep", {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision}, lambda: {"request_id": request_id, **gametable.keep_opening_hand(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision)}, request_id=request_id)

        @server.tool(
            name="game.view",
            title="View a GameTable",
            description=(
                "View public table state without a token, or the private hand/count projection for "
                "the holder of one valid seat token. Opponent hands and all library order stay out "
                "of every ordinary MCP response."
            ),
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def game_view(
            game_id: str,
            seat_token: str | None = None,
        ) -> dict[str, object]:
            arguments = {"game_id": game_id, "seat_token": seat_token}
            return guarded(
                "game.view",
                arguments,
                lambda: gametable.view(game_id=game_id, seat_token=seat_token),
            )

        @server.tool(
            name="game.events",
            title="Read GameTable events",
            description=(
                "Read a sequence-paged event log for one GameTable. The public event chain is "
                "visible to everyone; a valid seat token adds only details explicitly addressed "
                "to that seat, such as cards it drew."
            ),
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def game_events(
            game_id: str,
            after_sequence: int = 0,
            limit: int = 50,
            seat_token: str | None = None,
        ) -> dict[str, object]:
            arguments = {
                "game_id": game_id,
                "after_sequence": after_sequence,
                "limit": limit,
                "seat_token": seat_token,
            }
            return guarded(
                "game.events",
                arguments,
                lambda: gametable.events(
                    game_id=game_id,
                    after_sequence=after_sequence,
                    limit=limit,
                    seat_token=seat_token,
                ),
            )

        @server.tool(
            name="game.act",
            title="Apply a GameTable action",
            description=(
                "Apply one revision-bound action as the current priority seat. Supported action "
                "types are draw, play, move, tap, untap, tap_bundle, counter, damage, and life. The GameTable "
                "checks zone/control/turn bounds but does not adjudicate card text or the full "
                "rules of Magic."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_act(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            action: dict[str, Any],
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
                "action": action,
                "response_view": response_view,
            }
            return guarded(
                "game.act",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.act(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                        action=action,
                        response_view=response_view,
                    ),
                },
                request_id=request_id,
            )

        @server.tool(
            name="game.effect_declare",
            title="Declare a pending GameTable effect",
            description=(
                "Declare a revision-bound spell, activated ability, triggered ability, or manual "
                "effect without asking GameTable to adjudicate card text. Passing around a pending "
                "effect makes it ready for its controller to resolve through explicit table-state operations."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_effect_declare(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            effect: dict[str, Any],
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "effect": effect, "response_view": response_view}
            return guarded(
                "game.effect_declare",
                arguments,
                lambda: {"request_id": request_id, **gametable.declare_effect(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, effect=effect, response_view=response_view)},
                request_id=request_id,
            )

        @server.tool(
            name="game.effect_resolve",
            title="Resolve a pending GameTable effect",
            description=(
                "Resolve the ready top effect with atomic, rules-light operations: effect-driven moves, "
                "private-zone movement, shuffles, privacy-scoped top-card reveals, and bounded random choices. "
                "Set complete=false to record an intermediate batch (for example, reveal then choose); the "
                "controller keeps priority until a later completing call supplies the adjudicated outcome."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_effect_resolve(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            effect_id: str,
            operations: list[dict[str, Any]],
            outcome: str = "resolved",
            complete: bool = True,
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "effect_id": effect_id, "operations": operations, "outcome": outcome, "complete": complete, "response_view": response_view}
            return guarded(
                "game.effect_resolve",
                arguments,
                lambda: {"request_id": request_id, **gametable.resolve_effect(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, effect_id=effect_id, operations=operations, outcome=outcome, complete=complete, response_view=response_view)},
                request_id=request_id,
            )

        @server.tool(
            name="game.repair_state",
            title="Record an explicit GameTable state repair",
            description=(
                "Record an atomic, revision-bound correction to cards or a library owned/controlled by the "
                "token holder after human adjudication. This is a local referee trust boundary, not rules validation; "
                "it is visibly labeled in the event chain and never returns an incidental full hand."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_repair_state(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            operations: list[dict[str, Any]],
            reason: str,
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "operations": operations, "reason": reason, "response_view": response_view}
            return guarded(
                "game.repair_state",
                arguments,
                lambda: {"request_id": request_id, **gametable.repair_state(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, operations=operations, reason=reason, response_view=response_view)},
                request_id=request_id,
            )

        @server.tool(
            name="game.pass_priority",
            title="Pass GameTable priority",
            description=(
                "Pass priority with a valid seat token and current revision. Once every remaining "
                "seat passes, GameTable advances one configured turn step and returns priority to "
                "the active seat."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_pass_priority(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
                "response_view": response_view,
            }
            return guarded(
                "game.pass_priority",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.pass_priority(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                        response_view=response_view,
                    ),
                },
                request_id=request_id,
            )

        @server.tool(
            name="game.yield",
            title="Record a standing GameTable yield",
            description=(
                "Record a bounded standing yield as the current priority seat. Use scope kind="
                "step or turn for the next safe window, or kind=target with an explicit current/next-turn "
                "turn_number and step. All active seats must yield before the table advances to the earliest "
                "agreed target; pending effects and shortcuts remain explicit blockers."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_yield(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            scope: dict[str, Any],
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
                "scope": scope,
                "response_view": response_view,
            }
            return guarded(
                "game.yield",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.yield_priority(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                        scope=scope,
                        response_view=response_view,
                    ),
                },
                request_id=request_id,
            )

        @server.tool(
            name="game.shortcut_propose",
            title="Propose a consented GameTable shortcut",
            description=(
                "As the current priority seat, propose an exact future turn/step target in the "
                "current or next turn. Every active seat must explicitly accept before GameTable "
                "advances there in one compact event; no heuristic skip is performed."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_shortcut_propose(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            target: dict[str, Any],
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "target": target, "response_view": response_view}
            return guarded(
                "game.shortcut_propose",
                arguments,
                lambda: {"request_id": request_id, **gametable.propose_shortcut(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, target=target, response_view=response_view)},
                request_id=request_id,
            )

        @server.tool(
            name="game.shortcut_respond",
            title="Accept or decline a GameTable shortcut",
            description=(
                "Accept or decline one public shortcut proposal with an exact revision. A decline "
                "cancels it; unanimous active-seat consent executes the stated shortcut."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_shortcut_respond(
            game_id: str,
            seat_token: str,
            expected_revision: int,
            proposal_id: str,
            accept: bool,
            response_view: str = "public",
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {"game_id": game_id, "seat_token": seat_token, "expected_revision": expected_revision, "proposal_id": proposal_id, "accept": accept, "response_view": response_view}
            return guarded(
                "game.shortcut_respond",
                arguments,
                lambda: {"request_id": request_id, **gametable.respond_shortcut(game_id=game_id, seat_token=seat_token, expected_revision=expected_revision, proposal_id=proposal_id, accept=accept, response_view=response_view)},
                request_id=request_id,
            )

        @server.tool(
            name="game.concede",
            title="Concede a GameTable game",
            description=(
                "Record the token holder's concession at one exact revision. The result preserves "
                "the event chain and ends a game only when one or no seats remain."
            ),
            annotations=LOCAL_WRITE_ANNOTATIONS,
        )
        def game_concede(
            game_id: str,
            seat_token: str,
            expected_revision: int,
        ) -> dict[str, object]:
            request_id = f"mcp_req_{uuid.uuid4()}"
            arguments = {
                "game_id": game_id,
                "seat_token": seat_token,
                "expected_revision": expected_revision,
            }
            return guarded(
                "game.concede",
                arguments,
                lambda: {
                    "request_id": request_id,
                    **gametable.concede(
                        game_id=game_id,
                        seat_token=seat_token,
                        expected_revision=expected_revision,
                    ),
                },
                request_id=request_id,
            )

    @server.tool(
        name="sense.list",
        title="List registered sense organs",
        description=(
            "Inspect bounded declarative perception contracts. This describes available organs; "
            "it does not invoke observation."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def sense_list() -> dict[str, object]:
        return guarded("sense.list", {}, sense_registry.list)

    @server.tool(
        name="sense.show",
        title="Inspect a sense organ",
        description=(
            "Inspect one organ's modality, activation, consent, payload, retention, and semantic "
            "boundaries without invoking it."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def sense_show(organ_id: str) -> dict[str, object]:
        arguments = {"organ_id": organ_id}
        return guarded("sense.show", arguments, lambda: sense_registry.show(organ_id))

    @server.tool(
        name="sense.can_perceive",
        title="Check sense-organ scope",
        description=(
            "Check whether an explicitly invoked capture fits a registered organ's declared "
            "consent and perception boundary."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def sense_can_perceive(
        organ_id: str,
        request: dict[str, Any],
    ) -> dict[str, object]:
        arguments = {"organ_id": organ_id, "request": request}
        return guarded(
            "sense.can_perceive",
            arguments,
            lambda: sense_registry.can_perceive(organ_id, request),
        )

    @server.tool(
        name="lanternslide.status",
        title="Inspect Lanternslide catalog state",
        description=(
            "Inspect bounded Lanternslide scan progress and catalog digests. This does not read "
            "image payloads and never performs automatic visual retrieval."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def lanternslide_status() -> dict[str, object]:
        return guarded("lanternslide.status", {}, lambda: lanternslide_service().status())

    @server.tool(
        name="lanternslide.scan",
        title="Scan Archive images into Lanternslide",
        description=(
            "Explicitly scan a bounded batch of passive PNG, JPEG, GIF, and WebP files under "
            "the configured source prefix. Resume an incomplete scan with its scan_id. The "
            "source images remain immutable; only an MCP-owned metadata catalog changes."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def lanternslide_scan(scan_id: str | None = None) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {"scan_id": scan_id}

        def operation() -> dict[str, object]:
            result = lanternslide_service().scan(scan_id=scan_id)
            receipt_garden.append(
                request_id=request_id,
                operation="lanternslide.scan",
                edges=[
                    {
                        "type": "observed",
                        "source": "configured_live_archive",
                        "target": settings.lanternslide_source_prefix or ".",
                        "evidence_scope": "bounded_image_metadata",
                    },
                    {
                        "type": "stored",
                        "source": "lanternslide.scan",
                        "target": "mcp_owned_local_catalog",
                        "evidence_scope": "metadata_only",
                    },
                    {
                        "type": "omitted",
                        "source": "lanternslide.scan",
                        "target": "receipt_and_catalog",
                        "evidence_scope": "raw_image_bytes_source_pixels_thumbnails",
                    },
                ],
                omitted=("raw_image_bytes", "source_pixels", "thumbnails"),
                safe_summary={
                    "scan_id": result["scan_id"],
                    "complete": result["complete"],
                    "candidate_total": result["candidate_total"],
                    "indexed_total": result["indexed_total"],
                    "omitted_total": result["omitted_total"],
                    "candidate_manifest_sha256": result["candidate_manifest_sha256"],
                    "catalog_sha256": result["catalog_sha256"],
                },
            )
            return {
                "request_id": request_id,
                "scan_id": result["scan_id"],
                "complete": result["complete"],
                "candidate_total": result["candidate_total"],
                "next_offset": result["next_offset"],
                "indexed_total": result["indexed_total"],
                "omitted_total": result["omitted_total"],
                "candidate_manifest_sha256": result["candidate_manifest_sha256"],
                "catalog_sha256": result["catalog_sha256"],
            }

        return guarded(
            "lanternslide.scan",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="lanternslide.find",
        title="Find Lanternslide images",
        description=(
            "Find complete-catalog image metadata by literal, case-insensitive path text. "
            "This is not semantic visual search."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def lanternslide_find(query: str, unique_only: bool = False) -> dict[str, object]:
        arguments = {"query": query, "unique_only": unique_only}
        return guarded(
            "lanternslide.find",
            arguments,
            lambda: lanternslide_service().find(query, unique_only=unique_only),
        )

    @server.tool(
        name="lanternslide.deal",
        title="Deal Lanternslide images",
        description=(
            "Return a deterministic bounded random deal from the complete metadata catalog. "
            "The seed is explicit and no image bytes are returned."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def lanternslide_deal(
        count: int,
        seed: str,
        unique_only: bool = True,
    ) -> dict[str, object]:
        arguments = {"count": count, "seed": seed, "unique_only": unique_only}
        return guarded(
            "lanternslide.deal",
            arguments,
            lambda: lanternslide_service().deal(count, seed, unique_only=unique_only),
        )

    @server.tool(
        name="lanternslide.contact_sheet",
        title="Build a Lanternslide contact sheet",
        description=(
            "Build an in-memory PNG contact sheet for up to 16 explicitly selected image IDs. "
            "The PNG is returned in the response and is not written to the Archive."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def lanternslide_contact_sheet(
        image_ids: list[str],
    ) -> list[TextContent | ImageContent]:
        arguments = {"image_ids": image_ids}

        def operation() -> list[TextContent | ImageContent]:
            sheet = lanternslide_service().contact_sheet(image_ids)
            metadata = {
                "image_ids": sheet["image_ids"],
                "width": sheet["width"],
                "height": sheet["height"],
                "mime_type": sheet["mime_type"],
                "size": len(sheet["data"]),
                "retention": "response_only",
            }
            return [
                TextContent(text=json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
                ImageContent(
                    data=base64.b64encode(sheet["data"]).decode("ascii"),
                    mimeType="image/png",
                ),
            ]

        return guarded("lanternslide.contact_sheet", arguments, operation)

    @server.tool(
        name="lanternslide.stage_catalog",
        title="Stage the Lanternslide catalog",
        description=(
            "Stage the complete Lanternslide JSONL metadata catalog through the canonical Archive "
            "two-phase boundary. If its parent directory is absent, this first returns an "
            "explicit directory proposal; promote that proposal, then call this tool again."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def lanternslide_stage_catalog(reason: str = "") -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        arguments = {"reason": reason}

        def operation() -> dict[str, object]:
            service = lanternslide_service()
            content = service.catalog_export(max_bytes=settings.archive_write_max_bytes)
            catalog_path = settings.lanternslide_catalog_path
            parent = PurePosixPath(catalog_path).parent
            live_root = settings.live_archive_root
            assert live_root is not None
            parent_path = live_root.joinpath(*parent.parts)
            if not parent_path.is_dir():
                directory_stage = archive_mutations.stage_directory(
                    parent.as_posix(),
                    reason=reason or "Prepare Lanternslide catalog directory",
                )
                return {
                    "request_id": request_id,
                    "catalog_path": catalog_path,
                    "directory_stage": directory_stage,
                    "canonical_changed": False,
                    "next_step": (
                        "Promote directory_stage with archive.promote_directory, then call "
                        "lanternslide.stage_catalog again."
                    ),
                }
            live = source_for("live")
            current = live.entry(catalog_path)
            stage = archive_mutations.stage_text(
                catalog_path,
                content,
                expected_base_sha256=current.sha256 if current is not None else "absent",
                reason=reason or "Stage Lanternslide metadata catalog",
            )
            return {
                "request_id": request_id,
                "catalog_path": catalog_path,
                "base_catalog_sha256": current.sha256 if current is not None else None,
                "catalog_bytes": len(content.encode("utf-8")),
                "stage": stage,
                "canonical_changed": False,
            }

        return guarded(
            "lanternslide.stage_catalog",
            arguments,
            operation,
            request_id=request_id,
        )

    @server.tool(
        name="runtime.list",
        title="List connected Runtime houses",
        description=(
            "Inspect the operator-defined Runtime registry, its default route, and the live "
            "status of each named house. Legacy single-home environment configuration remains "
            "available as the default route."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_list() -> dict[str, object]:
        return guarded("runtime.list", {}, runtime_registry.list)

    @server.tool(
        name="runtime.status",
        title="Inspect Runtime linkage",
        description=(
            "Use this when you need to know whether a VESTIGIA Runtime home is linked, which "
            "resident/room it belongs to, and the digest/count of its currently projectable "
            "read-only capability surface."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_status(runtime_id: str | None = None) -> dict[str, object]:
        arguments = {"runtime_id": runtime_id}
        return guarded(
            "runtime.status",
            arguments,
            lambda: runtime_registry.status(runtime_id),
        )

    @server.tool(
        name="runtime.capabilities",
        title="Inspect projected Runtime capabilities",
        description=(
            "Use this when you need the Runtime-owned read-only MCP projection. With no target, "
            "returns a compact index; with target, returns the MCP arguments schema, the native "
            "Runtime envelope schema, and explicit wrapper-owned fields. Runtime "
            "CapabilityRegistry remains authoritative."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_capabilities(
        target: str | None = None,
        runtime_id: str | None = None,
    ) -> dict[str, object]:
        arguments = {"target": target, "runtime_id": runtime_id}
        return guarded(
            "runtime.capabilities",
            arguments,
            lambda: runtime_registry.capabilities(target, runtime_id=runtime_id),
        )

    @server.tool(
        name="runtime.call",
        title="Call one projected Runtime read",
        description=(
            "Use this after inspecting runtime.capabilities when you need to execute one Runtime "
            "capability through Runtime's own HousePort. Supply only the focused capability's "
            "input_schema fields inside arguments; MCP supplies action and after. The bridge "
            "rejects anything not already classified by Runtime as callable, confirmation-free, "
            "non-outward read behavior."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_call(
        action: str,
        arguments: dict[str, Any] | None = None,
        runtime_id: str | None = None,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        audit_arguments = {
            "action": action,
            "arguments": arguments or {},
            "runtime_id": runtime_id,
        }
        return guarded(
            "runtime.call",
            audit_arguments,
            lambda: runtime_registry.call(
                action=action,
                arguments=arguments,
                request_id=request_id,
                runtime_id=runtime_id,
            ),
            request_id=request_id,
        )

    @server.tool(
        name="runtime.write_capabilities",
        title="Inspect bounded Runtime writes",
        description=(
            "Use this before runtime.write to inspect the exact Runtime-owned local mutation "
            "contracts granted by this MCP deployment. A focused result separates the MCP "
            "arguments schema from the native Runtime envelope and names wrapper-owned fields. "
            "An empty result means the operator has not configured any write actions."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_write_capabilities(
        target: str | None = None,
        runtime_id: str | None = None,
    ) -> dict[str, object]:
        arguments = {"target": target, "runtime_id": runtime_id}
        return guarded(
            "runtime.write_capabilities",
            arguments,
            lambda: runtime_registry.write_capabilities(
                target, runtime_id=runtime_id
            ),
        )

    @server.tool(
        name="runtime.write",
        title="Call one bounded Runtime write",
        description=(
            "Use this only after runtime.write_capabilities. It dispatches one explicitly "
            "allowlisted, non-outward Runtime-local mutation through Runtime's own HousePort, "
            "which still enforces workspace roots, byte ceilings, optimistic hashes, schemas, "
            "and receipts. Supply only the focused input_schema fields inside arguments; MCP "
            "owns action/after and preserves a shared request ID."
        ),
        annotations=LOCAL_WRITE_ANNOTATIONS,
    )
    def runtime_write(
        action: str,
        arguments: dict[str, Any] | None = None,
        runtime_id: str | None = None,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        audit_arguments = {
            "action": action,
            "arguments": arguments or {},
            "runtime_id": runtime_id,
        }
        return guarded(
            "runtime.write",
            audit_arguments,
            lambda: runtime_registry.write(
                action=action,
                arguments=arguments,
                request_id=request_id,
                runtime_id=runtime_id,
            ),
            request_id=request_id,
        )

    @server.tool(
        name="receipts.recent",
        title="Read recent VESTIGIA receipts",
        description=(
            "Use this when you need recent MCP capability receipts for provenance or debugging. "
            "Results contain argument hashes, not raw tool arguments; request_id can join a "
            "cross-layer Runtime projected call to its MCP witness."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def receipts_recent(
        limit: int = 25,
        capability: str | None = None,
        outcome: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, object]:
        arguments = {
            "limit": limit,
            "capability": capability,
            "outcome": outcome,
            "request_id": request_id,
        }
        return guarded(
            "receipts.recent",
            arguments,
            lambda: ledger.recent(
                limit=limit,
                capability=capability,
                outcome=outcome,
                request_id=request_id,
            ),
        )

    @server.tool(
        name="receipts.trace",
        title="Trace MCP provenance edges",
        description=(
            "Inspect the MCP-owned Receipt Garden for one request ID. Results expose bounded "
            "typed provenance edges and omissions; included evidence is not claimed to have "
            "caused a response. Raw arguments and payloads are never returned."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def receipts_trace(request_id: str) -> dict[str, object]:
        arguments = {"request_id": request_id}
        return guarded(
            "receipts.trace",
            arguments,
            lambda: receipt_garden.trace(request_id),
        )

    @server.tool(
        name="audit.show",
        title="Inspect one MCP audit receipt",
        description=(
            "Use this when you have an MCP audit event_id and need its exact evidence record, "
            "including request_id and deciding authority when present. A receipt remains "
            "operational evidence; inspecting it does not make it autobiographical memory."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def audit_show(event_id: str) -> dict[str, object]:
        arguments = {"event_id": event_id}
        return guarded(
            "audit.show",
            arguments,
            lambda: ledger.show(event_id),
        )

    @server.tool(
        name="system.identity",
        title="Inspect exact house identity",
        description=(
            "Use this before trusting provenance-sensitive results to locate the exact MCP "
            "deployment: package/deployment identity, non-secret config fingerprint, executable "
            "policy digest, bounded Archive witnesses, Runtime linkage, and qualification limits."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def system_identity() -> dict[str, object]:
        return guarded(
            "system.identity",
            {},
            lambda: build_system_identity(
                server_version=__version__,
                settings=settings,
                policy=policy,
                source_for=source_for,
                runtime_status=runtime_registry.status,
            ),
        )

    @server.tool(
        name="house.glance",
        title="Glance around the current house",
        description=(
            "Use this as a compact first orientation call for a bell or autonomous turn. It "
            "summarizes live/snapshot presence, quick Archive health, Runtime linkage, recent MCP "
            "receipts/errors, and explicitly names proprioceptive surfaces not yet implemented."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def house_glance() -> dict[str, object]:
        def operation() -> dict[str, object]:
            archive_view: dict[str, object] = {}
            warnings: list[dict[str, object]] = []
            for name in ("live", "snapshot"):
                try:
                    source = source_for(name)
                    archive_view[name] = {
                        "available": True,
                        **asdict(source.stats()),
                        "clock": source_clock(source),
                    }
                except ArchiveError as exc:
                    configured = (
                        settings.live_archive_root is not None
                        if name == "live"
                        else settings.snapshot_archive_root is not None
                    )
                    archive_view[name] = {
                        "available": False,
                        "configured": configured,
                        "error": str(exc),
                    }
                    if configured:
                        warnings.append(
                            {
                                "family": "archive",
                                "source": name,
                                "detail": str(exc),
                            }
                        )

            quick_health: dict[str, object] | None = None
            try:
                quick_health = inspect_archive_health(
                    source_for("live"),
                    max_bytes=settings.archive_text_max_bytes,
                    issue_limit=12,
                    check_links=False,
                )
                health_summary = quick_health.get("summary", {})
                if isinstance(health_summary, dict) and int(
                    health_summary.get("issue_count", 0)
                ):
                    warnings.append(
                        {
                            "family": "archive_health",
                            "issue_count": health_summary.get("issue_count"),
                            "detail": "Call archive.health with link checks for full diagnostics.",
                        }
                    )
            except ArchiveError as exc:
                warnings.append({"family": "archive_health", "detail": str(exc)})

            runtime = runtime_registry.status()
            if runtime.get("configured") and not runtime.get("available"):
                warnings.append(
                    {
                        "family": "runtime",
                        "detail": runtime.get("error", "Runtime linkage unavailable"),
                    }
                )

            staged_patch_support = False
            if runtime.get("available"):
                try:
                    runtime_registry.capabilities("fs.patch_list")
                    staged_patch_support = True
                except RuntimeBridgeError:
                    staged_patch_support = False

            recent = ledger.recent(limit=5)
            recent_errors = ledger.recent(limit=5, outcome="error")
            canonical_stages = archive_mutations.list_stages(
                status="staged",
                limit=5,
            )
            archive_write = archive_mutations.capabilities()
            malformed = max(
                int(recent.get("malformed_lines", 0)),
                int(recent_errors.get("malformed_lines", 0)),
            )
            if malformed:
                warnings.append(
                    {
                        "family": "audit",
                        "malformed_lines": malformed,
                        "detail": "MCP audit ledger contains malformed lines.",
                    }
                )

            return {
                "schema_version": "vestigia.house-glance.v0.1",
                "generated_at": datetime.now(UTC).isoformat(),
                "authority": "descriptive_projection_only",
                "archive": archive_view,
                "archive_health": quick_health,
                "runtime": runtime,
                "runtimes": runtime_registry.list(),
                "mounts": mounts.status(),
                "gametable": (
                    gametable.status()
                    if gametable is not None
                    else {
                        "schema_version": "vestigia.gametable.v0.1",
                        "enabled": False,
                        "reason": "VESTIGIA_MCP_GAMETABLE_ENABLED is not enabled for this deployment.",
                    }
                ),
                "audit": {
                    "recent_events": recent.get("events", []),
                    "recent_errors": recent_errors.get("events", []),
                    "receipt_is_memory": False,
                },
                "meaningful_diff": {
                    "computed": False,
                    "reason": (
                        "house.glance avoids whole-tree rehashing; call archive.diff or "
                        "archive.diff_detail when change evidence is needed."
                    ),
                },
                "staged_patches": {
                    "supported": staged_patch_support,
                    "open_count": None,
                    "stage_granted": "fs.stage_patch"
                    in runtime_registry.write_actions,
                    "surface": (
                        "fs.stage_patch / fs.patch_list / fs.patch_preview / "
                        "fs.patch_validate / fs.patch_discard"
                    ),
                    "apply_capability_available": False,
                },
                "canonical_archive_stages": {
                    "open_count": canonical_stages["total"],
                    "recent": canonical_stages["stages"],
                    "promotion_configured": archive_write["promotion_configured"],
                    "write_prefixes": archive_write["write_prefixes"],
                    "surface": (
                        "archive.stage_text / archive.stage_directory / archive.stage_list / "
                        "archive.stage_inspect / archive.stage_discard / archive.promote / "
                        "archive.promote_directory"
                    ),
                },
                "watch_subscriptions": {
                    "supported": False,
                    "roadmap_surface": "durable watch spec with cursor/last-seen receipt",
                },
                "warnings": warnings,
            }

        return guarded("house.glance", {}, operation)

    @server.tool(
        name="vestigia.status",
        title="Inspect VESTIGIA MCP deployment",
        description=(
            "Use this when you need this deployment's server version, deployment identity, "
            "executable MCP policy surface, Archive configuration, optional Runtime linkage, "
            "audit-ledger health, and a cache-membrane canary for newly deployed capabilities."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def vestigia_status() -> dict[str, object]:
        def operation() -> dict[str, object]:
            capabilities = policy.capabilities()
            return {
                "server": {
                    "name": "VESTIGIA MCP",
                    "version": __version__,
                    "effect_ceiling": (
                        "canonical_archive_act"
                        if settings.archive_write_prefixes
                        else (
                            "bounded_local_act"
                            if runtime_registry.any_write_actions or gametable is not None
                            else "perceive"
                        )
                    ),
                    "tool_only": True,
                },
                "deployment_id": settings.deployment_id,
                "archive": {
                    "live_configured": settings.live_archive_root is not None,
                    "snapshot_configured": settings.snapshot_archive_root is not None,
                    "promotion_configured": bool(settings.archive_write_prefixes),
                    "write_prefixes": list(settings.archive_write_prefixes),
                    "write_max_bytes": settings.archive_write_max_bytes,
                },
                "mounts": mounts.status(),
                "gametable": (
                    gametable.status()
                    if gametable is not None
                    else {
                        "enabled": False,
                        "reason": "VESTIGIA_MCP_GAMETABLE_ENABLED is not enabled for this deployment.",
                    }
                ),
                "runtime": {
                    "configured": runtime_registry.configured,
                    "home": runtime_registry.configured_home,
                    "env_file": runtime_registry.configured_env_file,
                    "registry": runtime_registry.list(),
                    "projection": (
                        "runtime_owned_read_plus_opt_in_local_mutation"
                        if runtime_registry.any_write_actions
                        else "runtime_owned_read_only"
                    ),
                    "write_actions": list(runtime_registry.write_actions),
                },
                "policy": {
                    "capability_count": len(capabilities),
                    "capabilities": [
                        {
                            "name": capability.name,
                            "effect": capability.effect.value,
                            "default": capability.default.value,
                        }
                        for capability in capabilities
                    ],
                },
                "audit": ledger.summary(),
                "proprioception": {
                    "surface_version": "v0.1",
                    "new_native_tools": [
                        "archive.health",
                        "archive.write_capabilities",
                        "archive.stage_text",
                        "archive.stage_directory",
                        "archive.stage_list",
                        "archive.stage_inspect",
                        "archive.stage_discard",
                        "archive.promote",
                        "archive.promote_directory",
                        "mount.status",
                        "mount.list",
                        "mount.read_text",
                        "mount.read_media",
                        "mount.search_text",
                        "runtime.list",
                        "audit.show",
                        "system.identity",
                        "house.glance",
                    ]
                    + (
                        [
                            "game.profiles",
                            "game.status",
                            "game.create",
                            "game.load_deck",
                            "game.start",
                            "game.view",
                            "game.events",
                            "game.act",
                            "game.pass_priority",
                            "game.yield",
                            "game.concede",
                        ]
                        if gametable is not None
                        else []
                    ),
                    "tool_catalog_cache_note": (
                        "This live policy surface may be newer than a host/thread's cached MCP "
                        "tool catalog. Compare this list/count to host-visible descriptors."
                    ),
                },
            }

        return guarded("vestigia.status", {}, operation)

    def manifest_resource(source_name: str) -> str:
        try:
            capability = policy.require_allowed("archive.read_text")
            content = source_for(source_name).read_text(
                "manifest.md",
                max_bytes=settings.archive_text_max_bytes,
            )
            ledger.record(
                capability,
                {"source": source_name, "path": "manifest.md", "via": "resource"},
                "ok",
            )
            return content
        except (PolicyDenied, ArchiveError) as exc:
            raise ResourceError(str(exc)) from exc

    @server.resource("vestigia://archive/live/manifest")
    def live_manifest() -> str:
        """The immediately-current live Archive manifest."""
        return manifest_resource("live")

    @server.resource("vestigia://archive/snapshot/manifest")
    def snapshot_manifest() -> str:
        """The latest configured snapshot Archive manifest."""
        return manifest_resource("snapshot")

    return server


mcp = create_server()


if __name__ == "__main__":
    mcp.run()
