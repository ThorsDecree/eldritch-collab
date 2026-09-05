from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .adapters.archive import ArchiveError, ArchiveSource, normalize_relative_path
from .adapters.runtime import RuntimeBridge, RuntimeBridgeError
from .audit import AuditError, AuditLedger
from .config import Settings
from .health import (
    archive_health as inspect_archive_health,
    registry_status as inspect_registry_status,
    source_clock,
)
from .identity import system_identity as build_system_identity
from .policy import PolicyDenied, PolicyEngine


T = TypeVar("T")
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
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
    policy = PolicyEngine()
    ledger = AuditLedger(settings.state_dir, settings.deployment_id)
    runtime_bridge = RuntimeBridge(
        settings.runtime_home,
        settings.runtime_env_file,
        deployment_id=settings.deployment_id,
    )
    server = MCPServer(
        "VESTIGIA MCP",
        instructions=(
            "Local-first VESTIGIA capability broker. Tool descriptions are not authority; "
            "live policy is. Archive tools are native PERCEIVE capabilities. Runtime tools are "
            "a read-only projection of Runtime's own executable CapabilityRegistry through "
            "HousePort; MCP does not define a parallel Runtime capability ontology. Health, "
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
        except (ArchiveError, AuditError, RuntimeBridgeError) as exc:
            ledger.record(
                capability,
                arguments,
                "error",
                request_id=request_id,
                detail=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception:
            ledger.record(
                capability,
                arguments,
                "error",
                request_id=request_id,
                detail="unexpected_exception",
            )
            raise
        ledger.record(
            capability,
            arguments,
            "ok",
            request_id=request_id,
        )
        return result

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
            "Archive, optionally under one prefix."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_list(
        source: str,
        prefix: str = "",
        limit: int = 500,
    ) -> dict[str, object]:
        arguments = {"source": source, "prefix": prefix, "limit": limit}
        return guarded(
            "archive.list",
            arguments,
            lambda: source_for(source).list_paths(prefix=prefix, limit=limit),
        )

    @server.tool(
        name="archive.read_text",
        title="Read Archive text",
        description=(
            "Use this when you know the relative path of one UTF-8 text-like Archive file and "
            "need its bounded contents."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_read_text(source: str, path: str) -> dict[str, object]:
        arguments = {"source": source, "path": path}

        def operation() -> dict[str, object]:
            content = source_for(source).read_text(
                path,
                max_bytes=settings.archive_text_max_bytes,
            )
            return {"source": source, "path": path, "content": content}

        return guarded("archive.read_text", arguments, operation)

    @server.tool(
        name="archive.search_text",
        title="Search Archive text literally",
        description=(
            "Use this when you need literal line-level evidence from UTF-8 text-like Archive "
            "files. This is not semantic or fuzzy search; skipped oversized/non-UTF-8 files are "
            "reported explicitly."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def archive_search_text(
        source: str,
        query: str,
        prefix: str = "",
        limit: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, object]:
        arguments = {
            "source": source,
            "query": query,
            "prefix": prefix,
            "limit": limit,
            "case_sensitive": case_sensitive,
        }

        def operation() -> dict[str, object]:
            result = source_for(source).search_text(
                query,
                prefix=prefix,
                limit=limit,
                max_bytes=settings.archive_text_max_bytes,
                case_sensitive=case_sensitive,
            )
            return {"source": source, **result}

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
        name="runtime.status",
        title="Inspect Runtime linkage",
        description=(
            "Use this when you need to know whether a VESTIGIA Runtime home is linked, which "
            "resident/room it belongs to, and the digest/count of its currently projectable "
            "read-only capability surface."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_status() -> dict[str, object]:
        return guarded("runtime.status", {}, runtime_bridge.status)

    @server.tool(
        name="runtime.capabilities",
        title="Inspect projected Runtime capabilities",
        description=(
            "Use this when you need the Runtime-owned read-only MCP projection. With no target, "
            "returns a compact index; with target, returns that Runtime capability's full live "
            "contract and input schema. Runtime CapabilityRegistry remains authoritative."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_capabilities(target: str | None = None) -> dict[str, object]:
        arguments = {"target": target}
        return guarded(
            "runtime.capabilities",
            arguments,
            lambda: runtime_bridge.capabilities(target),
        )

    @server.tool(
        name="runtime.call",
        title="Call one projected Runtime read",
        description=(
            "Use this after inspecting runtime.capabilities when you need to execute one Runtime "
            "capability through Runtime's own HousePort. The bridge rejects anything not already "
            "classified by Runtime as callable, confirmation-free, non-outward read behavior."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def runtime_call(
        action: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        request_id = f"mcp_req_{uuid.uuid4()}"
        audit_arguments = {"action": action, "arguments": arguments or {}}
        return guarded(
            "runtime.call",
            audit_arguments,
            lambda: runtime_bridge.call(
                action=action,
                arguments=arguments,
                request_id=request_id,
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
                runtime_status=runtime_bridge.status,
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

            runtime = runtime_bridge.status()
            if runtime.get("configured") and not runtime.get("available"):
                warnings.append(
                    {
                        "family": "runtime",
                        "detail": runtime.get("error", "Runtime linkage unavailable"),
                    }
                )

            recent = ledger.recent(limit=5)
            recent_errors = ledger.recent(limit=5, outcome="error")
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
                    "supported": False,
                    "open_count": None,
                    "roadmap_surface": "fs.stage_patch / fs.patch_preview / fs.patch_apply",
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
                    "effect_ceiling": "perceive",
                    "tool_only": True,
                },
                "deployment_id": settings.deployment_id,
                "archive": {
                    "live_configured": settings.live_archive_root is not None,
                    "snapshot_configured": settings.snapshot_archive_root is not None,
                },
                "runtime": {
                    "configured": runtime_bridge.configured,
                    "home": runtime_bridge.configured_home,
                    "env_file": runtime_bridge.configured_env_file,
                    "projection": "runtime_owned_read_only",
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
                        "audit.show",
                        "system.identity",
                        "house.glance",
                    ],
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
