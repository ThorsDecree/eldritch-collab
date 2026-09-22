from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import sys
import threading

from house_mechanic.api import HouseMechanicServer
from house_mechanic.model import load_manifest
from house_mechanic.service_model import load_service_manifest


TOKEN = "house-mechanic-lifecycle-test-token"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _manifests(tmp_path: Path):
    service_port = _free_port()
    server_code = """
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "protocol": "fixture.lifecycle-api.v1",
            "healthy": True,
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
server.serve_forever()
""".strip()

    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "service.start",
                        "argv": [
                            sys.executable,
                            "-u",
                            "-c",
                            server_code,
                            str(service_port),
                        ],
                        "cwd": ".",
                        "env_profile": "python",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    recipes = load_manifest(recipes_path, tmp_path)

    services_path = tmp_path / "services.json"
    services_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-services.v0.2",
                "services": [
                    {
                        "id": "fixture",
                        "ownership": "mechanic_child",
                        "start_recipe": "service.start",
                        "health": {
                            "kind": "http",
                            "host": "127.0.0.1",
                            "port": service_port,
                            "path": "/health",
                            "expected_status": 200,
                            "expected_protocol": "fixture.lifecycle-api.v1",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    services = load_service_manifest(services_path, recipes)
    return recipes, services


def _request(
    port: int,
    path: str,
    payload: dict,
    *,
    request_id: str,
):
    body = json.dumps(payload).encode("utf-8")
    conn = HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Request-ID": request_id,
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8"))
    finally:
        conn.close()


def test_lifecycle_api_persists_verified_actions(tmp_path: Path) -> None:
    recipes, services = _manifests(tmp_path)
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    receipt_file = tmp_path / "receipts.jsonl"

    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=recipes,
        services=services,
        token_file=token_file,
        receipt_file=receipt_file,
        port=0,
        health_timeout_seconds=0.5,
        lifecycle_health_wait_seconds=5.0,
        lifecycle_stop_timeout_seconds=2.0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        status, started = _request(
            port,
            "/v1/process-start",
            {"service_id": "fixture"},
            request_id="req-api-start",
        )
        assert status == 200
        assert started["action_occurred"] is True
        assert started["verified"] is True
        assert started["receipt_persisted"] is True
        first_generation = started["lifecycle"]["generation_id"]
        assert first_generation

        status, duplicate = _request(
            port,
            "/v1/process-start",
            {"service_id": "fixture"},
            request_id="req-api-duplicate",
        )
        assert status == 409
        assert duplicate["error"]["code"] == "already_running"

        status, wrong_stop = _request(
            port,
            "/v1/process-stop",
            {
                "service_id": "fixture",
                "generation_id": "hm_proc_wrong_generation",
            },
            request_id="req-api-wrong-stop",
        )
        assert status == 409
        assert wrong_stop["error"]["code"] == "generation_mismatch"

        status, restarted = _request(
            port,
            "/v1/process-restart",
            {
                "service_id": "fixture",
                "generation_id": first_generation,
            },
            request_id="req-api-restart",
        )
        assert status == 200
        assert restarted["verified"] is True
        second_generation = restarted["lifecycle"]["new_generation_id"]
        assert second_generation
        assert second_generation != first_generation

        status, stopped = _request(
            port,
            "/v1/process-stop",
            {
                "service_id": "fixture",
                "generation_id": second_generation,
            },
            request_id="req-api-stop",
        )
        assert status == 200
        assert stopped["verified"] is True
        assert stopped["lifecycle"]["after"]["state"] == "exited"

        records = server.receipts.recent(limit=20, kind="service_lifecycle")
        actions = [item["evidence"]["action"] for item in records]
        assert "start" in actions
        assert "restart" in actions
        assert "stop" in actions
        assert all(item["evidence"]["verified"] is True for item in records)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
