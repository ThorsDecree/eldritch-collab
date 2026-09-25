from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Callable
import uuid


TASK_SCHEMA = "vestigia.house-mechanic-task.v0.1"
REPOSITORY_SCHEMA = "vestigia.house-mechanic-repositories.v0.1"
_ACTIVE = {"active", "handoff_pending"}
_TERMINAL = {"succeeded", "blocked", "failed_diagnostic", "needs_human_boundary", "abandoned", "released"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class TaskError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Repository:
    id: str
    path: str
    default_base_ref: str
    allowed_base_refs: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryManifest:
    repositories: dict[str, Repository]


def _validate_ref(value: str) -> str:
    value = value.strip()
    if not _REF.fullmatch(value) or value.startswith("-") or ".." in value or "//" in value:
        raise TaskError("invalid_ref", "Git ref is outside the bounded grammar")
    return value


def load_repository_manifest(path: Path, repo_root: Path) -> RepositoryManifest:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema_version", "repositories"}:
        raise ValueError("invalid repository manifest")
    if data["schema_version"] != REPOSITORY_SCHEMA or not isinstance(data["repositories"], list):
        raise ValueError("unsupported repository manifest")
    root = repo_root.resolve()
    out: dict[str, Repository] = {}
    for row in data["repositories"]:
        if not isinstance(row, dict) or set(row) - {"id", "path", "default_base_ref", "allowed_base_refs"}:
            raise ValueError("invalid repository entry")
        rid = row.get("id")
        if not isinstance(rid, str) or not _REPO_ID.fullmatch(rid) or rid in out:
            raise ValueError("repository id must be unique and path-safe")
        rel = Path(str(row.get("path", ".")))
        if rel.is_absolute():
            raise ValueError("repository path must be relative")
        resolved = (root / rel).resolve()
        try:
            stored = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("repository path escapes repo_root") from exc
        default = _validate_ref(str(row.get("default_base_ref", "main")))
        allowed_raw = row.get("allowed_base_refs", [default])
        if not isinstance(allowed_raw, list) or not allowed_raw or not all(isinstance(x, str) for x in allowed_raw):
            raise ValueError("allowed_base_refs must be a non-empty string list")
        allowed = tuple(_validate_ref(x) for x in allowed_raw)
        if default not in allowed:
            raise ValueError("default_base_ref must be allowed")
        out[rid] = Repository(rid, stored, default, allowed)
    return RepositoryManifest(out)


@dataclass
class TaskRecord:
    task_id: str
    repository_id: str
    purpose: str
    holder_id: str
    base_commit: str
    branch_name: str
    worktree_path: str
    authority_generation: int
    acquired_at: str
    lease_expires_at: str
    iteration_limit: int
    iterations_used: int
    state: str
    current_base_commit: str | None = None
    pending_base_commit: str | None = None
    current_iteration_id: str | None = None
    current_iteration_started_at: str | None = None
    pending_handoff_to: str | None = None
    terminal_reason: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": TASK_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        if data.get("schema_version") != TASK_SCHEMA:
            raise ValueError("unsupported task schema")
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        payload = {name: data[name] for name in fields if name in data}
        if "current_base_commit" not in payload:
            payload["current_base_commit"] = data.get("base_commit")
        return cls(**payload)


class TaskLedger:
    """Durable records survive restart; mutation authority deliberately does not."""

    def __init__(self, directory: Path, *, now: Callable[[], datetime] | None = None):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._suspend_restart_authority()

    def _path(self, task_id: str) -> Path:
        if not re.fullmatch(r"hm_task_[0-9a-f]{32}", task_id):
            raise TaskError("invalid_task_id", "task_id is not an issued task id")
        return self.directory / f"{task_id}.json"

    def _write(self, record: TaskRecord) -> None:
        path = self._path(record.task_id)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        raw = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _suspend_restart_authority(self) -> None:
        for path in self.directory.glob("hm_task_*.json"):
            try:
                record = TaskRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if record.state in _ACTIVE:
                record.state = "suspended_unverified"
                record.updated_at = self._now().isoformat()
                self._write(record)
            elif record.state in {"paused_budget_exhausted", "blocked_rebase_conflict"}:
                record.authority_generation += 1
                record.updated_at = self._now().isoformat()
                self._write(record)

    def create(self, record: TaskRecord) -> TaskRecord:
        with self._lock:
            if self._path(record.task_id).exists():
                raise TaskError("task_exists", "task already exists")
            self._write(record)
            return record

    def save(self, record: TaskRecord) -> TaskRecord:
        with self._lock:
            if not self._path(record.task_id).is_file():
                raise TaskError("unknown_task", "task was not found")
            self._write(record)
            return record

    def get(self, task_id: str) -> TaskRecord:
        with self._lock:
            path = self._path(task_id)
            if not path.is_file():
                raise TaskError("unknown_task", "task was not found")
            return TaskRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[TaskRecord]:
        result: list[TaskRecord] = []
        for path in sorted(self.directory.glob("hm_task_*.json")):
            try:
                result.append(TaskRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return result


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    branch: str
    dirty: bool
    status_lines: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"head": self.head, "branch": self.branch, "dirty": self.dirty, "status_lines": list(self.status_lines)}


class WorktreeManager:
    def __init__(self, *, repo_root: Path, repositories: RepositoryManifest, worktree_root: Path):
        self.repo_root = repo_root.resolve()
        self.repositories = repositories
        self.worktree_root = worktree_root.expanduser().resolve()
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    def _repository(self, repository_id: str) -> tuple[Repository, Path]:
        spec = self.repositories.repositories.get(repository_id)
        if spec is None:
            raise TaskError("unknown_repository", "repository_id is not configured")
        path = (self.repo_root / spec.path).resolve()
        if not path.is_dir():
            raise TaskError("repository_missing", "configured repository is missing")
        return spec, path

    @staticmethod
    def _run(cwd: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *argv], cwd=cwd, text=True, encoding="utf-8", errors="replace",
            capture_output=True, shell=False, timeout=120, check=False,
        )
        if check and result.returncode != 0:
            raise TaskError("git_failed", (result.stderr.strip() or "Git operation failed")[-1000:])
        return result

    def resolve_base(self, repository_id: str, requested: str | None) -> str:
        spec, repo = self._repository(repository_id)
        ref = _validate_ref(requested or spec.default_base_ref)
        if ref not in spec.allowed_base_refs:
            raise TaskError("base_ref_not_allowed", "base_ref is not allowed")
        commit = self._run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise TaskError("base_ref_unresolved", "base_ref did not resolve to a commit")
        return commit

    def create(self, *, repository_id: str, task_id: str, purpose: str, base_commit: str) -> tuple[str, Path]:
        _, repo = self._repository(repository_id)
        short = task_id.removeprefix("hm_task_")[:12]
        slug = re.sub(r"[^a-z0-9]+", "-", purpose.lower()).strip("-")[:32] or "task"
        branch = f"hm/{short}/{slug}"
        worktree = (self.worktree_root / repository_id / short).resolve()
        try:
            worktree.relative_to(self.worktree_root)
        except ValueError as exc:
            raise TaskError("worktree_path_invalid", "generated worktree escaped its root") from exc
        if worktree.exists():
            raise TaskError("worktree_exists", "generated worktree already exists")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(repo, "worktree", "add", "-b", branch, str(worktree), base_commit, check=False)
        if result.returncode != 0:
            raise TaskError("worktree_create_failed", (result.stderr.strip() or "git worktree add failed")[-1000:])
        return branch, worktree

    def snapshot(self, worktree: Path) -> GitSnapshot:
        if not worktree.is_dir():
            raise TaskError("worktree_missing", "task worktree is missing")
        head = self._run(worktree, "rev-parse", "HEAD").stdout.strip().lower()
        branch = self._run(worktree, "branch", "--show-current").stdout.strip()
        lines = tuple(x for x in self._run(worktree, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines() if x)
        return GitSnapshot(head, branch, bool(lines), lines)

    def checkpoint(self, worktree: Path, message: str) -> tuple[str | None, GitSnapshot]:
        before = self.snapshot(worktree)
        if not before.dirty:
            return None, before
        self._run(worktree, "add", "-A")
        result = self._run(
            worktree, "-c", "user.name=VESTIGIA House Mechanic",
            "-c", "user.email=house-mechanic@localhost", "commit", "--no-gpg-sign", "-m", message, check=False,
        )
        if result.returncode != 0:
            raise TaskError("checkpoint_failed", (result.stderr.strip() or "checkpoint commit failed")[-1000:])
        after = self.snapshot(worktree)
        return after.head, after

    def rebase_onto(self, worktree: Path, target_commit: str) -> tuple[bool, GitSnapshot, str | None]:
        before = self.snapshot(worktree)
        if before.dirty:
            raise TaskError("worktree_dirty", "base refresh refuses uncheckpointed changes")
        result = self._run(worktree, "rebase", target_commit, check=False)
        after = self.snapshot(worktree)
        if result.returncode == 0:
            return True, after, None
        detail = (result.stderr.strip() or result.stdout.strip() or "git rebase failed")[-1000:]
        return False, after, detail

    def abort_rebase(self, worktree: Path) -> GitSnapshot:
        result = self._run(worktree, "rebase", "--abort", check=False)
        if result.returncode != 0:
            raise TaskError("rebase_abort_failed", (result.stderr.strip() or "git rebase --abort failed")[-1000:])
        return self.snapshot(worktree)

    def materialize_detached(
        self,
        *,
        repository_id: str,
        service_id: str,
        deployment_id: str,
        commit: str,
    ) -> Path:
        _, repo = self._repository(repository_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", service_id):
            raise TaskError("invalid_service_id", "service_id is outside the bounded grammar")
        if not re.fullmatch(r"hm_deploy_[0-9a-f]{32}", deployment_id):
            raise TaskError("invalid_deployment_id", "deployment_id is not an issued deployment id")
        resolved = self._run(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").stdout.strip().lower()
        if resolved != commit.lower():
            raise TaskError("deployment_commit_unresolved", "deployment commit did not resolve exactly")
        short = deployment_id.removeprefix("hm_deploy_")[:12]
        worktree = (self.worktree_root / "_deployments" / service_id / short).resolve()
        try:
            worktree.relative_to(self.worktree_root)
        except ValueError as exc:
            raise TaskError("deployment_path_invalid", "generated deployment path escaped its root") from exc
        if worktree.exists():
            raise TaskError("deployment_worktree_exists", "generated deployment worktree already exists")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(repo, "worktree", "add", "--detach", str(worktree), resolved, check=False)
        if result.returncode != 0:
            raise TaskError(
                "deployment_worktree_create_failed",
                (result.stderr.strip() or "git worktree add failed")[-1000:],
            )
        return worktree

    def remove_detached(
        self,
        repository_id: str,
        worktree: Path,
        *,
        expected_commit: str | None = None,
    ) -> None:
        _, repo = self._repository(repository_id)
        worktree = worktree.resolve()
        deployment_root = (self.worktree_root / "_deployments").resolve()
        try:
            worktree.relative_to(deployment_root)
        except ValueError as exc:
            raise TaskError(
                "deployment_path_invalid",
                "deployment cleanup path is outside the deployment root",
            ) from exc
        if not worktree.exists():
            self._run(repo, "worktree", "prune")
            return
        snapshot = self.snapshot(worktree)
        if expected_commit is not None and snapshot.head != expected_commit.lower():
            raise TaskError(
                "deployment_commit_mismatch",
                "deployment cleanup commit does not match recorded provenance",
            )
        if snapshot.dirty:
            raise TaskError(
                "deployment_worktree_dirty",
                "deployment cleanup refuses a dirty checkout",
            )
        result = self._run(repo, "worktree", "remove", str(worktree), check=False)
        if result.returncode != 0:
            raise TaskError(
                "deployment_worktree_remove_failed",
                (result.stderr.strip() or "git worktree remove failed")[-1000:],
            )
        self._run(repo, "worktree", "prune")

    def remove(self, repository_id: str, worktree: Path) -> None:
        _, repo = self._repository(repository_id)
        if self.snapshot(worktree).dirty:
            raise TaskError("worktree_dirty", "cleanup refuses uncheckpointed changes")
        result = self._run(repo, "worktree", "remove", str(worktree), check=False)
        if result.returncode != 0:
            raise TaskError("worktree_remove_failed", (result.stderr.strip() or "worktree remove failed")[-1000:])
        self._run(repo, "worktree", "prune")


class TaskSupervisor:
    def __init__(
        self, *, ledger: TaskLedger, worktrees: WorktreeManager,
        now: Callable[[], datetime] | None = None,
        default_lease_seconds: int = 3600, max_lease_seconds: int = 86400,
        default_iteration_limit: int = 4, max_iteration_limit: int = 32,
    ):
        self.ledger = ledger
        self.worktrees = worktrees
        self._now = now or (lambda: datetime.now(UTC))
        self.default_lease_seconds = default_lease_seconds
        self.max_lease_seconds = max_lease_seconds
        self.default_iteration_limit = default_iteration_limit
        self.max_iteration_limit = max_iteration_limit
        self._lock = threading.RLock()

    @staticmethod
    def _holder(value: str) -> str:
        value = value.strip()
        if not _ID.fullmatch(value):
            raise TaskError("invalid_holder", "holder_id is outside the bounded grammar")
        return value

    def _lease(self, value: int | None) -> int:
        value = self.default_lease_seconds if value is None else value
        if not isinstance(value, int) or value <= 0:
            raise TaskError("invalid_lease", "lease_seconds must be positive")
        return min(value, self.max_lease_seconds)

    def _iterations(self, value: int | None) -> int:
        value = self.default_iteration_limit if value is None else value
        if not isinstance(value, int) or value <= 0:
            raise TaskError("invalid_budget", "iteration_limit must be positive")
        return min(value, self.max_iteration_limit)

    def _authorize(self, record: TaskRecord, holder_id: str, generation: int, *, allow_handoff: bool = True) -> None:
        if record.holder_id != self._holder(holder_id):
            raise TaskError("wrong_holder", "holder_id does not own this task")
        if record.authority_generation != generation:
            raise TaskError("stale_authority", "authority_generation is stale")
        states = {"active", "handoff_pending"} if allow_handoff else {"active"}
        if record.state not in states:
            raise TaskError("task_not_active", "task state does not admit mutation")
        if self._now() >= datetime.fromisoformat(record.lease_expires_at):
            record.state = "suspended_unverified"
            record.updated_at = self._now().isoformat()
            self.ledger.save(record)
            raise TaskError("lease_expired", "task lease expired before action admission")

    def acquire(
        self, *, repository_id: str, holder_id: str, purpose: str,
        base_ref: str | None = None, lease_seconds: int | None = None,
        iteration_limit: int | None = None,
    ) -> TaskRecord:
        with self._lock:
            holder = self._holder(holder_id)
            purpose = purpose.strip()
            if not purpose or len(purpose) > 512:
                raise TaskError("invalid_purpose", "purpose must contain 1-512 characters")
            base_commit = self.worktrees.resolve_base(repository_id, base_ref)
            task_id = f"hm_task_{uuid.uuid4().hex}"
            branch, worktree = self.worktrees.create(
                repository_id=repository_id, task_id=task_id, purpose=purpose, base_commit=base_commit,
            )
            now = self._now()
            record = TaskRecord(
                task_id=task_id, repository_id=repository_id, purpose=purpose, holder_id=holder,
                base_commit=base_commit, branch_name=branch, worktree_path=str(worktree),
                authority_generation=1, acquired_at=now.isoformat(),
                lease_expires_at=(now + timedelta(seconds=self._lease(lease_seconds))).isoformat(),
                iteration_limit=self._iterations(iteration_limit), iterations_used=0, state="active",
                current_base_commit=base_commit,
                updated_at=now.isoformat(),
            )
            try:
                return self.ledger.create(record)
            except Exception:
                try:
                    self.worktrees.remove(repository_id, worktree)
                except Exception:
                    pass
                raise

    def show(self, task_id: str) -> tuple[TaskRecord, GitSnapshot | None]:
        record = self.ledger.get(task_id)
        try:
            snapshot = self.worktrees.snapshot(Path(record.worktree_path))
        except TaskError:
            snapshot = None
        return record, snapshot

    def resume(self, *, task_id: str, holder_id: str, lease_seconds: int | None = None) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.state != "suspended_unverified":
                raise TaskError("task_not_suspended", "only suspended tasks may resume")
            if record.current_iteration_id is not None:
                raise TaskError("interrupted_iteration", "open iteration requires explicit recovery")
            snap = self.worktrees.snapshot(Path(record.worktree_path))
            if snap.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            now = self._now()
            record.authority_generation += 1
            record.lease_expires_at = (now + timedelta(seconds=self._lease(lease_seconds))).isoformat()
            record.state = "active"
            record.updated_at = now.isoformat()
            return self.ledger.save(record)

    def recover_interrupted_iteration(self, *, task_id: str, holder_id: str, lease_seconds: int | None = None) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.state != "suspended_unverified" or record.current_iteration_id is None:
                raise TaskError("no_interrupted_iteration", "no interrupted iteration is present")
            snap = self.worktrees.snapshot(Path(record.worktree_path))
            if snap.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            now = self._now()
            record.current_iteration_id = None
            record.current_iteration_started_at = None
            record.authority_generation += 1
            record.lease_expires_at = (now + timedelta(seconds=self._lease(lease_seconds))).isoformat()
            record.state = "active"
            record.updated_at = now.isoformat()
            return self.ledger.save(record)

    def begin_iteration(self, *, task_id: str, holder_id: str, authority_generation: int) -> tuple[TaskRecord, GitSnapshot]:
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation)
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "task already has an open iteration")
            if record.iterations_used >= record.iteration_limit:
                record.state = "paused_budget_exhausted"
                record.updated_at = self._now().isoformat()
                self.ledger.save(record)
                raise TaskError("iteration_budget_exhausted", "iteration budget is exhausted")
            snap = self.worktrees.snapshot(Path(record.worktree_path))
            if snap.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            now = self._now()
            record.iterations_used += 1
            record.current_iteration_id = f"hm_iter_{record.iterations_used:04d}_{uuid.uuid4().hex[:12]}"
            record.current_iteration_started_at = now.isoformat()
            record.updated_at = now.isoformat()
            return self.ledger.save(record), snap

    def checkpoint_iteration(
        self, *, task_id: str, holder_id: str, authority_generation: int,
        iteration_id: str, outcome: str,
    ) -> tuple[TaskRecord, str | None, GitSnapshot]:
        if outcome not in {"pass", "fail", "blocked", "not_run"}:
            raise TaskError("invalid_outcome", "unsupported iteration outcome")
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation)
            if record.current_iteration_id != iteration_id:
                raise TaskError("iteration_mismatch", "iteration_id is not current")
            message = (
                f"House Mechanic checkpoint: {record.task_id} iteration {record.iterations_used}\n\n"
                f"Task-Id: {record.task_id}\nIteration-Id: {iteration_id}\n"
                f"Holder-Id: {record.holder_id}\nAuthority-Generation: {record.authority_generation}\n"
                f"Outcome: {outcome}\n"
            )
            commit, snap = self.worktrees.checkpoint(Path(record.worktree_path), message)
            record.current_iteration_id = None
            record.current_iteration_started_at = None
            if record.iterations_used >= record.iteration_limit:
                record.state = "paused_budget_exhausted"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record), commit, snap

    def renew(
        self, *, task_id: str, holder_id: str, authority_generation: int,
        lease_seconds: int | None = None,
    ) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation, allow_handoff=False)
            now = self._now()
            current_expiry = datetime.fromisoformat(record.lease_expires_at)
            candidate = now + timedelta(seconds=self._lease(lease_seconds))
            if candidate <= current_expiry:
                raise TaskError("lease_not_extended", "renewal must move lease expiry forward")
            record.lease_expires_at = candidate.isoformat()
            record.updated_at = now.isoformat()
            return self.ledger.save(record)

    def extend_budget(
        self, *, task_id: str, holder_id: str, authority_generation: int,
        additional_iterations: int,
    ) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.authority_generation != authority_generation:
                raise TaskError("stale_authority", "authority_generation is stale")
            if record.state not in {"active", "paused_budget_exhausted"}:
                raise TaskError("task_not_extendable", "task state does not admit budget extension")
            if self._now() >= datetime.fromisoformat(record.lease_expires_at):
                record.state = "suspended_unverified"
                record.updated_at = self._now().isoformat()
                self.ledger.save(record)
                raise TaskError("lease_expired", "task lease expired before budget extension")
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "budget cannot change during an open iteration")
            if not isinstance(additional_iterations, int) or isinstance(additional_iterations, bool) or additional_iterations <= 0:
                raise TaskError("invalid_budget", "additional_iterations must be a positive integer")
            new_limit = record.iteration_limit + additional_iterations
            if new_limit > self.max_iteration_limit:
                raise TaskError("budget_ceiling_exceeded", "requested iteration budget exceeds configured ceiling")
            record.iteration_limit = new_limit
            if record.state == "paused_budget_exhausted" and record.iterations_used < record.iteration_limit:
                record.state = "active"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record)

    def refresh_base(
        self, *, task_id: str, holder_id: str, authority_generation: int,
        base_ref: str | None = None,
    ) -> tuple[TaskRecord, str, bool, GitSnapshot, str | None]:
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation, allow_handoff=False)
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "base refresh refuses an open iteration")
            worktree = Path(record.worktree_path)
            before = self.worktrees.snapshot(worktree)
            if before.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            if before.dirty:
                raise TaskError("worktree_dirty", "base refresh refuses uncheckpointed changes")
            candidate = self.worktrees.resolve_base(record.repository_id, base_ref)
            record.pending_base_commit = candidate
            record.updated_at = self._now().isoformat()
            self.ledger.save(record)
            success, after, detail = self.worktrees.rebase_onto(worktree, candidate)
            if not success:
                record.state = "blocked_rebase_conflict"
                record.updated_at = self._now().isoformat()
                self.ledger.save(record)
                return record, candidate, False, after, detail
            record.current_base_commit = candidate
            record.pending_base_commit = None
            record.authority_generation += 1
            record.state = "active"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record), candidate, True, after, None

    def abort_refresh(
        self, *, task_id: str, holder_id: str, authority_generation: int,
    ) -> tuple[TaskRecord, GitSnapshot]:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.authority_generation != authority_generation:
                raise TaskError("stale_authority", "authority_generation is stale")
            if record.state != "blocked_rebase_conflict":
                raise TaskError("refresh_not_blocked", "task is not blocked on a base-refresh conflict")
            after = self.worktrees.abort_rebase(Path(record.worktree_path))
            if after.branch != record.branch_name:
                raise TaskError("branch_mismatch", "aborted worktree branch does not match task record")
            record.pending_base_commit = None
            record.authority_generation += 1
            record.state = "active"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record), after

    def deployment_candidate_source(
        self,
        *,
        task_id: str,
        holder_id: str,
        authority_generation: int,
        repository_id: str,
    ) -> tuple[TaskRecord, GitSnapshot]:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.authority_generation != authority_generation:
                raise TaskError("stale_authority", "authority_generation is stale")
            if record.state not in {"active", "paused_budget_exhausted"}:
                raise TaskError("task_not_deployable", "task state does not admit candidate deployment")
            if self._now() >= datetime.fromisoformat(record.lease_expires_at):
                record.state = "suspended_unverified"
                record.updated_at = self._now().isoformat()
                self.ledger.save(record)
                raise TaskError("lease_expired", "task lease expired before deployment admission")
            if record.repository_id != repository_id:
                raise TaskError("repository_mismatch", "task repository does not match service deployment binding")
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "candidate deployment refuses an open iteration")
            snapshot = self.worktrees.snapshot(Path(record.worktree_path))
            if snapshot.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            if snapshot.dirty:
                raise TaskError("worktree_dirty", "candidate deployment requires a clean checkpointed worktree")
            return record, snapshot

    def handoff_offer(self, *, task_id: str, holder_id: str, authority_generation: int, recipient_id: str) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation, allow_handoff=False)
            recipient = self._holder(recipient_id)
            if recipient == record.holder_id:
                raise TaskError("invalid_handoff", "recipient already owns task")
            record.pending_handoff_to = recipient
            record.state = "handoff_pending"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record)

    def handoff_respond(self, *, task_id: str, recipient_id: str, accept: bool, lease_seconds: int | None = None) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            recipient = self._holder(recipient_id)
            if record.state != "handoff_pending" or record.pending_handoff_to != recipient:
                raise TaskError("handoff_not_offered", "no matching handoff is pending")
            if not accept:
                record.pending_handoff_to = None
                record.state = "active"
                record.updated_at = self._now().isoformat()
                return self.ledger.save(record)
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "handoff cannot commit during an open iteration")
            snap = self.worktrees.snapshot(Path(record.worktree_path))
            if snap.branch != record.branch_name:
                raise TaskError("branch_mismatch", "worktree branch does not match task record")
            now = self._now()
            record.holder_id = recipient
            record.pending_handoff_to = None
            record.authority_generation += 1
            record.lease_expires_at = (now + timedelta(seconds=self._lease(lease_seconds))).isoformat()
            record.state = "active"
            record.updated_at = now.isoformat()
            return self.ledger.save(record)

    def finish(self, *, task_id: str, holder_id: str, authority_generation: int, state: str, reason: str | None = None) -> TaskRecord:
        if state not in _TERMINAL - {"released"}:
            raise TaskError("invalid_terminal_state", "terminal state is not allowed")
        with self._lock:
            record = self.ledger.get(task_id)
            self._authorize(record, holder_id, authority_generation)
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "task cannot finish with an open iteration")
            record.state = state
            record.terminal_reason = (reason or "").strip()[:512] or None
            record.pending_handoff_to = None
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record)

    def cleanup(self, *, task_id: str, holder_id: str) -> TaskRecord:
        with self._lock:
            record = self.ledger.get(task_id)
            if record.holder_id != self._holder(holder_id):
                raise TaskError("wrong_holder", "holder_id does not own this task")
            if record.state not in _TERMINAL - {"released"}:
                raise TaskError("cleanup_not_allowed", "cleanup requires a terminal task")
            if record.current_iteration_id is not None:
                raise TaskError("iteration_open", "cleanup refuses an open iteration")
            self.worktrees.remove(record.repository_id, Path(record.worktree_path))
            record.state = "released"
            record.updated_at = self._now().isoformat()
            return self.ledger.save(record)
