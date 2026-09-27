from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import importlib
import importlib.util
import json
import os
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
    return repo, supervisor


def _source_ops():
    spec = importlib.util.find_spec("house_mechanic.source_ops")
    assert spec is not None, "task-scoped source operations module is missing"
    module = importlib.import_module("house_mechanic.source_ops")
    return module.SourceOpError, module.PatchProposalStore, module.TaskSourceWorkspace


def _workspace(tmp_path: Path, *, now=None):
    _, supervisor = _setup(tmp_path, now=now)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="vestigia",
        purpose="source operations",
    )
    SourceOpError, PatchProposalStore, TaskSourceWorkspace = _source_ops()
    proposals = PatchProposalStore(tmp_path / "proposals")
    workspace = TaskSourceWorkspace(tasks=supervisor, proposal_store=proposals)
    return task, supervisor, workspace, SourceOpError


def test_read_returns_utf8_text_hash_and_relative_path(tmp_path: Path) -> None:
    task, _, workspace, _ = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    (worktree / "second.txt").write_text("two\n", encoding="utf-8")

    result = workspace.read(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        paths=["hello.txt", "second.txt"],
    )

    assert [item["path"] for item in result["items"]] == ["hello.txt", "second.txt"]
    assert result["items"][0]["text"] == "one\n"
    assert result["items"][0]["sha256"] == hashlib.sha256(b"one\n").hexdigest()
    assert result["items"][0]["size_bytes"] == 4
    assert result["items"][0]["encoding"] == "utf-8"
    assert result["items"][0]["truncated"] is False


def test_read_does_not_require_open_iteration(tmp_path: Path) -> None:
    task, _, workspace, _ = _workspace(tmp_path)

    result = workspace.read(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        paths=["hello.txt"],
    )

    assert result["task_id"] == task.task_id
    assert result["authority_generation"] == 1


def test_read_refuses_parent_escape_and_absolute_path(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError = _workspace(tmp_path)

    for path in ["../outside.txt", str((tmp_path / "outside.txt").resolve())]:
        with pytest.raises(SourceOpError) as exc:
            workspace.read(
                task_id=task.task_id,
                holder_id="vestigia",
                authority_generation=1,
                paths=[path],
            )
        assert exc.value.code == "unsafe_path"


def test_read_refuses_symlink_escape(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = Path(task.worktree_path) / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SourceOpError) as exc:
        workspace.read(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            paths=["link.txt"],
        )
    assert exc.value.code == "symlink_refused"


def test_read_refuses_non_utf8_and_oversize_file(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    (worktree / "binary.dat").write_bytes(b"\xff\xfe\x00")
    (worktree / "huge.txt").write_text("x" * (1_048_576 + 1), encoding="utf-8")

    with pytest.raises(SourceOpError) as binary:
        workspace.read(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            paths=["binary.dat"],
        )
    assert binary.value.code == "non_utf8"

    with pytest.raises(SourceOpError) as huge:
        workspace.read(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            paths=["huge.txt"],
        )
    assert huge.value.code == "file_too_large"


def test_authorized_worktree_refuses_wrong_holder_stale_generation_and_expired_lease(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    clock = {"value": now}
    _, supervisor = _setup(tmp_path, now=lambda: clock["value"])
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="vestigia",
        purpose="authority",
        lease_seconds=60,
    )

    with pytest.raises(TaskError) as wrong:
        supervisor.authorized_worktree(
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=1,
        )
    assert wrong.value.code == "wrong_holder"

    with pytest.raises(TaskError) as stale:
        supervisor.authorized_worktree(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=9,
        )
    assert stale.value.code == "stale_authority"

    clock["value"] = now + timedelta(seconds=61)
    with pytest.raises(TaskError) as expired:
        supervisor.authorized_worktree(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
        )
    assert expired.value.code == "lease_expired"
