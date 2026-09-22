from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .environment import filtered_environment
from .model import Recipe


@dataclass(frozen=True)
class RunReceipt:
    request_id: str
    recipe_id: str
    recipe_sha256: str
    cwd: str
    env_profile: str
    source_commit: str | None
    source_branch: str | None
    source_dirty: bool | None
    source_status_sha256: str | None
    process_id: int
    started_at: str
    completed_at: str
    wall_seconds: float
    exit_code: int | None
    expected_exit: bool
    timed_out: bool
    output_limit_exceeded: bool
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    process_tree_termination_attempted: bool
    process_tree_containment_proven: bool

    def to_dict(self) -> dict:
        return asdict(self)


class _BoundedCollector:
    def __init__(self, cap: int):
        self.cap = cap
        self.buf = bytearray()
        self.truncated = False

    def pump(self, stream, overflow: threading.Event) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            room = self.cap - len(self.buf)
            if room > 0:
                self.buf.extend(chunk[:room])
            if len(chunk) > room:
                self.truncated = True
                overflow.set()

    def raw(self) -> bytes:
        return bytes(self.buf)

    def text(self) -> str:
        return self.raw().decode("utf-8", errors="replace")


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root.resolve()), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=filtered_environment("minimal"),
            shell=False,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _git_identity(repo_root: Path) -> tuple[str | None, str | None, bool | None, str | None]:
    commit = _git_output(repo_root, "rev-parse", "HEAD")
    if commit is None:
        return None, None, None, None
    branch = _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git_output(repo_root, "status", "--porcelain=v1", "-uno")
    if status is None:
        return commit, branch, None, None
    return (
        commit,
        branch,
        bool(status),
        hashlib.sha256(status.encode("utf-8")).hexdigest(),
    )


def _terminate_tree(proc: subprocess.Popen[bytes]) -> bool:
    if proc.poll() is not None:
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    else:
        proc.kill()
    return True


def run_recipe(recipe: Recipe, repo_root: Path, request_id: str | None = None) -> RunReceipt:
    request_id = request_id or str(uuid.uuid4())
    root = repo_root.resolve()
    cwd = (root / recipe.cwd).resolve()
    source_commit, source_branch, source_dirty, source_status_sha256 = _git_identity(root)
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        list(recipe.argv),
        cwd=cwd,
        env=filtered_environment(recipe.env_profile),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    process_id = int(proc.pid)
    assert proc.stdout is not None and proc.stderr is not None
    overflow = threading.Event()
    out = _BoundedCollector(recipe.max_stdout_bytes)
    err = _BoundedCollector(recipe.max_stderr_bytes)
    threads = [
        threading.Thread(target=out.pump, args=(proc.stdout, overflow), daemon=True),
        threading.Thread(target=err.pump, args=(proc.stderr, overflow), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    termination_attempted = False
    deadline = t0 + recipe.timeout_seconds
    while proc.poll() is None:
        if overflow.is_set():
            termination_attempted = _terminate_tree(proc) or termination_attempted
            break
        if time.monotonic() >= deadline:
            timed_out = True
            termination_attempted = _terminate_tree(proc) or termination_attempted
            break
        time.sleep(0.02)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        termination_attempted = _terminate_tree(proc) or termination_attempted
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    for thread in threads:
        thread.join(timeout=1)

    completed = datetime.now(timezone.utc)
    exit_code = proc.poll()
    stdout_raw = out.raw()
    stderr_raw = err.raw()
    return RunReceipt(
        request_id=request_id,
        recipe_id=recipe.id,
        recipe_sha256=recipe.digest(),
        cwd=recipe.cwd,
        env_profile=recipe.env_profile,
        source_commit=source_commit,
        source_branch=source_branch,
        source_dirty=source_dirty,
        source_status_sha256=source_status_sha256,
        process_id=process_id,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        wall_seconds=round(time.monotonic() - t0, 6),
        exit_code=exit_code,
        expected_exit=exit_code in recipe.expected_exit_codes if exit_code is not None else False,
        timed_out=timed_out,
        output_limit_exceeded=overflow.is_set(),
        stdout=stdout_raw.decode("utf-8", errors="replace"),
        stderr=stderr_raw.decode("utf-8", errors="replace"),
        stdout_bytes=len(stdout_raw),
        stderr_bytes=len(stderr_raw),
        stdout_sha256=hashlib.sha256(stdout_raw).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr_raw).hexdigest(),
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        process_tree_termination_attempted=termination_attempted,
        process_tree_containment_proven=False,
    )
