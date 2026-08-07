from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import sys
from typing import Any

from .utils import sha256_text, stable_json


BACKEND_ID = "local.process"
BACKEND_VERSION = "0.1.0"
PROFILE = "local_process"


@dataclass(frozen=True)
class SandboxLimits:
    wall_seconds: int
    input_bytes: int
    stdout_bytes: int
    stderr_bytes: int
    artifact_files: int
    artifact_bytes: int


@dataclass
class ProcessResult:
    status: str
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    stdout: bytes
    stderr: bytes
    wall_ms: int
    error_category: str | None = None
    safe_message: str | None = None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _limits(house: Any, payload: dict[str, Any] | None = None) -> SandboxLimits:
    payload = payload or {}
    configured_wall = _bounded_int(
        house.config.get("workshop.max_wall_seconds", 5),
        default=5,
        minimum=1,
        maximum=30,
    )
    return SandboxLimits(
        wall_seconds=_bounded_int(
            payload.get("wall_seconds"),
            default=configured_wall,
            minimum=1,
            maximum=configured_wall,
        ),
        input_bytes=_bounded_int(
            house.config.get("workshop.max_input_bytes", 65536),
            default=65536,
            minimum=1024,
            maximum=1048576,
        ),
        stdout_bytes=_bounded_int(
            house.config.get("workshop.max_stdout_bytes", 65536),
            default=65536,
            minimum=1024,
            maximum=1048576,
        ),
        stderr_bytes=_bounded_int(
            house.config.get("workshop.max_stderr_bytes", 32768),
            default=32768,
            minimum=1024,
            maximum=1048576,
        ),
        artifact_files=_bounded_int(
            house.config.get("workshop.max_artifact_files", 16),
            default=16,
            minimum=0,
            maximum=128,
        ),
        artifact_bytes=_bounded_int(
            house.config.get("workshop.max_artifact_bytes", 1048576),
            default=1048576,
            minimum=0,
            maximum=10000000,
        ),
    )


def _hash_id(value: str) -> str:
    return sha256_text(value)[:32]


def _guarantees() -> dict[str, bool]:
    return {
        "network_deny_enforced": False,
        "filesystem_mounts_enforced": False,
        "environment_stripped": True,
        "process_tree_contained": False,
        "memory_limit_enforced": False,
        "cpu_limit_enforced": False,
        "wall_limit_enforced": True,
        "output_limit_enforced": True,
        "hostile_code_approved": False,
    }


def backend_descriptor(house: Any) -> dict[str, Any]:
    configured = bool(house.config.get("workshop.local_process_enabled", True))
    executable = Path(sys.executable)
    guarantees = _guarantees()
    return {
        "schema_version": "vestigia.sandbox-backend.v0.1",
        "backend_id": BACKEND_ID,
        "version": BACKEND_VERSION,
        "profiles": [PROFILE],
        "languages": ["python"],
        "guarantees": guarantees,
        "guarantees_hash": sha256_text(stable_json(guarantees)),
        "platform": platform.system().casefold() or sys.platform,
        "interpreter": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "isolated_flag": True,
            "site_disabled": True,
            "bytecode_disabled": True,
        },
        "health": {
            "configured": configured,
            "callable_now": bool(configured and executable.is_file()),
            "reason": (
                None
                if configured and executable.is_file()
                else "local process backend disabled"
                if not configured
                else "python interpreter unavailable"
            ),
        },
        "truthful_boundary": (
            "This backend is for resident-authored or locally reviewed code and ordinary-bug "
            "containment. It is not hostile-code isolation. Network and host-filesystem denial "
            "are not claimed."
        ),
    }


def _safe_environment(root: Path) -> dict[str, str]:
    scratch = str((root / "tmp").resolve())
    return {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TMP": scratch,
        "TEMP": scratch,
    }
