from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import subprocess
import threading
from typing import BinaryIO
import uuid

from .model import Recipe
from .service_model import Service


LOG_TAIL_BYTES = 16_384


class ProcessOwnershipError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _environment(profile: str) -> dict[str, str]:
    keep = {"SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP"}
    if profile == "python":
        keep |= {"PYTHONUTF8", "PYTHONIOENCODING"}
    return {key: value for key, value in os.environ.items() if key in keep}


def _file_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> tuple[bytes, int]:
    if not path.is_file():
        return b"", 0
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit:
            handle.seek(-limit, os.SEEK_END)
        return handle.read(limit), size


@dataclass(frozen=True)
class ProcessStatus:
    service_id: str
    declared_ownership: str
    process_owned: bool
    ownership_scope: str
    ownership_survives_supervisor_restart: bool
    state: str
    generation_id: str | None
    pid: int | None
    started_at: str | None
    exit_code: int | None
    stdout_bytes: int
    stderr_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _OwnedProcess:
    service_id: str
    generation_id: str
    process: subprocess.Popen[bytes]
    started_at: str
    stdout_path: Path
    stderr_path: Path
    stdout_handle: BinaryIO
    stderr_handle: BinaryIO


class ProcessRegistry:
    """Supervisor-instance ownership registry.

    Ownership is proven only by retaining the exact Popen handle created by this
    registry. It is intentionally not reconstructed from a PID after restart.
    """

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.expanduser().resolve()
        self.log_dir = self.state_dir / "process_logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._owned: dict[str, _OwnedProcess] = {}

    def _refresh(self, owned: _OwnedProcess) -> int | None:
        code = owned.process.poll()
        if code is not None:
            for handle in (owned.stdout_handle, owned.stderr_handle):
                if not handle.closed:
                    handle.flush()
                    handle.close()
        return code

    def status(self, service: Service) -> ProcessStatus:
        if not service.mechanic_owned:
            return ProcessStatus(
                service_id=service.id,
                declared_ownership=service.ownership,
                process_owned=False,
                ownership_scope="none",
                ownership_survives_supervisor_restart=False,
                state="external_unowned",
                generation_id=None,
                pid=None,
                started_at=None,
                exit_code=None,
                stdout_bytes=0,
                stderr_bytes=0,
            )

        with self._lock:
            owned = self._owned.get(service.id)
            if owned is None:
                return ProcessStatus(
                    service_id=service.id,
                    declared_ownership=service.ownership,
                    process_owned=False,
                    ownership_scope="supervisor_instance",
                    ownership_survives_supervisor_restart=False,
                    state="not_started",
                    generation_id=None,
                    pid=None,
                    started_at=None,
                    exit_code=None,
                    stdout_bytes=0,
                    stderr_bytes=0,
                )
            code = self._refresh(owned)
            _, stdout_size = _file_tail(owned.stdout_path)
            _, stderr_size = _file_tail(owned.stderr_path)
            return ProcessStatus(
                service_id=service.id,
                declared_ownership=service.ownership,
                process_owned=True,
                ownership_scope="supervisor_instance",
                ownership_survives_supervisor_restart=False,
                state="running" if code is None else "exited",
                generation_id=owned.generation_id,
                pid=int(owned.process.pid),
                started_at=owned.started_at,
                exit_code=code,
                stdout_bytes=stdout_size,
                stderr_bytes=stderr_size,
            )

    def logs(self, service: Service) -> dict[str, object]:
        status = self.status(service)
        if not status.process_owned:
            return {
                "service_id": service.id,
                "process_owned": False,
                "state": status.state,
                "stdout_tail": "",
                "stderr_tail": "",
                "stdout_tail_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_tail_sha256": hashlib.sha256(b"").hexdigest(),
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "tail_limit_bytes": LOG_TAIL_BYTES,
            }

        with self._lock:
            owned = self._owned[service.id]
            stdout_raw, stdout_size = _file_tail(owned.stdout_path)
            stderr_raw, stderr_size = _file_tail(owned.stderr_path)
        return {
            "service_id": service.id,
            "process_owned": True,
            "state": status.state,
            "generation_id": status.generation_id,
            "pid": status.pid,
            "stdout_tail": stdout_raw.decode("utf-8", errors="replace"),
            "stderr_tail": stderr_raw.decode("utf-8", errors="replace"),
            "stdout_tail_sha256": hashlib.sha256(stdout_raw).hexdigest(),
            "stderr_tail_sha256": hashlib.sha256(stderr_raw).hexdigest(),
            "stdout_bytes": stdout_size,
            "stderr_bytes": stderr_size,
            "tail_limit_bytes": LOG_TAIL_BYTES,
        }

    def launch_owned(
        self,
        service: Service,
        recipe: Recipe,
        repo_root: Path,
    ) -> ProcessStatus:
        """Launch one declared mechanic-owned child for the typed lifecycle API."""
        if not service.mechanic_owned:
            raise ValueError("external service cannot be launched as an owned process")
        with self._lock:
            existing = self._owned.get(service.id)
            if existing is not None and self._refresh(existing) is None:
                raise RuntimeError("service already has a running owned process")

            generation_id = f"hm_proc_{uuid.uuid4().hex}"
            stdout_path = self.log_dir / f"{service.id}-{generation_id}.stdout.log"
            stderr_path = self.log_dir / f"{service.id}-{generation_id}.stderr.log"
            stdout_handle = stdout_path.open("wb")
            stderr_handle = stderr_path.open("wb")
            cwd = (repo_root.resolve() / recipe.cwd).resolve()
            try:
                process = subprocess.Popen(
                    list(recipe.argv),
                    cwd=cwd,
                    env=_environment(recipe.env_profile),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    shell=False,
                )
            except Exception:
                stdout_handle.close()
                stderr_handle.close()
                raise

            self._owned[service.id] = _OwnedProcess(
                service_id=service.id,
                generation_id=generation_id,
                process=process,
                started_at=datetime.now(UTC).isoformat(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                stdout_handle=stdout_handle,
                stderr_handle=stderr_handle,
            )
            return self.status(service)

    def stop_owned(
        self,
        service: Service,
        *,
        expected_generation_id: str,
        timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        if not service.mechanic_owned:
            raise ProcessOwnershipError(
                "external_service",
                "external service cannot be stopped as an owned process",
            )

        with self._lock:
            owned = self._owned.get(service.id)
            if owned is None:
                raise ProcessOwnershipError(
                    "no_owned_process",
                    "service has no process owned by this supervisor instance",
                )
            if owned.generation_id != expected_generation_id:
                raise ProcessOwnershipError(
                    "generation_mismatch",
                    "requested generation is not the generation owned by this supervisor",
                )

            before = self.status(service)
            if before.state == "exited":
                return {
                    "action_occurred": False,
                    "after": before.to_dict(),
                    "termination": {
                        "requested_generation_id": expected_generation_id,
                        "terminate_called": False,
                        "forced": False,
                        "process_tree_containment_proven": False,
                    },
                }

            terminate_called = False
            forced = False
            try:
                owned.process.terminate()
                terminate_called = True
                owned.process.wait(timeout=max(0.1, float(timeout_seconds)))
            except subprocess.TimeoutExpired:
                forced = True
                if os.name == "nt":
                    subprocess.run(
                        [
                            "taskkill",
                            "/PID",
                            str(owned.process.pid),
                            "/T",
                            "/F",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        shell=False,
                    )
                else:
                    owned.process.kill()
                try:
                    owned.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

            after = self.status(service)
            return {
                "action_occurred": True,
                "after": after.to_dict(),
                "termination": {
                    "requested_generation_id": expected_generation_id,
                    "terminate_called": terminate_called,
                    "forced": forced,
                    "process_tree_containment_proven": False,
                },
            }

    def terminate_all_for_shutdown(self) -> None:
        """Best-effort test/operator shutdown only; not a remote lifecycle API."""
        with self._lock:
            for owned in self._owned.values():
                if self._refresh(owned) is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(owned.process.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            shell=False,
                        )
                    else:
                        owned.process.kill()
                    try:
                        owned.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                self._refresh(owned)
