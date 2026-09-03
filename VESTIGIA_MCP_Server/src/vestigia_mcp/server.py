from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from .adapters.archive import ArchiveError, ArchiveSource, normalize_relative_path
from .audit import AuditLedger
from .config import Settings
from .policy import PolicyDenied, PolicyEngine


T = TypeVar("T")


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
    server = MCPServer(
        "VESTIGIA MCP",
        instructions=(
            "Local-first VESTIGIA capability broker. Tool descriptions are not authority; "
            "the server's live policy is authority. The initial Archive surface is read-only."
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
    ) -> T:
        try:
            capability = policy.require_allowed(capability_name)
        except PolicyDenied as exc:
            raise ToolError(str(exc)) from exc
        try:
            result = operation()
        except ArchiveError as exc:
            ledger.record(
                capability,
                arguments,
                "error",
                detail=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception:
            ledger.record(
                capability,
                arguments,
                "error",
                detail="unexpected_exception",
            )
            raise
        ledger.record(capability, arguments, "ok")
        return result

    @server.tool(name="archive.status")
    def archive_status() -> dict[str, object]:
        """Inspect live/snapshot Archive configuration and basic read-only metadata."""

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

    @server.tool(name="archive.list")
    def archive_list(
        source: str,
        prefix: str = "",
        limit: int = 500,
    ) -> dict[str, object]:
        """List relative file paths under a live or snapshot Archive source."""
        arguments = {"source": source, "prefix": prefix, "limit": limit}
        return guarded(
            "archive.list",
            arguments,
            lambda: source_for(source).list_paths(prefix=prefix, limit=limit),
        )

    @server.tool(name="archive.read_text")
    def archive_read_text(source: str, path: str) -> dict[str, object]:
        """Read one bounded UTF-8 text file from a configured Archive source."""
        arguments = {"source": source, "path": path}

        def operation() -> dict[str, object]:
            content = source_for(source).read_text(
                path,
                max_bytes=settings.archive_text_max_bytes,
            )
            return {"source": source, "path": path, "content": content}

        return guarded("archive.read_text", arguments, operation)

    @server.tool(name="archive.diff")
    def archive_diff(limit: int = 250) -> dict[str, object]:
        """Hash and compare the live Archive against the configured snapshot."""
        arguments = {"limit": limit}

        def operation() -> dict[str, object]:
            live = source_for("live")
            snapshot = source_for("snapshot")
            return live.compare(snapshot).limited(limit)

        return guarded("archive.diff", arguments, operation)

    @server.tool(name="archive.diff_detail")
    def archive_diff_detail(path: str) -> dict[str, object]:
        """Compare one path across live and snapshot without hashing unrelated files."""
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
