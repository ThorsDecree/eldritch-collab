from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading

from house_mechanic.api import HouseMechanicServer
from house_mechanic.model import load_manifest
from house_mechanic.service_model import load_service_manifest


TOKEN = "house-mechanic-test-token"


def _write_manifests(tmp_path: Path):
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
                "schema_version": "vestigia.house-mechanic-services.v0.1",
                "services": [
                    {
                        "id": "fixture",
                        "description": "fixture service",
                        "start_recipe": "test.ok",
                        "health": {
                            "kind": "http",
                            "host": "127.0.0.1",
                            "port": 8766,
                            "path": "/health",
                            "expected_status": 200,
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


def test_api_is_authenticated_bounded_and_recipe_only(tmp_path: Path) -> None:
    recipes, services = _write_manifests(tmp_path)
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")

    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=recipes,
        services=services,
        token_file=token_file,
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        status, health = _request(port, "GET", "/health")
        assert status == 200
        assert health["healthy"] is True
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

        status, services_payload = _request(
            port,
            "GET",
            "/v1/services",
            token=TOKEN,
        )
        assert status == 200
        assert services_payload["services"][0]["id"] == "fixture"
        assert services_payload["services"][0]["process_authority"] is False

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
        assert result["receipt"]["request_id"] == "mcp_req_run_fixture"
        assert result["receipt"]["recipe_id"] == "test.ok"
        assert result["receipt"]["stdout"].strip() == "mechanic-ok"

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


def test_api_requires_preprovisioned_token(tmp_path: Path) -> None:
    recipes, services = _write_manifests(tmp_path)
    try:
        HouseMechanicServer(
            repo_root=tmp_path,
            recipes=recipes,
            services=services,
            token_file=tmp_path / "missing-token",
            port=0,
        )
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("missing token file must fail closed")
