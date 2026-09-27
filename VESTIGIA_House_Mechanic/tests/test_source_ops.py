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
    (repo / "hello.txt").write_bytes(b"one\n")
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
    (worktree / "second.txt").write_bytes(b"two\n")

    expected = (worktree / "hello.txt").read_bytes()
    result = workspace.read(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        paths=["hello.txt", "second.txt"],
    )

    assert [item["path"] for item in result["items"]] == ["hello.txt", "second.txt"]
    assert result["items"][0]["text"] == expected.decode("utf-8")
    assert result["items"][0]["sha256"] == hashlib.sha256(expected).hexdigest()
    assert result["items"][0]["size_bytes"] == len(expected)
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


def test_read_refuses_windows_special_path_components(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError = _workspace(tmp_path)

    for path in [
        "hello.txt:secret",
        "NUL",
        "folder/COM1.txt",
        "trailing.",
        "trailing ",
        "bad?.txt",
    ]:
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


def _begin_iteration(supervisor: TaskSupervisor, task) -> str:
    record, _ = supervisor.begin_iteration(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=task.authority_generation,
    )
    assert record.current_iteration_id is not None
    return record.current_iteration_id


def test_diff_creates_durable_proposal_without_mutating_worktree(tmp_path: Path) -> None:
    task, supervisor, workspace, _ = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    before = (worktree / "hello.txt").read_bytes()
    text = before.decode("utf-8")
    iteration_id = _begin_iteration(supervisor, task)

    result = workspace.diff(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        mutations=[
            {
                "op": "modify",
                "path": "hello.txt",
                "expected_sha256": hashlib.sha256(before).hexdigest(),
                "old": text,
                "new": text.replace("one", "ONE", 1),
            }
        ],
    )

    assert (worktree / "hello.txt").read_bytes() == before
    assert result["state"] == "ready"
    assert result["proposal_id"].startswith("hm_patch_")
    assert len(result["proposal_digest"]) == 64
    stored = workspace.proposal_store.get(result["proposal_id"]).to_dict()
    assert stored["proposal_digest"] == result["proposal_digest"]
    assert stored["task_id"] == task.task_id
    assert stored["iteration_id"] == iteration_id
    assert stored["mutations"][0]["pre_state"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert "--- a/hello.txt" in stored["unified_diff"]
    assert "+++ b/hello.txt" in stored["unified_diff"]


def test_diff_accepts_atomic_modify_and_create_set(tmp_path: Path) -> None:
    task, supervisor, workspace, _ = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    before = (worktree / "hello.txt").read_bytes()
    text = before.decode("utf-8")
    iteration_id = _begin_iteration(supervisor, task)

    result = workspace.diff(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        mutations=[
            {
                "op": "modify",
                "path": "hello.txt",
                "expected_sha256": hashlib.sha256(before).hexdigest(),
                "old": text,
                "new": text.replace("one", "ONE", 1),
            },
            {
                "op": "create",
                "path": "Vesti/new_machine.py",
                "expected_state": "absent",
                "content": "print('hello from the workbench')\n",
            },
        ],
    )

    assert not (worktree / "Vesti" / "new_machine.py").exists()
    proposal = workspace.proposal_store.get(result["proposal_id"]).to_dict()
    assert [item["op"] for item in proposal["mutations"]] == ["modify", "create"]
    assert proposal["mutations"][1]["pre_state"] == {"state": "absent"}


def test_diff_refuses_duplicate_paths(tmp_path: Path) -> None:
    task, supervisor, workspace, SourceOpError = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    before = (worktree / "hello.txt").read_bytes()
    text = before.decode("utf-8")
    iteration_id = _begin_iteration(supervisor, task)

    with pytest.raises(SourceOpError) as exc:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {
                    "op": "modify",
                    "path": "hello.txt",
                    "expected_sha256": hashlib.sha256(before).hexdigest(),
                    "old": text,
                    "new": text,
                },
                {
                    "op": "create",
                    "path": "hello.txt",
                    "expected_state": "absent",
                    "content": "collision\n",
                },
            ],
        )
    assert exc.value.code == "duplicate_path"


def test_diff_refuses_stale_modify_hash(tmp_path: Path) -> None:
    task, supervisor, workspace, SourceOpError = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    text = (worktree / "hello.txt").read_text(encoding="utf-8")
    iteration_id = _begin_iteration(supervisor, task)

    with pytest.raises(SourceOpError) as exc:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {
                    "op": "modify",
                    "path": "hello.txt",
                    "expected_sha256": "0" * 64,
                    "old": text,
                    "new": text,
                }
            ],
        )
    assert exc.value.code == "expected_hash_mismatch"


def test_diff_refuses_create_when_target_exists(tmp_path: Path) -> None:
    task, supervisor, workspace, SourceOpError = _workspace(tmp_path)
    iteration_id = _begin_iteration(supervisor, task)

    with pytest.raises(SourceOpError) as exc:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {
                    "op": "create",
                    "path": "hello.txt",
                    "expected_state": "absent",
                    "content": "nope\n",
                }
            ],
        )
    assert exc.value.code == "expected_absent_exists"


def test_diff_requires_current_iteration(tmp_path: Path) -> None:
    task, supervisor, workspace, _ = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    before = (worktree / "hello.txt").read_bytes()
    text = before.decode("utf-8")
    mutation = {
        "op": "modify",
        "path": "hello.txt",
        "expected_sha256": hashlib.sha256(before).hexdigest(),
        "old": text,
        "new": text,
    }

    with pytest.raises(TaskError) as missing:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id="hm_iter_missing",
            mutations=[mutation],
        )
    assert missing.value.code == "iteration_required"

    current = _begin_iteration(supervisor, task)
    with pytest.raises(TaskError) as wrong:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=current + "-stale",
            mutations=[mutation],
        )
    assert wrong.value.code == "iteration_mismatch"


def test_diff_enforces_file_count_file_size_and_total_payload_limits(tmp_path: Path) -> None:
    task, supervisor, workspace, SourceOpError = _workspace(tmp_path)
    _, _, TaskSourceWorkspace = _source_ops()
    iteration_id = _begin_iteration(supervisor, task)

    count_limited = TaskSourceWorkspace(
        tasks=supervisor,
        proposal_store=workspace.proposal_store,
        max_files=1,
    )
    with pytest.raises(SourceOpError) as too_many:
        count_limited.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {"op": "create", "path": "a.txt", "expected_state": "absent", "content": "a"},
                {"op": "create", "path": "b.txt", "expected_state": "absent", "content": "b"},
            ],
        )
    assert too_many.value.code == "too_many_files"

    file_limited = TaskSourceWorkspace(
        tasks=supervisor,
        proposal_store=workspace.proposal_store,
        max_file_bytes=4,
    )
    with pytest.raises(SourceOpError) as file_big:
        file_limited.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {
                    "op": "create",
                    "path": "large.txt",
                    "expected_state": "absent",
                    "content": "12345",
                }
            ],
        )
    assert file_big.value.code == "file_too_large"

    patch_limited = TaskSourceWorkspace(
        tasks=supervisor,
        proposal_store=workspace.proposal_store,
        max_patch_bytes=5,
    )
    with pytest.raises(SourceOpError) as patch_big:
        patch_limited.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            mutations=[
                {
                    "op": "create",
                    "path": "payload.txt",
                    "expected_state": "absent",
                    "content": "abcdef",
                }
            ],
        )
    assert patch_big.value.code == "patch_too_large"


def _modify_create_proposal(tmp_path: Path):
    task, supervisor, workspace, SourceOpError = _workspace(tmp_path)
    worktree = Path(task.worktree_path)
    before = (worktree / "hello.txt").read_bytes()
    text = before.decode("utf-8")
    iteration_id = _begin_iteration(supervisor, task)
    result = workspace.diff(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        mutations=[
            {
                "op": "modify",
                "path": "hello.txt",
                "expected_sha256": hashlib.sha256(before).hexdigest(),
                "old": text,
                "new": text.replace("one", "ONE", 1),
            },
            {
                "op": "create",
                "path": "Vesti/new_machine.py",
                "expected_state": "absent",
                "content": "print('hello from the workbench')\n",
            },
        ],
    )
    return task, supervisor, workspace, SourceOpError, worktree, before, iteration_id, result


def test_diff_refuses_rendered_preview_above_configured_limit(tmp_path: Path) -> None:
    _, supervisor = _setup(tmp_path)
    task = supervisor.acquire(
        repository_id="fixture",
        holder_id="vestigia",
        purpose="bounded diff preview",
    )
    _, PatchProposalStore, TaskSourceWorkspace = _source_ops()
    workspace = TaskSourceWorkspace(
        tasks=supervisor,
        proposal_store=PatchProposalStore(tmp_path / "proposals"),
        max_diff_bytes=64,
    )
    worktree = Path(task.worktree_path)
    original = "a" * 256 + "\n"
    (worktree / "long-line.txt").write_text(original, encoding="utf-8")
    iteration = _begin(supervisor, task)

    with pytest.raises(SourceOpError) as exc:
        workspace.diff(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration,
            mutations=[
                {
                    "op": "modify",
                    "path": "long-line.txt",
                    "expected_sha256": hashlib.sha256(
                        original.encode("utf-8")
                    ).hexdigest(),
                    "old": "a",
                    "new": "b",
                }
            ],
        )

    assert exc.value.code == "diff_too_large"


def test_patch_applies_exact_modify_and_create_proposal(tmp_path: Path) -> None:
    task, _, workspace, _, worktree, before, iteration_id, proposal = _modify_create_proposal(tmp_path)

    result = workspace.patch(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        proposal_id=proposal["proposal_id"],
        proposal_digest=proposal["proposal_digest"],
    )

    assert (worktree / "hello.txt").read_text(encoding="utf-8").startswith("ONE")
    assert (worktree / "Vesti" / "new_machine.py").read_text(encoding="utf-8") == "print('hello from the workbench')\n"
    assert [item["path"] for item in result["mutations"]] == ["hello.txt", "Vesti/new_machine.py"]
    assert result["mutations"][0]["pre_sha256"] == hashlib.sha256(before).hexdigest()
    assert len(result["mutations"][0]["post_sha256"]) == 64
    assert result["mutations"][1]["pre_state"] == "absent"
    assert workspace.proposal_store.get(proposal["proposal_id"]).state == "consumed"


def test_patch_refuses_digest_mismatch(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError, worktree, before, iteration_id, proposal = _modify_create_proposal(tmp_path)

    with pytest.raises(SourceOpError) as exc:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest="0" * 64,
        )
    assert exc.value.code == "proposal_digest_mismatch"
    assert (worktree / "hello.txt").read_bytes() == before
    assert not (worktree / "Vesti" / "new_machine.py").exists()


def test_patch_refuses_consumed_proposal_replay(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError, _, _, iteration_id, proposal = _modify_create_proposal(tmp_path)

    workspace.patch(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        proposal_id=proposal["proposal_id"],
        proposal_digest=proposal["proposal_digest"],
    )

    with pytest.raises(SourceOpError) as exc:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["proposal_digest"],
        )
    assert exc.value.code == "proposal_consumed"


def test_patch_refuses_changed_prestate_after_diff(tmp_path: Path) -> None:
    task, _, workspace, SourceOpError, worktree, _, iteration_id, proposal = _modify_create_proposal(tmp_path)
    (worktree / "hello.txt").write_text("somebody else changed this\n", encoding="utf-8")

    with pytest.raises(SourceOpError) as exc:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["proposal_digest"],
        )
    assert exc.value.code == "proposal_stale"
    assert not (worktree / "Vesti" / "new_machine.py").exists()


def test_patch_refuses_generation_or_iteration_drift(tmp_path: Path) -> None:
    task, supervisor, workspace, _, _, _, iteration_id, proposal = _modify_create_proposal(tmp_path)

    with pytest.raises(TaskError) as stale:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=2,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["proposal_digest"],
        )
    assert stale.value.code == "stale_authority"

    supervisor.checkpoint_iteration(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        outcome="not_run",
    )
    with pytest.raises(TaskError) as closed:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["proposal_digest"],
        )
    assert closed.value.code == "iteration_required"


def test_patch_rolls_back_all_targets_on_mid_apply_failure(tmp_path: Path, monkeypatch) -> None:
    task, _, workspace, SourceOpError, worktree, before, iteration_id, proposal = _modify_create_proposal(tmp_path)
    original_replace = workspace._replace_file
    calls = {"count": 0}

    def fail_second(source: Path, target: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected second-file failure")
        original_replace(source, target)

    monkeypatch.setattr(workspace, "_replace_file", fail_second)

    with pytest.raises(SourceOpError) as exc:
        workspace.patch(
            task_id=task.task_id,
            holder_id="vestigia",
            authority_generation=1,
            iteration_id=iteration_id,
            proposal_id=proposal["proposal_id"],
            proposal_digest=proposal["proposal_digest"],
        )
    assert exc.value.code == "atomic_apply_failed"
    assert (worktree / "hello.txt").read_bytes() == before
    assert not (worktree / "Vesti" / "new_machine.py").exists()
    assert workspace.proposal_store.get(proposal["proposal_id"]).state == "ready"


def test_checkpoint_tracks_files_created_by_task_patch(tmp_path: Path) -> None:
    task, supervisor, workspace, _, worktree, _, iteration_id, proposal = _modify_create_proposal(tmp_path)

    workspace.patch(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        proposal_id=proposal["proposal_id"],
        proposal_digest=proposal["proposal_digest"],
    )
    _, commit, _ = supervisor.checkpoint_iteration(
        task_id=task.task_id,
        holder_id="vestigia",
        authority_generation=1,
        iteration_id=iteration_id,
        outcome="pass",
    )

    assert commit is not None
    assert _git(worktree, "ls-files", "Vesti/new_machine.py") == "Vesti/new_machine.py"
