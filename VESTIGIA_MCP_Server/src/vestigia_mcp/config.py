from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _optional_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class Settings:
    live_archive_root: Path | None
    snapshot_archive_root: Path | None
    state_dir: Path
    deployment_id: str
    archive_text_max_bytes: int = 1_000_000

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
        max_bytes_raw = os.getenv(
            "VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES", "1000000"
        ).strip()
        try:
            max_bytes = int(max_bytes_raw)
        except ValueError as exc:
            raise ValueError(
                "VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES must be an integer"
            ) from exc
        if max_bytes <= 0:
            raise ValueError(
                "VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES must be positive"
            )

        return cls(
            live_archive_root=_optional_path("VESTIGIA_MCP_LIVE_ARCHIVE_ROOT"),
            snapshot_archive_root=_optional_path(
                "VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT"
            ),
            state_dir=state_dir,
            deployment_id=deployment_id,
            archive_text_max_bytes=max_bytes,
        )
