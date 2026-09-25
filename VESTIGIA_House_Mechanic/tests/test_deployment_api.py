from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

from house_mechanic.api import HouseMechanicServer, PROTOCOL
from house_mechanic.model import load_manifest
from house_mechanic.service_model import load_service_manifest
from house_mechanic.tasking import load_repository_manifest


TOKEN = "house-mechanic-deployment-api-token"


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


SERVICE = """from http.server import BaseHTTPRequestHandler, HTTPServer
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


def _request(
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
):
    body = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Connection": "close",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn = HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def _server(tmp_path: Path):
    service_port = _free_port()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "service.py").write_text(SERVICE, encoding="utf-8")
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
                        "argv": [sys.executable, "-u", "service.py", str(service_port)],
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
                            "port": service_port,
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

    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "receipts.jsonl"
    server = HouseMechanicServer(
        repo_root=repo,
        recipes=recipes,
        services=services,
        token_file=token_file,
        receipt_file=receipt_file,
        repositories=repositories,
        worktree_root=tmp_path / "worktrees",
        port=0,
        health_timeout_seconds=0.25,
        lifecycle_health_wait_seconds=3.0,
        lifecycle_stop_timeout_seconds=2.0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, receipt_file


def test_authenticated_deployment_api_promotes_verified_candidate(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    port = server.server_address[1]
    try:
        status, caps = _request(port, "GET", "/v1/capabilities")
        assert status == 200
        assert caps["protocol"] == PROTOCOL
        assert caps["operations"]["deployment.candidate"]["enabled"] is True

        status, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "deployment api",
            },
        )
        assert status == 200
        task = acquired["task"]
        worktree = Path(task["worktree_path"])

        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task["task_id"],
                "holder_id": "liora",
                "authority_generation": 1,
            },
        )
        assert status == 200
        (worktree / "marker.txt").write_text("candidate\n", encoding="utf-8")
        status, checkpoint = _request(
            port,
            "POST",
            "/v1/iteration-checkpoint",
            {
                "task_id": task["task_id"],
                "holder_id": "liora",
                "authority_generation": 1,
                "iteration_id": begun["task"]["current_iteration_id"],
                "outcome": "pass",
            },
        )
        assert status == 200
        candidate_commit = checkpoint["checkpoint_commit"]
        assert candidate_commit

        status, deployed = _request(
            port,
            "POST",
            "/v1/deploy-candidate",
            {
                "service_id": "fixture",
                "task_id": task["task_id"],
                "holder_id": "liora",
                "authority_generation": 1,
            },
        )
        assert status == 200
        assert deployed["receipt_persisted"] is True
        assert deployed["verified"] is True
        generation_id = deployed["deployment"]["after"]["active_generation_id"]
        assert generation_id

        status, promoted = _request(
            port,
            "POST",
            "/v1/deploy-promote",
            {
                "service_id": "fixture",
                "generation_id": generation_id,
            },
        )
        assert status == 200
        assert promoted["verified"] is True
        assert (
            promoted["deployment"]["after"]["last_known_good_commit"]
            == candidate_commit
        )

        status, listed = _request(port, "GET", "/v1/deployments")
        assert status == 200
        row = listed["deployments"][0]
        assert row["deployment"]["last_known_good_commit"] == candidate_commit
        assert row["process"]["generation_id"] == generation_id

        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        deployment_ops = [
            row["evidence"]["operation"]
            for row in rows
            if row["kind"] == "dev_deployment"
        ]
        assert "deploy_candidate" in deployment_ops
        assert "promote" in deployment_ops
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
