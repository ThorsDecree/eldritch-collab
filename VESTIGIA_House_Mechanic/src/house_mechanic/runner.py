from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .model import Recipe


@dataclass(frozen=True)
class RunReceipt:
    request_id: str
    recipe_id: str
    recipe_sha256: str
    cwd: str
    started_at: str
    completed_at: str
    wall_seconds: float
    exit_code: int | None
    expected_exit: bool
    timed_out: bool
    output_limit_exceeded: bool
    stdout: str
    stderr: str
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

    def text(self) -> str:
        return bytes(self.buf).decode("utf-8", errors="replace")


def _env(profile: str) -> dict[str, str]:
    keep = {"SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP"}
    if profile == "python":
        keep |= {"PYTHONUTF8", "PYTHONIOENCODING"}
    return {k: v for k, v in os.environ.items() if k in keep}


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
    cwd = (repo_root.resolve() / recipe.cwd).resolve()
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        list(recipe.argv),
        cwd=cwd,
        env=_env(recipe.env_profile),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
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
    return RunReceipt(
        request_id=request_id,
        recipe_id=recipe.id,
        recipe_sha256=recipe.digest(),
        cwd=recipe.cwd,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        wall_seconds=round(time.monotonic() - t0, 6),
        exit_code=exit_code,
        expected_exit=exit_code in recipe.expected_exit_codes if exit_code is not None else False,
        timed_out=timed_out,
        output_limit_exceeded=overflow.is_set(),
        stdout=out.text(),
        stderr=err.text(),
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        process_tree_termination_attempted=termination_attempted,
        process_tree_containment_proven=False,
    )
