from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


_ALLOWED_TOP = {"schema_version", "recipes"}
_ALLOWED_RECIPE = {
    "id", "description", "argv", "cwd", "timeout_seconds", "env_profile",
    "expected_exit_codes", "max_stdout_bytes", "max_stderr_bytes",
}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Recipe:
    id: str
    description: str
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: float
    env_profile: str
    expected_exit_codes: tuple[int, ...]
    max_stdout_bytes: int
    max_stderr_bytes: int

    def digest(self) -> str:
        payload = {
            "id": self.id,
            "description": self.description,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "env_profile": self.env_profile,
            "expected_exit_codes": list(self.expected_exit_codes),
            "max_stdout_bytes": self.max_stdout_bytes,
            "max_stderr_bytes": self.max_stderr_bytes,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Manifest:
    recipes: dict[str, Recipe]


def _relative_dir(repo_root: Path, value: str) -> str:
    p = Path(value)
    if p.is_absolute():
        raise ManifestError("recipe cwd must be repository-relative")
    resolved = (repo_root / p).resolve()
    root = repo_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestError("recipe cwd escapes repository root") from exc
    return resolved.relative_to(root).as_posix()


def load_manifest(path: Path, repo_root: Path) -> Manifest:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ManifestError("manifest must be an object")
    unknown = set(data) - _ALLOWED_TOP
    if unknown:
        raise ManifestError(f"unknown manifest fields: {sorted(unknown)}")
    if data.get("schema_version") != "vestigia.house-mechanic.v0.1":
        raise ManifestError("unsupported schema_version")
    rows = data.get("recipes")
    if not isinstance(rows, list):
        raise ManifestError("recipes must be a list")

    out: dict[str, Recipe] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ManifestError("recipe must be an object")
        unknown = set(row) - _ALLOWED_RECIPE
        if unknown:
            raise ManifestError(f"unknown recipe fields: {sorted(unknown)}")
        rid = row.get("id")
        argv = row.get("argv")
        if not isinstance(rid, str) or not rid or rid in out:
            raise ManifestError("recipe id must be unique and non-empty")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ManifestError(f"{rid}: argv must be a non-empty string list")
        timeout = row.get("timeout_seconds", 120)
        stdout_cap = row.get("max_stdout_bytes", 65536)
        stderr_cap = row.get("max_stderr_bytes", 32768)
        expected = row.get("expected_exit_codes", [0])
        if not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 3600:
            raise ManifestError(f"{rid}: invalid timeout_seconds")
        if not isinstance(stdout_cap, int) or not 0 < stdout_cap <= 1_048_576:
            raise ManifestError(f"{rid}: invalid max_stdout_bytes")
        if not isinstance(stderr_cap, int) or not 0 < stderr_cap <= 1_048_576:
            raise ManifestError(f"{rid}: invalid max_stderr_bytes")
        if not isinstance(expected, list) or not expected or not all(isinstance(x, int) for x in expected):
            raise ManifestError(f"{rid}: expected_exit_codes must be integers")
        profile = row.get("env_profile", "minimal")
        if profile not in {"minimal", "python"}:
            raise ManifestError(f"{rid}: unknown env_profile")
        out[rid] = Recipe(
            id=rid,
            description=str(row.get("description", "")),
            argv=tuple(argv),
            cwd=_relative_dir(repo_root, str(row.get("cwd", "."))),
            timeout_seconds=float(timeout),
            env_profile=profile,
            expected_exit_codes=tuple(expected),
            max_stdout_bytes=stdout_cap,
            max_stderr_bytes=stderr_cap,
        )
    return Manifest(out)
