from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import threading

from house_mechanic.api import HouseMechanicServer, PROTOCOL
from house_mechanic.model import Manifest
from house_mechanic.service_model import ServiceManifest
from house_mechanic.tasking import load_repository_manifest


TOKEN = "house-mechanic-task-api-token"


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


def _request(port: int, method: str, path: str, payload: dict | None = None):
    body = None
    headers = {"Authorization": f"Bearer {TOKEN}", "Connection": "close"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def _server(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "hello.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "initial")

    repositories_path = tmp_path / "repositories.json"
    repositories_path.write_text(
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
    repositories = load_repository_manifest(repositories_path, tmp_path)
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "receipts.jsonl"

    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=Manifest({}),
        services=ServiceManifest({}),
        token_file=token,
        receipt_file=receipt_file,
        repositories=repositories,
        worktree_root=tmp_path / "worktrees",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, receipt_file


def test_authenticated_task_api_acquire_iterate_checkpoint_and_receipt(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    port = server.server_address[1]
    try:
        status, caps = _request(port, "GET", "/v1/capabilities")
        assert status == 200
        assert caps["protocol"] == PROTOCOL
        assert caps["operations"]["task.acquire"]["enabled"] is True
        assert caps["operations"]["task.acquire"]["caller_supplies_worktree_path"] is False

        status, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "repair the thing",
                "iteration_limit": 2,
            },
        )
        assert status == 200
        assert acquired["receipt_persisted"] is True
        task = acquired["task"]
        task_id = task["task_id"]
        assert task["authority_generation"] == 1
        assert task["state"] == "active"
        worktree = Path(task["worktree_path"])
        assert worktree.is_dir()

        status, listed = _request(port, "GET", "/v1/tasks")
        assert status == 200
        assert [row["task_id"] for row in listed["tasks"]] == [task_id]

        status, stale = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 2,
            },
        )
        assert status == 409
        assert stale["error"]["code"] == "stale_authority"

        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
            },
        )
        assert status == 200
        iteration_id = begun["task"]["current_iteration_id"]
        assert iteration_id

        (worktree / "hello.txt").write_text("changed\n", encoding="utf-8")

        status, checkpoint = _request(
            port,
            "POST",
            "/v1/iteration-checkpoint",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "outcome": "fail",
            },
        )
        assert status == 200
        assert checkpoint["receipt_persisted"] is True
        assert checkpoint["checkpoint_commit"]
        assert checkpoint["git"]["dirty"] is False

        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kinds = [row["kind"] for row in rows]
        assert "dev_task_transition" in kinds
        assert "dev_iteration_checkpoint" in kinds
        checkpoint_rows = [row for row in rows if row["kind"] == "dev_iteration_checkpoint"]
        assert checkpoint_rows[-1]["evidence"]["outcome"] == "fail"
        assert checkpoint_rows[-1]["evidence"]["checkpoint_commit"] == checkpoint["checkpoint_commit"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_api_handoff_requires_acceptance(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    port = server.server_address[1]
    try:
        _, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "handoff",
            },
        )
        task = acquired["task"]

        status, offered = _request(
            port,
            "POST",
            "/v1/handoff-offer",
            {
                "task_id": task["task_id"],
                "holder_id": "liora",
                "authority_generation": 1,
                "recipient_id": "vestigia",
            },
        )
        assert status == 200
        assert offered["task"]["holder_id"] == "liora"
        assert offered["task"]["state"] == "handoff_pending"

        status, accepted = _request(
            port,
            "POST",
            "/v1/handoff-respond",
            {
                "task_id": task["task_id"],
                "recipient_id": "vestigia",
                "accept": True,
            },
        )
        assert status == 200
        assert accepted["task"]["holder_id"] == "vestigia"
        assert accepted["task"]["authority_generation"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_routes_fail_closed_when_not_configured(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=Manifest({}),
        services=ServiceManifest({}),
        token_file=token,
        receipt_file=tmp_path / "receipts.jsonl",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, response = _request(server.server_address[1], "GET", "/v1/tasks")
        assert status == 503
        assert response["error"]["code"] == "tasking_not_configured"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
