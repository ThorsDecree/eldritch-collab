from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class RuntimeBridgeError(RuntimeError):
    pass


class RuntimeBridge:
    """Lazy adapter over VESTIGIA Runtime's own HousePort/CapabilityRegistry.

    Runtime remains the semantic and policy authority. The MCP server does not copy
    Runtime capability schemas or implement a competing read/write classification.
    """

    def __init__(
        self,
        home: Path | None,
        env_file: Path | None,
        *,
        deployment_id: str,
    ) -> None:
        self._home = home.expanduser() if home is not None else None
        self._env_file = env_file.expanduser() if env_file is not None else None
        self._deployment_id = deployment_id
        self._lock = threading.Lock()
        self._loaded: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return self._home is not None

    @property
    def configured_home(self) -> str | None:
        return str(self._home) if self._home is not None else None

    @property
    def configured_env_file(self) -> str | None:
        return str(self._env_file) if self._env_file is not None else None

    def _load(self) -> dict[str, Any]:
        if self._home is None:
            raise RuntimeBridgeError("VESTIGIA Runtime home is not configured")
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            try:
                from vestigia import __version__ as runtime_version
                from vestigia.config import load_config
                from vestigia.db import ContinuityDB
                from vestigia.house_tools import HousePort
                from vestigia.mcp_projection import (
                    dispatch_read_projection,
                    read_projection,
                )
            except ImportError as exc:
                raise RuntimeBridgeError(
                    "VESTIGIA Runtime is not importable in the MCP environment. "
                    "Install the sibling VESTIGIA_Runtime package into this virtual environment."
                ) from exc

            try:
                config = load_config(self._home, env_file=self._env_file)
            except Exception as exc:
                raise RuntimeBridgeError(
                    f"Unable to load VESTIGIA Runtime home: {type(exc).__name__}: {exc}"
                ) from exc

            try:
                db = ContinuityDB(config.home_path / "memory" / "continuity.db")
                db.initialize()
                house = HousePort(config, db)
            except Exception as exc:
                raise RuntimeBridgeError(
                    f"Unable to open VESTIGIA Runtime HousePort: {type(exc).__name__}: {exc}"
                ) from exc

            self._loaded = {
                "runtime_version": runtime_version,
                "config": config,
                "db": db,
                "house": house,
                "read_projection": read_projection,
                "dispatch_read_projection": dispatch_read_projection,
            }
            return self._loaded

    def status(self) -> dict[str, object]:
        if self._home is None:
            return {
                "configured": False,
                "available": False,
                "mode": "embedded_house_port_read_projection",
                "provider_initialized": False,
                "provider_calls_enabled_by_bridge": False,
            }
        try:
            loaded = self._load()
            house = loaded["house"]
            projection = loaded["read_projection"](house)
            return {
                "configured": True,
                "available": True,
                "mode": "embedded_house_port_read_projection",
                "runtime_version": loaded["runtime_version"],
                "home": str(loaded["config"].home_path),
                "resident_id": house.resident_id,
                "room_id": house.room_id,
                "projected_capability_count": projection["capability_count"],
                "capability_digest_sha256": projection["capability_digest_sha256"],
                "projection_authority": projection["authority"],
                "provider_initialized": False,
                "provider_calls_enabled_by_bridge": False,
                "note": (
                    "The embedded HousePort may maintain Runtime-derived indexes and receipts. "
                    "It does not initialize CoreRuntime or a model provider."
                ),
            }
        except RuntimeBridgeError as exc:
            return {
                "configured": True,
                "available": False,
                "mode": "embedded_house_port_read_projection",
                "home": str(self._home),
                "error": str(exc),
                "provider_initialized": False,
                "provider_calls_enabled_by_bridge": False,
            }

    def capabilities(self, target: str | None = None) -> dict[str, Any]:
        loaded = self._load()
        try:
            return loaded["read_projection"](loaded["house"], target)
        except (KeyError, ValueError, PermissionError) as exc:
            raise RuntimeBridgeError(str(exc)) from exc

    def call(
        self,
        *,
        action: str,
        arguments: dict[str, Any] | None,
        request_id: str,
    ) -> dict[str, Any]:
        loaded = self._load()
        try:
            return loaded["dispatch_read_projection"](
                loaded["house"],
                action=action,
                arguments=arguments,
                request_id=request_id,
                deployment_id=self._deployment_id,
            )
        except Exception as exc:
            if isinstance(exc, RuntimeBridgeError):
                raise
            raise RuntimeBridgeError(
                f"Runtime projected call failed: {type(exc).__name__}: {exc}"
            ) from exc
