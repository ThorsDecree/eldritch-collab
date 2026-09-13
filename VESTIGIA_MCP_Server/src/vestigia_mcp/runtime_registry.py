from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.runtime import RuntimeBridge, RuntimeBridgeError


RUNTIMES_SCHEMA_VERSION = "vestigia.runtimes.v0.1"
_RUNTIME_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class RuntimeEntry:
    runtime_id: str
    home: Path | None
    env_file: Path | None
    write_actions: tuple[str, ...]


class RuntimeRegistry:
    """Route MCP Runtime projection calls to one operator-named Runtime house."""

    def __init__(
        self,
        registry_file: Path | None,
        *,
        legacy_home: Path | None,
        legacy_env_file: Path | None,
        legacy_write_actions: tuple[str, ...],
        deployment_id: str,
    ) -> None:
        self._registry_file = (
            registry_file.expanduser() if registry_file is not None else None
        )
        self._legacy = RuntimeEntry(
            "default",
            legacy_home.expanduser() if legacy_home is not None else None,
            legacy_env_file.expanduser() if legacy_env_file is not None else None,
            tuple(sorted(set(legacy_write_actions))),
        )
        self._deployment_id = deployment_id
        self._entries: dict[str, RuntimeEntry] | None = None
        self._bridges: dict[str, RuntimeBridge] = {}
        self._default_runtime_id: str | None = None

    @property
    def registry_configured(self) -> bool:
        return self._registry_file is not None

    def _load(self) -> dict[str, RuntimeEntry]:
        if self._entries is not None:
            return self._entries
        if self._registry_file is None:
            self._entries = {"default": self._legacy}
            self._default_runtime_id = "default"
            return self._entries
        try:
            raw: Any = json.loads(self._registry_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeBridgeError(
                "Runtime registry file is configured but unavailable"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeBridgeError(
                "Runtime registry file is unreadable or invalid JSON"
            ) from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != RUNTIMES_SCHEMA_VERSION:
            raise RuntimeBridgeError(
                f"Runtime registry must use {RUNTIMES_SCHEMA_VERSION}"
            )
        items = raw.get("runtimes")
        if not isinstance(items, list) or not items:
            raise RuntimeBridgeError("Runtime registry must contain a non-empty runtimes array")
        entries: dict[str, RuntimeEntry] = {}
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeBridgeError("Every Runtime registry entry must be an object")
            runtime_id = item.get("id")
            if not isinstance(runtime_id, str) or not _RUNTIME_ID.fullmatch(runtime_id):
                raise RuntimeBridgeError(
                    "Runtime IDs must be lowercase path-safe identifiers"
                )
            if runtime_id in entries:
                raise RuntimeBridgeError(f"Duplicate Runtime ID: {runtime_id}")
            home = self._absolute_path(item.get("home"), runtime_id, "home", required=True)
            env_file = self._absolute_path(
                item.get("env_file"), runtime_id, "env_file", required=False
            )
            actions = item.get("write_actions", [])
            if not isinstance(actions, list) or not all(
                isinstance(action, str) and action.strip() for action in actions
            ):
                raise RuntimeBridgeError(
                    f"Runtime {runtime_id} write_actions must be an array of strings"
                )
            entries[runtime_id] = RuntimeEntry(
                runtime_id,
                home,
                env_file,
                tuple(sorted({action.strip().lower() for action in actions})),
            )
        default = raw.get("default_runtime_id")
        if not isinstance(default, str) or default not in entries:
            raise RuntimeBridgeError(
                "Runtime registry default_runtime_id must name one configured Runtime"
            )
        self._entries = entries
        self._default_runtime_id = default
        return entries

    @staticmethod
    def _absolute_path(
        value: object,
        runtime_id: str,
        field: str,
        *,
        required: bool,
    ) -> Path | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise RuntimeBridgeError(f"Runtime {runtime_id} requires {field}")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise RuntimeBridgeError(f"Runtime {runtime_id} {field} must be absolute")
        return path

    def _entry(self, runtime_id: str | None) -> RuntimeEntry:
        entries = self._load()
        selected = runtime_id or self._default_runtime_id
        if selected is None or selected not in entries:
            raise RuntimeBridgeError(f"Runtime is not configured: {selected}")
        return entries[selected]

    def bridge(self, runtime_id: str | None = None) -> RuntimeBridge:
        entry = self._entry(runtime_id)
        bridge = self._bridges.get(entry.runtime_id)
        if bridge is None:
            bridge = RuntimeBridge(
                entry.home,
                entry.env_file,
                deployment_id=self._deployment_id,
                write_actions=entry.write_actions,
            )
            self._bridges[entry.runtime_id] = bridge
        return bridge

    def status(self, runtime_id: str | None = None) -> dict[str, object]:
        try:
            entry = self._entry(runtime_id)
        except RuntimeBridgeError as exc:
            return {
                "runtime_id": runtime_id,
                "registry_mode": "file" if self.registry_configured else "legacy_env",
                "configured": self.registry_configured,
                "available": False,
                "error": str(exc),
                "provider_initialized": False,
                "provider_calls_enabled_by_bridge": False,
            }
        return {
            "runtime_id": entry.runtime_id,
            "registry_mode": "file" if self.registry_configured else "legacy_env",
            "env_file": str(entry.env_file) if entry.env_file is not None else None,
            "write_actions": list(entry.write_actions),
            **self.bridge(entry.runtime_id).status(),
        }

    def list(self) -> dict[str, object]:
        try:
            entries = self._load()
        except RuntimeBridgeError as exc:
            return {
                "schema_version": RUNTIMES_SCHEMA_VERSION,
                "configured": True,
                "available": False,
                "registry_file": str(self._registry_file),
                "runtime_count": 0,
                "runtimes": [],
                "error": str(exc),
            }
        statuses = [self.status(runtime_id) for runtime_id in sorted(entries)]
        return {
            "schema_version": RUNTIMES_SCHEMA_VERSION,
            "configured": any(bool(item.get("configured")) for item in statuses),
            "available": all(bool(item.get("available")) for item in statuses),
            "registry_mode": "file" if self.registry_configured else "legacy_env",
            "registry_file": str(self._registry_file) if self._registry_file else None,
            "default_runtime_id": self._default_runtime_id,
            "runtime_count": len(statuses) if self.registry_configured else int(
                bool(statuses[0].get("configured"))
            ),
            "runtimes": statuses if self.registry_configured else (
                statuses if statuses[0].get("configured") else []
            ),
        }

    def capabilities(
        self, target: str | None = None, *, runtime_id: str | None = None
    ) -> dict[str, Any]:
        result = self.bridge(runtime_id).capabilities(target)
        return {"runtime_id": self._entry(runtime_id).runtime_id, **result}

    def call(
        self,
        *,
        action: str,
        arguments: dict[str, Any] | None,
        request_id: str,
        runtime_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.bridge(runtime_id).call(
            action=action, arguments=arguments, request_id=request_id
        )
        return {"runtime_id": self._entry(runtime_id).runtime_id, **result}

    def write_capabilities(
        self, target: str | None = None, *, runtime_id: str | None = None
    ) -> dict[str, Any]:
        result = self.bridge(runtime_id).write_capabilities(target)
        return {"runtime_id": self._entry(runtime_id).runtime_id, **result}

    def write(
        self,
        *,
        action: str,
        arguments: dict[str, Any] | None,
        request_id: str,
        runtime_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.bridge(runtime_id).write(
            action=action, arguments=arguments, request_id=request_id
        )
        return {"runtime_id": self._entry(runtime_id).runtime_id, **result}

    @property
    def configured(self) -> bool:
        return bool(self.list().get("configured"))

    @property
    def configured_home(self) -> str | None:
        try:
            return self.bridge().configured_home
        except RuntimeBridgeError:
            return None

    @property
    def configured_env_file(self) -> str | None:
        try:
            return self.bridge().configured_env_file
        except RuntimeBridgeError:
            return None

    @property
    def write_actions(self) -> tuple[str, ...]:
        try:
            return self._entry(None).write_actions
        except RuntimeBridgeError:
            return ()

    @property
    def any_write_actions(self) -> bool:
        try:
            return any(entry.write_actions for entry in self._load().values())
        except RuntimeBridgeError:
            return False
