from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.archive import normalize_relative_path
from .house_mechanic import DevActionFilter, parse_dev_actions


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


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, yes/no, or on/off")


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


def _lanternslide_source_prefix_env() -> str:
    raw = os.getenv("VESTIGIA_MCP_LANTERNSLIDE_SOURCE_PREFIX", "pics").strip()
    if not raw:
        return ""
    return normalize_relative_path(raw).rstrip("/")


def _lanternslide_catalog_path_env(source_prefix: str) -> str:
    default = f"{source_prefix}/Lanternslide/catalog.json" if source_prefix else "Lanternslide/catalog.json"
    raw = os.getenv("VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH", default).strip()
    normalized = normalize_relative_path(raw)
    if Path(normalized).suffix.lower() != ".json":
        raise ValueError("VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH must end in .json")
    if source_prefix and not (
        normalized == source_prefix
        or normalized.startswith(source_prefix.rstrip("/") + "/")
    ):
        raise ValueError(
            "VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH must be inside the Lanternslide source prefix"
        )
    return normalized


def _bridge_host_env() -> str:
    host = os.getenv("VESTIGIA_MCP_PORCHLIGHT_BRIDGE_HOST", "127.0.0.1").strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    if host != "127.0.0.1":
        raise ValueError("VESTIGIA MCP Porchlight bridge host must be loopback")
    return host


def _daemon_bridge_host_env() -> str:
    host = os.getenv("VESTIGIA_MCP_DAEMON_BRIDGE_HOST", "127.0.0.1").strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    if host != "127.0.0.1":
        raise ValueError("VESTIGIA MCP Daemon-Bridge host must be loopback")
    return host


def _house_mechanic_host_env() -> str:
    host = os.getenv(
        "VESTIGIA_MCP_HOUSE_MECHANIC_HOST",
        "127.0.0.1",
    ).strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    if host != "127.0.0.1":
        raise ValueError("VESTIGIA MCP House Mechanic host must be loopback")
    return host


@dataclass(frozen=True)
class Settings:
    live_archive_root: Path | None
    snapshot_archive_root: Path | None
    state_dir: Path
    deployment_id: str
    archive_text_max_bytes: int = 1_000_000
    archive_browse_ttl_seconds: int = 900
    archive_page_max_bytes: int = 64_000
    archive_media_max_bytes: int = 20_000_000
    runtime_home: Path | None = None
    runtime_env_file: Path | None = None
    runtime_write_actions: tuple[str, ...] = ()
    archive_write_prefixes: tuple[str, ...] = ()
    archive_write_max_bytes: int = 1_000_000
    porchlight_screenshot_max_bytes: int = 2_000_000
    porchlight_bridge_host: str = "127.0.0.1"
    porchlight_bridge_port: int = 8765
    porchlight_bridge_token_path: Path | None = None
    porchlight_bridge_extension_origin: str = "chrome-extension://porchlight"
    porchlight_bridge_max_body_bytes: int = 1_200_000
    daemon_bridge_enabled: bool = False
    daemon_bridge_host: str = "127.0.0.1"
    daemon_bridge_port: int = 8766
    daemon_bridge_token_path: Path | None = None
    daemon_bridge_timeout_seconds: int = 120
    daemon_bridge_max_response_bytes: int = 262_144
    house_mechanic_enabled: bool = False
    house_mechanic_host: str = "127.0.0.1"
    house_mechanic_port: int = 8770
    house_mechanic_token_path: Path | None = None
    house_mechanic_timeout_seconds: int = 120
    house_mechanic_max_response_bytes: int = 262_144
    dev_actions: DevActionFilter = field(
        default_factory=lambda: DevActionFilter(mode="wildcard", actions=())
    )
    mounts_file: Path | None = None
    runtimes_file: Path | None = None
    gametable_enabled: bool = False
    gametable_state_dir: Path | None = None
    lanternslide_source_prefix: str = "pics"
    lanternslide_catalog_path: str = "pics/Lanternslide/catalog.json"
    lanternslide_scan_batch_max: int = 50
    lanternslide_image_max_bytes: int = 25_000_000
    lanternslide_contact_sheet_max_bytes: int = 4_000_000

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
        lanternslide_source_prefix = _lanternslide_source_prefix_env()
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
            archive_browse_ttl_seconds=_positive_int_env(
                "VESTIGIA_MCP_ARCHIVE_BROWSE_TTL_SECONDS", 900
            ),
            archive_page_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_ARCHIVE_PAGE_MAX_BYTES", 64_000
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
            porchlight_screenshot_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_PORCHLIGHT_SCREENSHOT_MAX_BYTES", 2_000_000
            ),
            porchlight_bridge_host=_bridge_host_env(),
            porchlight_bridge_port=_positive_int_env(
                "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_PORT", 8765
            ),
            porchlight_bridge_token_path=_optional_path(
                "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_TOKEN_PATH"
            )
            or state_dir / "porchlight-token",
            porchlight_bridge_extension_origin=os.getenv(
                "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_EXTENSION_ORIGIN",
                "chrome-extension://porchlight",
            ).strip()
            or "chrome-extension://porchlight",
            porchlight_bridge_max_body_bytes=_positive_int_env(
                "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_MAX_BODY_BYTES", 1_200_000
            ),
            daemon_bridge_enabled=_bool_env("VESTIGIA_MCP_DAEMON_BRIDGE_ENABLED"),
            daemon_bridge_host=_daemon_bridge_host_env(),
            daemon_bridge_port=_positive_int_env(
                "VESTIGIA_MCP_DAEMON_BRIDGE_PORT", 8766
            ),
            daemon_bridge_token_path=_optional_path(
                "VESTIGIA_MCP_DAEMON_BRIDGE_TOKEN_PATH"
            ),
            daemon_bridge_timeout_seconds=_positive_int_env(
                "VESTIGIA_MCP_DAEMON_BRIDGE_TIMEOUT_SECONDS", 120
            ),
            daemon_bridge_max_response_bytes=_positive_int_env(
                "VESTIGIA_MCP_DAEMON_BRIDGE_MAX_RESPONSE_BYTES", 262_144
            ),
            house_mechanic_enabled=_bool_env(
                "VESTIGIA_MCP_HOUSE_MECHANIC_ENABLED"
            ),
            house_mechanic_host=_house_mechanic_host_env(),
            house_mechanic_port=_positive_int_env(
                "VESTIGIA_MCP_HOUSE_MECHANIC_PORT", 8770
            ),
            house_mechanic_token_path=_optional_path(
                "VESTIGIA_MCP_HOUSE_MECHANIC_TOKEN_PATH"
            ),
            house_mechanic_timeout_seconds=_positive_int_env(
                "VESTIGIA_MCP_HOUSE_MECHANIC_TIMEOUT_SECONDS", 120
            ),
            house_mechanic_max_response_bytes=_positive_int_env(
                "VESTIGIA_MCP_HOUSE_MECHANIC_MAX_RESPONSE_BYTES", 262_144
            ),
            dev_actions=parse_dev_actions(
                os.environ.get("VESTIGIA_MCP_DEV_ACTIONS")
            ),
            mounts_file=_optional_path("VESTIGIA_MCP_MOUNTS_FILE"),
            runtimes_file=_optional_path("VESTIGIA_MCP_RUNTIMES_FILE"),
            gametable_enabled=_bool_env("VESTIGIA_MCP_GAMETABLE_ENABLED"),
            gametable_state_dir=_optional_path("VESTIGIA_MCP_GAMETABLE_STATE_DIR"),
            lanternslide_source_prefix=lanternslide_source_prefix,
            lanternslide_catalog_path=_lanternslide_catalog_path_env(
                lanternslide_source_prefix
            ),
            lanternslide_scan_batch_max=_positive_int_env(
                "VESTIGIA_MCP_LANTERNSLIDE_SCAN_BATCH_MAX", 50
            ),
            lanternslide_image_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_LANTERNSLIDE_IMAGE_MAX_BYTES", 25_000_000
            ),
            lanternslide_contact_sheet_max_bytes=_positive_int_env(
                "VESTIGIA_MCP_LANTERNSLIDE_CONTACT_SHEET_MAX_BYTES", 4_000_000
            ),
        )
