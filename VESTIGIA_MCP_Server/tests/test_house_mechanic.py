from __future__ import annotations

import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest


TOKEN = "house-mechanic-client-test-token"
PROTOCOL = "vestigia.house-mechanic-api.v0.9"


class _FakeMechanicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def fixture(self):
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        request_id = self.headers.get("X-Request-ID") or "hm-local"
        if self.path == "/health":
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "healthy": True,
                    "request_id": request_id,
                },
            )
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(
                401,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "error": {"code": "unauthorized", "message": "nope"},
                },
            )
            return
        if self.path == "/v1/capabilities":
            if self.fixture.invalid_json:
                raw = b"{not-json"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            payload = {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "operations": self.fixture.operations,
            }
            if self.fixture.large_response:
                payload["padding"] = "x" * 10_000
            self._send(200, payload)
            return
        self._send(
            404,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "error": {"code": "not_found", "message": "missing"},
            },
        )

    def do_POST(self) -> None:
        request_id = self.headers.get("X-Request-ID") or "hm-local"
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(
                401,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "error": {"code": "unauthorized", "message": "nope"},
                },
            )
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        payload = json.loads(raw.decode("utf-8"))
        self.fixture.mutation_hits += 1
        self.fixture.last_payload = payload
        self.fixture.last_request_id = request_id
        if self.path != "/v1/do":
            self._send(
                404,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "error": {"code": "not_found", "message": "missing"},
                },
            )
            return
        response_id = "wrong-request-id" if self.fixture.mismatch_request_id else request_id
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": response_id,
                "action_occurred": True,
                "receipt_persisted": True,
                "echo": payload,
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_fake_mechanic() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMechanicHandler)
    server.operations = {  # type: ignore[attr-defined]
        "task.acquire": {
            "effect": "worktree_mutation",
            "mutation": True,
            "method": "POST",
            "path": "/v1/do",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"purpose": {"type": "string"}},
                "required": ["purpose"],
            },
        },
        "service.process_status": {
            "effect": "read",
            "mutation": False,
            "method": "POST",
            "path": "/v1/process-status",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        },
        "unsafe.route": {
            "effect": "mutation",
            "mutation": True,
            "method": "POST",
            "path": "/v1/do?target=elsewhere",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
    }
    server.mutation_hits = 0  # type: ignore[attr-defined]
    server.last_payload = None  # type: ignore[attr-defined]
    server.last_request_id = None  # type: ignore[attr-defined]
    server.mismatch_request_id = False  # type: ignore[attr-defined]
    server.invalid_json = False  # type: ignore[attr-defined]
    server.large_response = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _load_contract():
    spec = importlib.util.find_spec("vestigia_mcp.house_mechanic")
    assert spec is not None, "Phase 5 House Mechanic MCP client module is missing"
    from vestigia_mcp.house_mechanic import (  # noqa: PLC0415
        DevActionFilter,
        HouseMechanicClient,
        HouseMechanicClientError,
    )

    return DevActionFilter, HouseMechanicClient, HouseMechanicClientError


def _client(tmp_path: Path, server, action_filter, *, max_response_bytes: int = 65_536):
    _, HouseMechanicClient, _ = _load_contract()
    token = tmp_path / "house-mechanic-token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    return HouseMechanicClient(
        enabled=True,
        host="127.0.0.1",
        port=server.server_address[1],
        token_path=token,
        timeout_seconds=2,
        max_response_bytes=max_response_bytes,
        action_filter=action_filter,
    )


def test_client_projects_only_safe_allowed_mutations(tmp_path: Path) -> None:
    DevActionFilter, _, _ = _load_contract()
    server, thread = _start_fake_mechanic()
    try:
        client = _client(
            tmp_path,
            server,
            DevActionFilter(mode="wildcard", actions=()),
        )
        response = client.capabilities(request_id="mcp_req_caps")
        projected = client.projected_mutations(response)
        assert set(projected) == {"task.acquire"}
        assert projected["task.acquire"]["input_schema"]["type"] == "object"

        exact = _client(
            tmp_path,
            server,
            DevActionFilter(mode="exact", actions=("service.start",)),
        )
        assert exact.projected_mutations(response) == {}

        denied = _client(
            tmp_path,
            server,
            DevActionFilter(mode="deny_all", actions=()),
        )
        assert denied.projected_mutations(response) == {}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_call_forwards_arguments_and_denies_before_dispatch(tmp_path: Path) -> None:
    DevActionFilter, _, HouseMechanicClientError = _load_contract()
    server, thread = _start_fake_mechanic()
    try:
        client = _client(
            tmp_path,
            server,
            DevActionFilter(mode="wildcard", actions=()),
        )
        result = client.call(
            "task.acquire",
            {"purpose": "repair"},
            request_id="mcp_req_mutation",
        )
        assert result["request_id"] == "mcp_req_mutation"
        assert server.last_payload == {"purpose": "repair"}  # type: ignore[attr-defined]
        assert server.last_request_id == "mcp_req_mutation"  # type: ignore[attr-defined]
        assert server.mutation_hits == 1  # type: ignore[attr-defined]

        exact = _client(
            tmp_path,
            server,
            DevActionFilter(mode="exact", actions=("deployment.candidate",)),
        )
        with pytest.raises(HouseMechanicClientError, match="not allowed"):
            exact.call(
                "task.acquire",
                {"purpose": "blocked"},
                request_id="mcp_req_blocked",
            )
        assert server.mutation_hits == 1  # type: ignore[attr-defined]

        with pytest.raises(HouseMechanicClientError, match="not a projected mutation"):
            client.call(
                "service.process_status",
                {"service_id": "fixture"},
                request_id="mcp_req_read",
            )
        assert server.mutation_hits == 1  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_client_rejects_request_id_mismatch_invalid_json_and_large_response(
    tmp_path: Path,
) -> None:
    DevActionFilter, _, HouseMechanicClientError = _load_contract()
    server, thread = _start_fake_mechanic()
    try:
        client = _client(
            tmp_path,
            server,
            DevActionFilter(mode="wildcard", actions=()),
        )
        server.mismatch_request_id = True  # type: ignore[attr-defined]
        with pytest.raises(HouseMechanicClientError, match="request_id"):
            client.call(
                "task.acquire",
                {"purpose": "repair"},
                request_id="mcp_req_wrong",
            )

        server.mismatch_request_id = False  # type: ignore[attr-defined]
        server.invalid_json = True  # type: ignore[attr-defined]
        with pytest.raises(HouseMechanicClientError, match="invalid JSON"):
            client.capabilities(request_id="mcp_req_invalid")

        server.invalid_json = False  # type: ignore[attr-defined]
        server.large_response = True  # type: ignore[attr-defined]
        tiny = _client(
            tmp_path,
            server,
            DevActionFilter(mode="wildcard", actions=()),
            max_response_bytes=512,
        )
        with pytest.raises(HouseMechanicClientError, match="byte ceiling"):
            tiny.capabilities(request_id="mcp_req_large")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_client_is_loopback_only_and_token_is_bounded(tmp_path: Path) -> None:
    DevActionFilter, HouseMechanicClient, HouseMechanicClientError = _load_contract()
    with pytest.raises(ValueError, match="loopback"):
        HouseMechanicClient(
            enabled=True,
            host="0.0.0.0",
            port=8770,
            token_path=tmp_path / "token",
            timeout_seconds=2,
            max_response_bytes=1024,
            action_filter=DevActionFilter(mode="wildcard", actions=()),
        )

    server, thread = _start_fake_mechanic()
    try:
        token = tmp_path / "oversized-token"
        token.write_text("x" * 5000, encoding="utf-8")
        client = HouseMechanicClient(
            enabled=True,
            host="127.0.0.1",
            port=server.server_address[1],
            token_path=token,
            timeout_seconds=2,
            max_response_bytes=4096,
            action_filter=DevActionFilter(mode="wildcard", actions=()),
        )
        with pytest.raises(HouseMechanicClientError, match="token file size"):
            client.capabilities(request_id="mcp_req_token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_client_reports_transport_failure(tmp_path: Path) -> None:
    DevActionFilter, HouseMechanicClient, HouseMechanicClientError = _load_contract()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    client = HouseMechanicClient(
        enabled=True,
        host="127.0.0.1",
        port=port,
        token_path=token,
        timeout_seconds=1,
        max_response_bytes=4096,
        action_filter=DevActionFilter(mode="wildcard", actions=()),
    )
    with pytest.raises(HouseMechanicClientError, match="loopback request failed"):
        client.capabilities(request_id="mcp_req_transport")
