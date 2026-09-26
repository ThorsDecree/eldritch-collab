from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys

from house_mechanic.deployment import DeploymentController, DeploymentLedger
from house_mechanic.lifecycle import LifecycleController
from house_mechanic.model import load_manifest
from house_mechanic.processes import ProcessRegistry
from house_mechanic.service_model import load_service_manifest
from house_mechanic.tasking import TaskLedger, TaskSupervisor, WorktreeManager, load_repository_manifest


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


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


GOOD_SERVICE = """from http.server import BaseHTTPRequestHandler, HTTPServer
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        return

HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


BAD_SERVICE = """raise SystemExit(2)
"""


def _fixture(tmp_path: Path):
    port = _free_port()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "service.py").write_text(GOOD_SERVICE, encoding="utf-8")
    (repo / "marker.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")

    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "service.start",
                        "argv": [sys.executable, "-u", "service.py", str(port)],
                        "cwd": ".",
                        "env_profile": "python",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    recipes = load_manifest(recipes_path, repo)

    services_path = tmp_path / "services.json"
    services_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-services.v0.3",
                "services": [
                    {
                        "id": "fixture",
                        "ownership": "mechanic_child",
                        "start_recipe": "service.start",
                        "health": {
                            "kind": "http",
                            "host": "127.0.0.1",
                            "port": port,
                            "path": "/",
                            "expected_status": 200,
                        },
                        "deployment": {"repository_id": "fixture"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    services = load_service_manifest(services_path, recipes)
    service = services.services["fixture"]

    repositories_path = tmp_path / "repositories.json"
    repositories_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-repositories.v0.1",
                "repositories": [
                    {
                        "id": "fixture",
                        "path": ".",
                        "default_base_ref": "main",
                        "allowed_base_refs": ["main"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repositories = load_repository_manifest(repositories_path, repo)
    worktrees = WorktreeManager(
        repo_root=repo,
        repositories=repositories,
        worktree_root=tmp_path / "worktrees",
    )
    tasks = TaskSupervisor(
        ledger=TaskLedger(tmp_path / "task-state"),
        worktrees=worktrees,
    )
    processes = ProcessRegistry(tmp_path / "process-state")
    lifecycle = LifecycleController(
        repo_root=repo,
        recipes=recipes,
        processes=processes,
        health_probe_timeout_seconds=0.25,
        health_wait_seconds=3.0,
        health_poll_seconds=0.05,
        stop_timeout_seconds=2.0,
    )
    deployments = DeploymentController(
        tasks=tasks,
        lifecycle=lifecycle,
        ledger=DeploymentLedger(tmp_path / "deployment-state"),
    )
    return repo, service, tasks, processes, deployments


def _checkpoint(
    tasks: TaskSupervisor,
    task_id: str,
    *,
    holder: str,
    generation: int,
    mutate,
) -> str:
    opened, _ = tasks.begin_iteration(
        task_id=task_id,
        holder_id=holder,
        authority_generation=generation,
    )
    worktree = Path(opened.worktree_path)
    mutate(worktree)
    closed, commit, _ = tasks.checkpoint_iteration(
        task_id=task_id,
        holder_id=holder,
        authority_generation=generation,
        iteration_id=opened.current_iteration_id or "",
        outcome="pass",
    )
    assert closed.state == "active"
    assert commit
    return commit


def test_candidate_promote_then_unhealthy_candidate_auto_rolls_back(tmp_path: Path) -> None:
    _repo, service, tasks, processes, deployments = _fixture(tmp_path)
    try:
        first = tasks.acquire(
            repository_id="fixture",
            holder_id="liora",
            purpose="good candidate",
        )
        good_commit = _checkpoint(
            tasks,
            first.task_id,
            holder="liora",
            generation=1,
            mutate=lambda worktree: (worktree / "marker.txt").write_text(
                "candidate one\n",
                encoding="utf-8",
            ),
        )

        deployed = deployments.deploy_candidate(
            service,
            request_id="req-good",
            task_id=first.task_id,
            holder_id="liora",
            authority_generation=1,
        )
        assert deployed["verified"] is True
        assert deployed["outcome"] == "candidate_running_healthy"
        good_generation = deployed["after"]["active_generation_id"]
        assert good_generation

        promoted = deployments.promote(
            service,
            request_id="req-promote",
            generation_id=good_generation,
        )
        assert promoted["verified"] is True
        assert promoted["after"]["last_known_good_commit"] == good_commit
        assert promoted["after"]["state"] == "last_known_good_running"

        second = tasks.acquire(
            repository_id="fixture",
            holder_id="anima",
            purpose="bad candidate",
        )
        bad_commit = _checkpoint(
            tasks,
            second.task_id,
            holder="anima",
            generation=1,
            mutate=lambda worktree: (worktree / "service.py").write_text(
                BAD_SERVICE,
                encoding="utf-8",
            ),
        )
        assert bad_commit != good_commit

        failed = deployments.deploy_candidate(
            service,
            request_id="req-bad",
            task_id=second.task_id,
            holder_id="anima",
            authority_generation=1,
            expected_generation_id=good_generation,
        )
        assert failed["verified"] is False
        assert failed["outcome"] == "candidate_unhealthy_rolled_back"
        assert failed["rollback"]["verified"] is True
        after = failed["after"]
        assert after["state"] == "last_known_good_running"
        assert after["active_kind"] == "last_known_good"
        assert after["active_commit"] == good_commit
        assert after["last_known_good_commit"] == good_commit
        assert after["active_generation_id"]
        assert after["active_generation_id"] != good_generation
        assert processes.status(service).state == "running"
    finally:
        processes.terminate_all_for_shutdown()


def test_deployment_ledger_suspends_active_generation_after_restart(tmp_path: Path) -> None:
    _repo, service, tasks, processes, deployments = _fixture(tmp_path)
    try:
        task = tasks.acquire(
            repository_id="fixture",
            holder_id="liora",
            purpose="restart suspension",
        )
        _checkpoint(
            tasks,
            task.task_id,
            holder="liora",
            generation=1,
            mutate=lambda worktree: (worktree / "marker.txt").write_text(
                "restart\n",
                encoding="utf-8",
            ),
        )
        deployed = deployments.deploy_candidate(
            service,
            request_id="req-restart",
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=1,
        )
        assert deployed["verified"] is True

        restarted = DeploymentLedger(tmp_path / "deployment-state")
        record = restarted.get(service.id)
        assert record is not None
        assert record.state == "suspended_unverified"
        assert record.last_outcome == "supervisor_restart_lost_process_authority"
    finally:
        processes.terminate_all_for_shutdown()


def test_restart_reconciliation_reports_health_without_adopting_process(tmp_path: Path) -> None:
    repo, service, tasks, processes, deployments = _fixture(tmp_path)
    restarted_processes = ProcessRegistry(tmp_path / "process-state-restarted")
    try:
        task = tasks.acquire(
            repository_id="fixture",
            holder_id="liora",
            purpose="reconcile restart",
        )
        _checkpoint(
            tasks,
            task.task_id,
            holder="liora",
            generation=1,
            mutate=lambda worktree: (worktree / "marker.txt").write_text(
                "reconcile\n",
                encoding="utf-8",
            ),
        )
        deployed = deployments.deploy_candidate(
            service,
            request_id="req-reconcile-deploy",
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=1,
        )
        assert deployed["verified"] is True
        active_path = Path(deployed["after"]["active_worktree_path"])

        restarted_lifecycle = LifecycleController(
            repo_root=repo,
            recipes=deployments.lifecycle.recipes,
            processes=restarted_processes,
            health_probe_timeout_seconds=0.25,
            health_wait_seconds=3.0,
            health_poll_seconds=0.05,
            stop_timeout_seconds=2.0,
        )
        restarted = DeploymentController(
            tasks=tasks,
            lifecycle=restarted_lifecycle,
            ledger=DeploymentLedger(tmp_path / "deployment-state"),
        )

        observed = restarted.reconciliation(
            service,
            request_id="req-reconcile-observe",
        )
        assert observed["verified"] is False
        assert observed["outcome"] == "operator_boundary_required"
        assert observed["operator_boundary_required"] is True
        assert observed["automatic_process_adoption"] is False
        assert observed["automatic_active_checkout_cleanup"] is False
        assert observed["process"]["state"] == "not_started"
        assert observed["health"]["healthy"] is True
        assert observed["deployment"]["state"] == "suspended_unverified"
        assert observed["deployment"]["suspended_from_state"] == "candidate_running"
        assert observed["deployment"]["suspended_at"]
        assert observed["active_checkout"]["path_present"] is True
        assert observed["active_checkout"]["commit_matches"] is True
        assert active_path.exists()
    finally:
        restarted_processes.terminate_all_for_shutdown()
        processes.terminate_all_for_shutdown()


def test_dirty_retired_checkout_is_queued_then_safely_retried(tmp_path: Path) -> None:
    _repo, service, tasks, processes, deployments = _fixture(tmp_path)
    try:
        task = tasks.acquire(
            repository_id="fixture",
            holder_id="liora",
            purpose="cleanup retry",
        )
        good_commit = _checkpoint(
            tasks,
            task.task_id,
            holder="liora",
            generation=1,
            mutate=lambda worktree: (worktree / "marker.txt").write_text(
                "good\n",
                encoding="utf-8",
            ),
        )
        deployed = deployments.deploy_candidate(
            service,
            request_id="req-cleanup-deploy",
            task_id=task.task_id,
            holder_id="liora",
            authority_generation=1,
        )
        generation = deployed["after"]["active_generation_id"]
        assert generation
        promoted = deployments.promote(
            service,
            request_id="req-cleanup-promote",
            generation_id=generation,
        )
        old_worktree = Path(promoted["after"]["active_worktree_path"])
        (old_worktree / "marker.txt").write_text("dirty after launch\n", encoding="utf-8")

        rolled = deployments.rollback(
            service,
            request_id="req-cleanup-rollback",
            expected_generation_id=generation,
        )
        assert rolled["verified"] is True
        assert rolled["cleanup_current"]["removed"] is False
        after = rolled["after"]
        assert len(after["cleanup_pending"]) == 1
        pending = after["cleanup_pending"][0]
        assert pending["worktree_path"] == str(old_worktree)
        assert pending["expected_commit"] == good_commit
        assert pending["safe_basis"] == "verified_stop"
        assert old_worktree.exists()

        _git(old_worktree, "reset", "--hard", good_commit)
        retried = deployments.retry_cleanup(
            service,
            request_id="req-cleanup-retry",
        )
        assert retried["verified"] is True
        assert retried["outcome"] == "cleanup_complete"
        assert retried["removed_count"] == 1
        assert retried["after"]["cleanup_pending"] == []
        assert not old_worktree.exists()
        assert Path(retried["after"]["active_worktree_path"]).exists()
    finally:
        processes.terminate_all_for_shutdown()
