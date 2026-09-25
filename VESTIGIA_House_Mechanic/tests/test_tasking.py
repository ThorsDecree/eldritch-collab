from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess

import pytest

from house_mechanic.tasking import (
    TaskError,
    TaskLedger,
    TaskSupervisor,
    WorktreeManager,
    load_repository_manifest,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _setup(tmp_path: Path, *, now=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "hello.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "initial")

    manifest_path = tmp_path / "repositories.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-repositories.v0.1",
                "repositories": [
                    {
                        "id": "fixture",
                        "path": "repo",
                        "default_base_ref": "main",
                        "allowed_base_refs": ["main"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_repository_manifest(manifest_path, tmp_path)
    ledger = TaskLedger(tmp_path / "state", now=now)
    worktrees = WorktreeManager(
        repo_root=tmp_path,
        repositories=manifest,
        worktree_root=tmp_path / "worktrees",
    )
    supervisor = TaskSupervisor(ledger=ledger, worktrees=worktrees, now=now)
    return repo, ledger, worktrees, supervisor


def test_acquire_isolates_worktrees_and_pins_base(tmp_path: Path) -> None:
    repo, _, _, supervisor = _setup(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")

    first = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="repair bells",
    )
    second = supervisor.acquire(
        repository_id="fixture",
        holder_id="vestigia",
        purpose="repair receipts",
    )

    assert first.base_commit == base == second.base_commit
    assert first.worktree_path != second.worktree_path
    assert first.branch_name != second.branch_name

    (repo / "hello.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "upstream moves")
    assert supervisor.show(first.task_id)[0].base_commit == base


def test_authority_tuple_blocks_wrong_holder_and_generation(tmp_path: Path) -> None:
    _, _, _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="authority",
    )

    with pytest.raises(TaskError) as wrong:
        supervisor.begin_iteration(
            task_id=task.task_id,
            holder_id="anima",
            authority_generation=1,
        )
    assert wrong.value.code == "wrong_holder"

    with pytest.raises(TaskError) as stale:
        supervisor.begin_iteration(
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=9,
        )
    assert stale.value.code == "stale_authority"


def test_restart_suspends_then_resume_rotates_generation(tmp_path: Path) -> None:
    _, ledger, worktrees, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="restart",
    )

    restarted_ledger = TaskLedger(ledger.directory)
    assert restarted_ledger.get(task.task_id).state == "suspended_unverified"

    restarted = TaskSupervisor(ledger=restarted_ledger, worktrees=worktrees)
    resumed = restarted.resume(task_id=task.task_id, holder_id="liora")
    assert resumed.state == "active"
    assert resumed.authority_generation == 2


def test_failing_iteration_becomes_recoverable_checkpoint(tmp_path: Path) -> None:
    _, _, _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="checkpoint",
        iteration_limit=2,
    )
    opened, start = supervisor.begin_iteration(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
    )
    worktree = Path(opened.worktree_path)
    (worktree / "hello.txt").write_text("broken\n", encoding="utf-8")

    closed, commit, snapshot = supervisor.checkpoint_iteration(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
        iteration_id=opened.current_iteration_id or "",
        outcome="fail",
    )

    assert commit and snapshot.head == commit and snapshot.head != start.head
    assert snapshot.dirty is False
    assert closed.state == "active"
    message = _git(worktree, "log", "-1", "--pretty=%B")
    assert f"Task-Id: {task.task_id}" in message
    assert "Outcome: fail" in message


def test_no_change_iteration_creates_no_commit(tmp_path: Path) -> None:
    _, _, _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="diagnostic",
        iteration_limit=2,
    )
    opened, start = supervisor.begin_iteration(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
    )
    closed, commit, snapshot = supervisor.checkpoint_iteration(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
        iteration_id=opened.current_iteration_id or "",
        outcome="not_run",
    )
    assert commit is None
    assert snapshot.head == start.head
    assert closed.current_iteration_id is None


def test_handoff_requires_acceptance_and_rotates_generation(tmp_path: Path) -> None:
    _, _, _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="handoff",
    )
    pending = supervisor.handoff_offer(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
        recipient_id="vestigia",
    )
    assert pending.holder_id == "liora"
    assert pending.state == "handoff_pending"

    accepted = supervisor.handoff_respond(
        task_id=task.task_id,
        recipient_id="vestigia",
        accept=True,
    )
    assert accepted.holder_id == "vestigia"
    assert accepted.authority_generation == 2


def test_open_iteration_after_restart_requires_explicit_recovery(tmp_path: Path) -> None:
    _, ledger, worktrees, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="crash",
    )
    supervisor.begin_iteration(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
    )

    restarted_ledger = TaskLedger(ledger.directory)
    restarted = TaskSupervisor(ledger=restarted_ledger, worktrees=worktrees)
    with pytest.raises(TaskError) as blocked:
        restarted.resume(task_id=task.task_id, holder_id="liora")
    assert blocked.value.code == "interrupted_iteration"

    recovered = restarted.recover_interrupted_iteration(
        task_id=task.task_id,
        holder_id="liora",
    )
    assert recovered.state == "active"
    assert recovered.authority_generation == 2


def test_expired_lease_blocks_new_mutation_then_revalidates(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 25, tzinfo=UTC)]

    def now() -> datetime:
        return clock[0]

    _, _, _, supervisor = _setup(tmp_path, now=now)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="expiry",
        lease_seconds=10,
    )
    clock[0] += timedelta(seconds=11)

    with pytest.raises(TaskError) as expired:
        supervisor.begin_iteration(
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=1,
        )
    assert expired.value.code == "lease_expired"

    resumed = supervisor.resume(task_id=task.task_id, holder_id="liora")
    assert resumed.authority_generation == 2


def test_cleanup_requires_terminal_state_and_preserves_branch(tmp_path: Path) -> None:
    repo, _, _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="liora",
        purpose="cleanup",
    )

    with pytest.raises(TaskError) as denied:
        supervisor.cleanup(task_id=task.task_id, holder_id="liora")
    assert denied.value.code == "cleanup_not_allowed"

    finished = supervisor.finish(
        task_id=task.task_id,
        holder_id="liora",
        authority_generation=1,
        state="succeeded",
    )
    released = supervisor.cleanup(task_id=task.task_id, holder_id="liora")
    assert released.state == "released"
    assert not Path(finished.worktree_path).exists()
    assert task.branch_name in _git(repo, "branch", "--list", task.branch_name)
