from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

from house_mechanic.api import HouseMechanicServer
from house_mechanic.model import load_manifest
from house_mechanic.service_model import load_service_manifest


TOKEN = "house-mechanic-test-token"


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(
            {
                "protocol": "fixture.service.v1",
                "healthy": True,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _write_manifests(tmp_path: Path, health_port: int):
    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "test.ok",
                        "description": "fixture",
                        "argv": [sys.executable, "-c", "print('mechanic-ok')"],
                        "cwd": ".",
                        "timeout_seconds": 5,
                        "env_profile": "python",
                        "expected_exit_codes": [0],
                        "max_stdout_bytes": 4096,
                        "max_stderr_bytes": 4096,
                    },
                    {
                        "id": "service.start",
                        "description": "reserved lifecycle fixture",
                        "argv": [sys.executable, "-c", "print('service-start')"],
                        "cwd": ".",
                        "timeout_seconds": 5,
                        "env_profile": "python",
                        "expected_exit_codes": [0],
                        "max_stdout_bytes": 4096,
                        "max_stderr_bytes": 4096,
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
                        "description": "fixture service",
                        "ownership": "mechanic_child",
                        "start_recipe": "service.start",
                        "health": {
                            "kind": "http",
                            "host": "127.0.0.1",
                            "port": health_port,
                            "path": "/health",
                            "expected_status": 200,
                            "expected_protocol": "fixture.service.v1",
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
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict | None = None,
    request_id: str | None = None,
):
    headers = {"Connection": "close"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8"))
    finally:
        conn.close()


def test_api_persists_recipe_and_health_receipts(tmp_path: Path) -> None:
    health_server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    health_thread = threading.Thread(
        target=health_server.serve_forever,
        daemon=True,
    )
    health_thread.start()

    recipes, services = _write_manifests(
        tmp_path,
        health_server.server_address[1],
    )
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "receipts.jsonl"

    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=recipes,
        services=services,
        token_file=token_file,
        receipt_file=receipt_file,
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        status, health = _request(port, "GET", "/health")
        assert status == 200
        assert health["healthy"] is True
        assert health["receipt_persistence"] is True
        assert health["process_authority"] is False

        status, denied = _request(port, "GET", "/v1/recipes")
        assert status == 401
        assert denied["error"]["code"] == "unauthorized"

        status, recipes_payload = _request(
            port,
            "GET",
            "/v1/recipes",
            token=TOKEN,
            request_id="mcp_req_fixture",
        )
        assert status == 200
        assert recipes_payload["request_id"] == "mcp_req_fixture"
        assert recipes_payload["recipes"][0]["id"] == "test.ok"
        assert "argv" not in recipes_payload["recipes"][0]

        status, process_status = _request(
            port,
            "POST",
            "/v1/process-status",
            token=TOKEN,
            payload={"service_id": "fixture"},
        )
        assert status == 200
        assert process_status["process"]["declared_ownership"] == "mechanic_child"
        assert process_status["process"]["state"] == "not_started"
        assert process_status["process"]["process_owned"] is False
        assert process_status["lifecycle_authority_exposed"] is False

        status, process_logs = _request(
            port,
            "POST",
            "/v1/process-logs",
            token=TOKEN,
            payload={"service_id": "fixture"},
        )
        assert status == 200
        assert process_logs["logs"]["process_owned"] is False
        assert process_logs["logs"]["stdout_tail"] == ""
        assert process_logs["lifecycle_authority_exposed"] is False

        status, reserved = _request(
            port,
            "POST",
            "/v1/run",
            token=TOKEN,
            payload={"recipe_id": "service.start"},
        )
        assert status == 403
        assert reserved["error"]["code"] == "lifecycle_recipe_reserved"

        status, result = _request(
            port,
            "POST",
            "/v1/run",
            token=TOKEN,
            request_id="mcp_req_run_fixture",
            payload={"recipe_id": "test.ok"},
        )
        assert status == 200
        assert result["request_id"] == "mcp_req_run_fixture"
        assert result["execution_occurred"] is True
        assert result["receipt_persisted"] is True
        assert result["receipt"]["request_id"] == "mcp_req_run_fixture"
        assert result["receipt"]["recipe_id"] == "test.ok"
        assert result["receipt"]["stdout"].strip() == "mechanic-ok"
        run_receipt_id = result["durable_receipt_id"]

        status, probe = _request(
            port,
            "POST",
            "/v1/health-check",
            token=TOKEN,
            request_id="mcp_req_health_fixture",
            payload={"service_id": "fixture"},
        )
        assert status == 200
        assert probe["observation_occurred"] is True
        assert probe["receipt_persisted"] is True
        assert probe["health"]["healthy"] is True
        assert probe["health"]["observed_protocol"] == "fixture.service.v1"
        health_receipt_id = probe["durable_receipt_id"]

        status, recent = _request(
            port,
            "GET",
            "/v1/receipts",
            token=TOKEN,
        )
        assert status == 200
        ids = {item["receipt_id"] for item in recent["receipts"]}
        assert run_receipt_id in ids
        assert health_receipt_id in ids

        status, inspected = _request(
            port,
            "POST",
            "/v1/receipt",
            token=TOKEN,
            payload={"receipt_id": run_receipt_id},
        )
        assert status == 200
        evidence = inspected["receipt"]["evidence"]
        assert evidence["recipe_id"] == "test.ok"
        assert evidence["raw_full_output_persisted"] is False
        assert evidence["stdout_excerpt"].strip() == "mechanic-ok"

        status, rejected = _request(
            port,
            "POST",
            "/v1/run",
            token=TOKEN,
            payload={"recipe_id": "test.ok", "argv": ["whoami"]},
        )
        assert status == 400
        assert rejected["error"]["code"] == "invalid_request"

        status, missing = _request(
            port,
            "POST",
            "/v1/run",
            token=TOKEN,
            payload={"recipe_id": "not-there"},
        )
        assert status == 404
        assert missing["error"]["code"] == "unknown_recipe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        health_server.shutdown()
        health_server.server_close()
        health_thread.join(timeout=2)


def test_api_requires_preprovisioned_token(tmp_path: Path) -> None:
    recipes, services = _write_manifests(tmp_path, 1)
    try:
        HouseMechanicServer(
            repo_root=tmp_path,
            recipes=recipes,
            services=services,
            token_file=tmp_path / "missing-token",
            receipt_file=tmp_path / "receipts.jsonl",
            port=0,
        )
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("missing token file must fail closed")
