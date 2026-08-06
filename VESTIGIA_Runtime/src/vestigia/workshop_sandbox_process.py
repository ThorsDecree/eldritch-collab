from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from typing import Any

from .utils import atomic_write_text, stable_json
from .workshop_sandbox_backend import ProcessResult, SandboxLimits, _safe_environment


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(maximum + 1)


def _run_process(
    *,
    source: str,
    input_bytes: bytes,
    root: Path,
    limits: SandboxLimits,
) -> ProcessResult:
    script_path = root / "script.py"
    input_path = root / "input" / "request.json"
    stdout_path = root / "tmp" / "stdout.bin"
    stderr_path = root / "tmp" / "stderr.bin"
    atomic_write_text(script_path, source)
    input_path.write_bytes(input_bytes)
    try:
        script_path.chmod(stat.S_IRUSR)
        input_path.chmod(stat.S_IRUSR)
    except OSError:
        pass

    started = time.monotonic()
    timed_out = False
    output_limited = False
    with input_path.open("rb") as stdin, stdout_path.open("wb") as stdout, stderr_path.open(
        "wb"
    ) as stderr:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", str(script_path)],
            cwd=root,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=_safe_environment(root),
            start_new_session=(os.name != "nt"),
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                else 0
            ),
        )
        while process.poll() is None:
            elapsed = time.monotonic() - started
            stdout.flush()
            stderr.flush()
            try:
                stdout_size = stdout_path.stat().st_size
                stderr_size = stderr_path.stat().st_size
            except FileNotFoundError:
                stdout_size = stderr_size = 0
            if elapsed > limits.wall_seconds:
                timed_out = True
                _terminate_process(process)
                break
            if stdout_size > limits.stdout_bytes or stderr_size > limits.stderr_bytes:
                output_limited = True
                _terminate_process(process)
                break
            time.sleep(0.02)
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            exit_code = process.wait(timeout=2)

    wall_ms = max(0, int((time.monotonic() - started) * 1000))
    stdout_bytes = _read_bounded(stdout_path, limits.stdout_bytes)
    stderr_bytes = _read_bounded(stderr_path, limits.stderr_bytes)
    if timed_out:
        return ProcessResult(
            status="failed",
            exit_code=exit_code,
            timed_out=True,
            output_limited=False,
            stdout=stdout_bytes[: limits.stdout_bytes],
            stderr=stderr_bytes[: limits.stderr_bytes],
            wall_ms=wall_ms,
            error_category="timeout",
            safe_message="The local workshop process exceeded its wall-time ceiling.",
        )
    if output_limited or len(stdout_bytes) > limits.stdout_bytes or len(stderr_bytes) > limits.stderr_bytes:
        return ProcessResult(
            status="failed",
            exit_code=exit_code,
            timed_out=False,
            output_limited=True,
            stdout=stdout_bytes[: limits.stdout_bytes],
            stderr=stderr_bytes[: limits.stderr_bytes],
            wall_ms=wall_ms,
            error_category="resource_limit",
            safe_message="The local workshop process exceeded its stdout or stderr ceiling.",
        )
    if exit_code != 0:
        return ProcessResult(
            status="failed",
            exit_code=exit_code,
            timed_out=False,
            output_limited=False,
            stdout=stdout_bytes,
            stderr=stderr_bytes,
            wall_ms=wall_ms,
            error_category="sandbox_start",
            safe_message="The local workshop process exited without a successful result.",
        )
    return ProcessResult(
        status="succeeded",
        exit_code=exit_code,
        timed_out=False,
        output_limited=False,
        stdout=stdout_bytes,
        stderr=stderr_bytes,
        wall_ms=wall_ms,
    )


def _normalize_declared_artifact(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("script output artifact declarations must be objects")
    unknown = sorted(set(value) - {"path", "media_type"})
    if unknown:
        raise ValueError(
            "script output artifact declaration has unsupported fields: "
            + ", ".join(unknown)
        )
    relative = str(value.get("path") or "").strip().replace("\\", "/")
    media_type = str(value.get("media_type") or "").strip()
    if not relative or len(relative) > 240:
        raise ValueError("script output artifact path is missing or too long")
    path = Path(relative)
    if path.is_absolute() or relative.startswith("/"):
        raise ValueError("script output artifact path must be relative")
    parts = path.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("script output artifact path is unsafe")
    if ":" in relative or "\x00" in relative:
        raise ValueError("script output artifact path is unsafe")
    normalized = path.as_posix()
    if normalized != relative:
        raise ValueError("script output artifact path is not normalized")
    if not media_type or len(media_type) > 160 or any(ch.isspace() for ch in media_type):
        raise ValueError("script output artifact media_type is invalid")
    return {"path": normalized, "media_type": media_type}


def _validate_output_envelope(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("script output was not UTF-8") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("script output was not one JSON value") from exc
    if not isinstance(value, dict):
        raise ValueError("script output envelope must be an object")
    allowed = {
        "schema_version",
        "value",
        "artifacts",
        "warnings",
        "notes",
        "requested_follow_up",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"script output envelope has unsupported fields: {', '.join(unknown)}")
    if value.get("schema_version") != "vestigia.script-output.v0.1":
        raise ValueError("script output envelope has an unsupported schema version")
    artifacts = value.get("artifacts", [])
    warnings = value.get("warnings", [])
    if not isinstance(artifacts, list):
        raise ValueError("script output artifacts must be an array")
    normalized_artifacts = [_normalize_declared_artifact(item) for item in artifacts]
    artifact_paths = [item["path"] for item in normalized_artifacts]
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError("script output artifact paths must be unique")
    value["artifacts"] = normalized_artifacts
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ValueError("script output warnings must be an array of strings")
    follow_up = value.get("requested_follow_up", [])
    if not isinstance(follow_up, list):
        raise ValueError("requested_follow_up must be an array")
    stable_json(value.get("value"))
    return value


def _harvest_output_files(
    root: Path,
    limits: SandboxLimits,
    declarations: list[dict[str, str]],
) -> list[dict[str, Any]]:
    output_root = (root / "output").resolve()
    declared = {item["path"]: item for item in declarations}
    harvested: list[dict[str, Any]] = []
    total = 0
    for path in sorted(output_root.rglob("*")):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("sandbox output contained a symlink")
        if path.is_dir():
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("sandbox output contained a special file")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(output_root).as_posix()
        except ValueError as exc:
            raise ValueError("sandbox output escaped the output root") from exc
        declaration = declared.get(relative)
        if declaration is None:
            raise ValueError(f"sandbox output file was not declared: {relative}")
        if len(harvested) >= limits.artifact_files:
            raise ValueError("sandbox output exceeded the artifact-file ceiling")
        size = info.st_size
        total += size
        if total > limits.artifact_bytes:
            raise ValueError("sandbox output exceeded the artifact-byte ceiling")
        data = path.read_bytes()
        harvested.append(
            {
                "relative_path": relative,
                "size_bytes": size,
                "content_hash": hashlib.sha256(data).hexdigest(),
                "media_type": declaration["media_type"],
                "data": data,
            }
        )
    missing = sorted(set(declared) - {item["relative_path"] for item in harvested})
    if missing:
        raise ValueError("declared sandbox output file was not created: " + ", ".join(missing))
    return harvested
