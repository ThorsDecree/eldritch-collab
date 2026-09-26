from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcp import Client

from vestigia_mcp.config import Settings
from vestigia_mcp.house_mechanic import DevActionFilter
from vestigia_mcp.server import create_server


TOKEN = "phase5-projection-token"
PROTOCOL = "vestigia.house-mechanic-api.v0.9"


class _MechanicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def fixture(self):
        return self.server  # type: ignore[return-value]

    def _request_id(self) -> str:
        return self.headers.get("X-Request-ID") or "hm-local"

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self) -> None:
        request_id = self._request_id()
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
        if not self._authorized():
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
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "operations": self.fixture.operations,
                },
            )
            return
        if self.path == "/v1/receipts":
            self.fixture.hits.append(("GET", self.path, None, request_id))
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "receipts": [
                        {"receipt_id": "r3", "request_id": "req-3"},
                        {"receipt_id": "r2", "request_id": "req-2"},
                        {"receipt_id": "r1", "request_id": "req-1"},
                    ],
                },
            )
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
        request_id = self._request_id()
        if not self._authorized():
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
        self.fixture.hits.append(("POST", self.path, payload, request_id))

        if self.path == "/v1/task-acquire":
            if payload.get("purpose") == "reject":
                self._send(
                    409,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "error": {
                            "code": "stale_authority",
                            "message": "fixture rejection",
                        },
                    },
                )
                return
            response_id = (
                "wrong-request-id"
                if self.fixture.mismatch_request_id
                else request_id
            )
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": response_id,
                    "action_occurred": True,
                    "receipt_persisted": False,
                    "task": {"task_id": "hm_task_fixture"},
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": "mutation happened but receipt write failed",
                    },
                },
            )
            return
        if self.path == "/v1/process-status":
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "process": {
                        "service_id": payload["service_id"],
                        "state": "running",
                        "process_owned": True,
                        "generation_id": "hm_proc_" + "a" * 32,
                    },
                    "lifecycle_authority_exposed": True,
                },
            )
            return
        if self.path == "/v1/health-check":
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "observation_occurred": True,
                    "receipt_persisted": True,
                    "health": {
                        "service_id": payload["service_id"],
                        "healthy": True,
                    },
                },
            )
            return
        if self.path == "/v1/process-logs":
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "logs": {
                        "service_id": payload["service_id"],
                        "stdout_tail": "12345678",
                        "stderr_tail": "ABCDEFGH",
                        "process_owned": True,
                    },
                },
            )
            return
        if self.path == "/v1/receipt":
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "receipt": {
                        "receipt_id": payload["receipt_id"],
                        "kind": "fixture",
                    },
                },
            )
            return
        self._send(
            404,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "error": {"code": "not_found", "message": "missing"},
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_mechanic() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MechanicHandler)
    server.operations = {  # type: ignore[attr-defined]
        "task.acquire": {
            "effect": "worktree_mutation",
            "mutation": True,
            "method": "POST",
            "path": "/v1/task-acquire",
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
            "effect": "worktree_mutation",
            "mutation": True,
            "method": "POST",
            "path": "/v1/task-acquire?surprise=yes",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
    }
    server.hits = []  # type: ignore[attr-defined]
    server.mismatch_request_id = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _settings(
    tmp_path: Path,
    mechanic: ThreadingHTTPServer,
    *,
    action_filter: DevActionFilter | None = None,
) -> Settings:
    token = tmp_path / "house-mechanic-token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    return Settings(
        live_archive_root=None,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="phase5-test",
        house_mechanic_enabled=True,
        house_mechanic_host="127.0.0.1",
        house_mechanic_port=mechanic.server_address[1],
        house_mechanic_token_path=token,
        house_mechanic_timeout_seconds=3,
        house_mechanic_max_response_bytes=65_536,
        dev_actions=action_filter
        or DevActionFilter(mode="wildcard", actions=()),
    )


def test_dev_surface_projects_one_mutation_tool_and_preserves_receipt_join(
    tmp_path: Path,
) -> None:
    mechanic, thread = _start_mechanic()
    server = create_server(_settings(tmp_path, mechanic))

    async def exercise() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert {
                "dev.capabilities",
                "dev.call",
                "dev.process",
                "dev.logs",
            } <= set(tools)
            assert tools["dev.call"].annotations is not None
            assert tools["dev.call"].annotations.read_only_hint is False
            for name in ("dev.capabilities", "dev.process", "dev.logs"):
                assert tools[name].annotations is not None
                assert tools[name].annotations.read_only_hint is True

            caps = await client.call_tool("dev.capabilities", {})
            assert caps.is_error is False
            assert caps.structured_content is not None
            projected = caps.structured_content
            assert projected["configured"] is True
            assert projected["available"] is True
            assert projected["action_filter"]["mode"] == "wildcard"
            assert set(projected["projected_mutations"]) == {"task.acquire"}
            assert (
                projected["projected_mutations"]["task.acquire"]["input_schema"]["type"]
                == "object"
            )
            assert (
                projected["rejections"]["service.process_status"]
                == "not_mutation"
            )
            assert projected["rejections"]["unsafe.route"] == "unsafe_route"

            called = await client.call_tool(
                "dev.call",
                {
                    "action": "task.acquire",
                    "arguments": {"purpose": "repair"},
                },
            )
            assert called.is_error is False
            assert called.structured_content is not None
            body = called.structured_content
            request_id = body["request_id"]
            assert request_id.startswith("mcp_req_")
            assert body["action"] == "task.acquire"
            assert body["projection"] == {
                "allowed": True,
                "allowlist_mode": "wildcard",
            }
            assert body["result"]["action_occurred"] is True
            assert body["result"]["receipt_persisted"] is False
            assert mechanic.hits[-1] == (  # type: ignore[attr-defined]
                "POST",
                "/v1/task-acquire",
                {"purpose": "repair"},
                request_id,
            )

            audit = await client.call_tool(
                "receipts.recent",
                {
                    "capability": "dev.call",
                    "request_id": request_id,
                },
            )
            assert audit.is_error is False
            assert audit.structured_content is not None
            assert audit.structured_content["matched_total"] == 1

    try:
        asyncio.run(exercise())
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)


def test_dev_call_refuses_reads_denied_actions_unsafe_routes_and_http_errors(
    tmp_path: Path,
) -> None:
    mechanic, thread = _start_mechanic()

    async def exercise() -> None:
        server = create_server(_settings(tmp_path, mechanic))
        async with Client(server) as client:
            before = len(mechanic.hits)  # type: ignore[attr-defined]
            read = await client.call_tool(
                "dev.call",
                {
                    "action": "service.process_status",
                    "arguments": {"service_id": "fixture"},
                },
            )
            assert read.is_error is True
            assert "not a projected mutation" in str(read.content)
            assert len(mechanic.hits) == before  # type: ignore[attr-defined]

            unsafe = await client.call_tool(
                "dev.call",
                {"action": "unsafe.route", "arguments": {}},
            )
            assert unsafe.is_error is True
            assert "unsafe_route" in str(unsafe.content)
            assert len(mechanic.hits) == before  # type: ignore[attr-defined]

            rejected = await client.call_tool(
                "dev.call",
                {
                    "action": "task.acquire",
                    "arguments": {"purpose": "reject"},
                },
            )
            assert rejected.is_error is True
            serialized = str(rejected.content)
            assert "HTTP 409" in serialized
            assert "stale_authority" in serialized
            assert "fixture rejection" in serialized

        exact_server = create_server(
            _settings(
                tmp_path,
                mechanic,
                action_filter=DevActionFilter(
                    mode="exact",
                    actions=("deployment.candidate",),
                ),
            )
        )
        async with Client(exact_server) as client:
            before = len(mechanic.hits)  # type: ignore[attr-defined]
            denied = await client.call_tool(
                "dev.call",
                {
                    "action": "task.acquire",
                    "arguments": {"purpose": "blocked"},
                },
            )
            assert denied.is_error is True
            assert "not allowed" in str(denied.content)
            assert len(mechanic.hits) == before  # type: ignore[attr-defined]

    try:
        asyncio.run(exercise())
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)


def test_dev_process_and_logs_are_read_only_and_bounded(tmp_path: Path) -> None:
    mechanic, thread = _start_mechanic()
    server = create_server(_settings(tmp_path, mechanic))

    async def exercise() -> None:
        async with Client(server) as client:
            process = await client.call_tool(
                "dev.process",
                {"service_id": "fixture", "include_health": True},
            )
            assert process.is_error is False
            assert process.structured_content is not None
            assert process.structured_content["process"]["process"]["state"] == "running"
            assert process.structured_content["health"]["health"]["healthy"] is True

            logs = await client.call_tool(
                "dev.logs",
                {
                    "source": "process",
                    "service_id": "fixture",
                    "tail_bytes": 5,
                },
            )
            assert logs.is_error is False
            assert logs.structured_content is not None
            assert logs.structured_content["logs"]["stdout_tail"] == "45678"
            assert logs.structured_content["logs"]["stderr_tail"] == "DEFGH"

            recent = await client.call_tool(
                "dev.logs",
                {"source": "receipts", "limit": 1},
            )
            assert recent.is_error is False
            assert recent.structured_content is not None
            assert recent.structured_content["receipts"] == [
                {"receipt_id": "r3", "request_id": "req-3"}
            ]

            exact = await client.call_tool(
                "dev.logs",
                {
                    "source": "receipts",
                    "receipt_id": "r2",
                },
            )
            assert exact.is_error is False
            assert exact.structured_content is not None
            assert exact.structured_content["receipt"]["receipt_id"] == "r2"

            bad_tail = await client.call_tool(
                "dev.logs",
                {
                    "source": "process",
                    "service_id": "fixture",
                    "tail_bytes": 16385,
                },
            )
            assert bad_tail.is_error is True

    try:
        asyncio.run(exercise())
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)


def test_dev_call_rejects_house_mechanic_request_id_mismatch(tmp_path: Path) -> None:
    mechanic, thread = _start_mechanic()
    mechanic.mismatch_request_id = True  # type: ignore[attr-defined]
    server = create_server(_settings(tmp_path, mechanic))

    async def exercise() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "dev.call",
                {
                    "action": "task.acquire",
                    "arguments": {"purpose": "repair"},
                },
            )
            assert result.is_error is True
            assert "request_id" in str(result.content)

    try:
        asyncio.run(exercise())
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)
