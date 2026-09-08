from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .adapters.archive import normalize_relative_path


def _optional_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _action_allowlist(name: str) -> tuple[str, ...]:
    values = {
        item.strip().lower()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    }
    return tuple(sorted(values))


def _path_prefix_allowlist(name: str) -> tuple[str, ...]:
    values = {
        normalize_relative_path(item.strip()).rstrip("/")
        for item in os.getenv(name, "").split(",")
        if item.strip()
    }
    return tuple(sorted(values))


@dataclass(frozen=True)
class Settings:
    live_archive_root: Path | None
    snapshot_archive_root: Path | None
    state_dir: Path
    deployment_id: str
    archive_text_max_bytes: int = 1_000_000
    archive_media_max_bytes: int = 20_000_000
    runtime_home: Path | None = None
    runtime_env_file: Path | None = None
    runtime_write_actions: tuple[str, ...] = ()
    archive_write_prefixes: tuple[str, ...] = ()
    archive_write_max_bytes: int = 1_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        state_raw = os.getenv("VESTIGIA_MCP_STATE_DIR", "").strip()
        state_dir = (
            Path(state_raw).expanduser()
            if state_raw
            else Path.home() / ".vestigia-mcp"
        )
        deployment_id = os.getenv(
            "VESTIGIA_MCP_DEPLOYMENT_ID", "local-desktop"
        ).strip() or "local-desktop"
        return cls(
            live_archive_root=_optional_path("VESTIGIA_MCP_LIVE_ARCHIVE_ROOT"),
            snapshot_archive_root=_optional_path(
                "VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT"
            ),
            state_dir=state_dir,
            deployment_id=deployment_id,
            archive_text_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES", 1_000_000
            ),
            archive_media_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES", 20_000_000
            ),
            runtime_home=_optional_path("VESTIGIA_MCP_RUNTIME_HOME"),
            runtime_env_file=_optional_path("VESTIGIA_MCP_RUNTIME_ENV_FILE"),
            runtime_write_actions=_action_allowlist(
                "VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS"
            ),
            archive_write_prefixes=_path_prefix_allowlist(
                "VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES"
            ),
            archive_write_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES", 1_000_000
            ),
        )
