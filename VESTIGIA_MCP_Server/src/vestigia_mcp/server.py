from __future__ import annotations

import json
import uuid
from dataclasses import asdict
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
from .policy import PolicyDenied, PolicyEngine


T = TypeVar("T")
CANONICAL_REGISTRY_PATH = "00_Bootloader/house_index.json"
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


def _registry_status(source: ArchiveSource, max_bytes: int) -> dict[str, object]:
    try:
        registry = json.loads(source.read_text(CANONICAL_REGISTRY_PATH, max_bytes=max_bytes))
    except json.JSONDecodeError as exc:
        raise ArchiveError("Canonical house registry is not valid JSON") from exc
    if not isinstance(registry, dict):
        raise ArchiveError("Canonical house registry must be a JSON object")

    paths = source.all_paths()
    file_paths = set(paths)
    checks: list[dict[str, object]] = []
    labels_by_path: dict[str, list[str]] = {}

    def add_check(label: str, raw_path: object, kind: str) -> None:
        if not isinstance(raw_path, str):
            raise ArchiveError(f"Registry target is not a string: {label}")
        normalized = normalize_relative_path(raw_path)
        if kind == "directory":
            boundary = normalized.rstrip("/") + "/"
            present = normalized in file_paths or any(
                path.startswith(boundary) for path in paths
            )
        else:
            present = normalized in file_paths
        checks.append(
            {
                "label": label,
                "path": normalized,
                "kind": kind,
                "present": present,
            }
        )
        labels_by_path.setdefault(normalized, []).append(label)

    anchors = registry.get("anchors", {})
    if not isinstance(anchors, dict):
        raise ArchiveError("Registry anchors must be an object")
    for name, path in sorted(anchors.items()):
        add_check(f"anchor:{name}", path, "file")

    residents = registry.get("residents", {})
    if not isinstance(residents, dict):
        raise ArchiveError("Registry residents must be an object")
    for resident_name, record in sorted(residents.items()):
        if not isinstance(record, dict):
            raise ArchiveError(f"Resident registry entry must be an object: {resident_name}")
        if "shell" in record:
            add_check(f"resident:{resident_name}:shell", record["shell"], "directory")
        if "breathprint" in record:
            add_check(
                f"resident:{resident_name}:breathprint",
                record["breathprint"],
                "file",
            )
        if "index" in record:
            add_check(f"resident:{resident_name}:index", record["index"], "file")

    garden = registry.get("garden_breathprints", {})
    if not isinstance(garden, dict):
        raise ArchiveError("Registry garden_breathprints must be an object")
    for name, path in sorted(garden.items()):
        add_check(f"garden_breathprint:{name}", path, "file")

    missing = [check for check in checks if not check["present"]]
    duplicates = [
        {"path": path, "labels": labels}
        for path, labels in sorted(labels_by_path.items())
        if len(labels) > 1
    ]
    return {
        "registry_path": CANONICAL_REGISTRY_PATH,
        "schema_version": registry.get("schema_version"),
        "generated": registry.get("generated"),
        "archive_root": registry.get("archive_root"),
        "summary": {
            "anchors": len(anchors),
            "residents": len(residents),
            "garden_breathprints": len(garden),
            "registered_targets": len(checks),
            "present": len(checks) - len(missing),
            "missing": len(missing),
            "duplicate_targets": len(duplicates),
        },
        "missing": missing,
        "duplicate_targets": duplicates,
    }


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
            "HousePort; MCP does not define a parallel Runtime capability ontology. Prefer "
            "diff_detail for one known Archive path and diff for whole-tree comparison. Text "
            "search is literal evidence retrieval, not semantic similarity."
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
                **_registry_status(
                    source_for(source),
                    settings.archive_text_max_bytes,
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
        name="vestigia.status",
        title="Inspect VESTIGIA MCP deployment",
        description=(
            "Use this when you need this deployment's server version, deployment identity, "
            "executable MCP policy surface, Archive configuration, optional Runtime linkage, "
            "and audit-ledger health."
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
