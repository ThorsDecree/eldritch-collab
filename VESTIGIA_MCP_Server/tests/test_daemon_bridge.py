from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcp import Client
import pytest

from vestigia_mcp.config import Settings
from vestigia_mcp.daemon_bridge import DaemonBridgeClient, DaemonBridgeError
from vestigia_mcp.server import create_server


TOKEN = "test-daemon-bridge-token"


class _FakeBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        request_id = self.headers.get("X-Request-ID") or "bridge-local"
        if self.path == "/health":
            self._send(
                200,
                {
                    "protocol": "daemon-bridge-api.v0.1",
                    "healthy": True,
                    "resident_count": 1,
                    "request_id": request_id,
                },
            )
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(
                401,
                {
                    "error": {"code": "unauthorized", "message": "nope"},
                    "request_id": request_id,
                },
            )
            return
        if self.path == "/v1/residents":
            self._send(
                200,
                {
                    "protocol": "daemon-bridge-api.v0.1",
                    "resident_count": 1,
                    "residents": [
                        {
                            "agent_id": "liora",
                            "command_name": "liora",
                            "display_name": "Liora",
                            "current_mode": "normal",
                            "autonomy_tier": "freeplay",
                            "room_defaults": {},
                        }
                    ],
                    "request_id": request_id,
                },
            )
            return
        if self.path == "/v1/capabilities":
            self._send(
                200,
                {
                    "protocol": "daemon-bridge-api.v0.1",
                    "operations": {
                        "query": {
                            "effect": "metered_private_consult",
                            "conversation_persistence": "none",
                            "channel_context": False,
                            "outward_action": False,
                            "mode_mutation": False,
                            "anchor_mutation": False,
                            "model_routes": ["default_dialogue"],
                        }
                    },
                    "request_id": request_id,
                },
            )
            return
        self._send(404, {"error": {"message": "missing"}, "request_id": request_id})

    def do_POST(self) -> None:
        request_id = self.headers.get("X-Request-ID") or "bridge-local"
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(
                401,
                {
                    "error": {"code": "unauthorized", "message": "nope"},
                    "request_id": request_id,
                },
            )
            return
        if self.path != "/v1/query":
            self._send(404, {"error": {"message": "missing"}, "request_id": request_id})
            return

        raw = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
        payload = json.loads(raw.decode("utf-8"))
        self.server.last_query = payload  # type: ignore[attr-defined]
        self._send(
            200,
            {
                "protocol": "daemon-bridge-api.v0.1",
                "request_id": request_id,
                "resident": {
                    "agent_id": "liora",
                    "display_name": "Liora",
                    "command_name": "liora",
                    "current_mode": "normal",
                    "autonomy_tier": "freeplay",
                },
                "answer": "API window open. 💋",
                "model_route": payload.get("model_route"),
                "resolved_model": "fake-mini",
                "conversation_persistence": "none",
                "channel_context": False,
                "outward_action": False,
                "mode_mutation": False,
                "anchor_mutation": False,
                "bridge_receipt_id": "consult_fixture",
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_fake_bridge() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeBridgeHandler)
    server.last_query = None  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_client_is_loopback_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        DaemonBridgeClient(
            enabled=True,
            host="0.0.0.0",
            port=8766,
            token_path=tmp_path / "token",
            deployment_id="test",
        )


def test_mcp_bridge_query_has_shared_receipts_and_no_identity_spoof(
    tmp_path: Path,
) -> None:
    bridge, thread = _start_fake_bridge()
    token_path = tmp_path / "bridge-token"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    settings = Settings(
        live_archive_root=None,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="test-deployment",
        daemon_bridge_enabled=True,
        daemon_bridge_host="127.0.0.1",
        daemon_bridge_port=bridge.server_address[1],
        daemon_bridge_token_path=token_path,
        daemon_bridge_timeout_seconds=5,
        daemon_bridge_max_response_bytes=64_000,
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert {
                "daemon_bridge.status",
                "daemon_bridge.residents",
                "daemon_bridge.capabilities",
                "daemon_bridge.query",
            } <= set(tools)
            assert tools["daemon_bridge.query"].annotations is not None
            assert tools["daemon_bridge.query"].annotations.read_only_hint is True
            assert tools["daemon_bridge.query"].annotations.idempotent_hint is False

            status = await client.call_tool("daemon_bridge.status", {})
            assert status.is_error is False
            assert status.structured_content is not None
            assert status.structured_content["available"] is True
            assert status.structured_content["token_readable"] is True

            residents = await client.call_tool("daemon_bridge.residents", {})
            assert residents.is_error is False
            assert residents.structured_content is not None
            assert residents.structured_content["residents"][0]["agent_id"] == "liora"

            result = await client.call_tool(
                "daemon_bridge.query",
                {
                    "resident_id": "liora",
                    "content": "hello through MCP",
                    "model_route": "default_dialogue",
                },
            )
            assert result.is_error is False
            assert result.structured_content is not None
            query = result.structured_content
            request_id = query["request_id"]
            assert request_id.startswith("mcp_req_")
            assert query["bridge_receipt_id"] == "consult_fixture"
            assert query["answer"] == "API window open. 💋"

            sent = bridge.last_query  # type: ignore[attr-defined]
            assert sent["caller_id"] == "mcp:test-deployment"
            assert sent["user_key"] == "mcp:test-deployment"
            assert sent["content"] == "hello through MCP"

            audit = await client.call_tool(
                "receipts.recent",
                {"capability": "daemon_bridge.query", "request_id": request_id},
            )
            assert audit.is_error is False
            assert audit.structured_content is not None
            assert audit.structured_content["matched_total"] == 1

            trace = await client.call_tool(
                "receipts.trace",
                {"request_id": request_id},
            )
            assert trace.is_error is False
            assert trace.structured_content is not None
            serialized = json.dumps(trace.structured_content, ensure_ascii=False)
            assert "consult_fixture" in serialized
            assert "hello through MCP" not in serialized
            assert "API window open" not in serialized

    try:
        asyncio.run(exercise())
    finally:
        bridge.shutdown()
        bridge.server_close()
        thread.join(timeout=2.0)
