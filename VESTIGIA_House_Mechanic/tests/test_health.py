from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from house_mechanic.health import probe_service
from house_mechanic.service_model import HealthProbe, Service


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(
            {
                "protocol": "daemon-bridge-api.v0.1",
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


def _service(port: int, protocol: str = "daemon-bridge-api.v0.1") -> Service:
    return Service(
        id="daemon-bridge",
        description="fixture",
        ownership="external",
        start_recipe=None,
        stop_recipe=None,
        health=HealthProbe(
            kind="http",
            host="127.0.0.1",
            port=port,
            path="/health",
            expected_status=200,
            expected_protocol=protocol,
        ),
    )


def test_probe_service_verifies_status_and_protocol() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = probe_service(
            _service(server.server_address[1]),
            request_id="req-health",
        )
        assert result.healthy is True
        assert result.reachable is True
        assert result.observed_status == 200
        assert result.observed_protocol == "daemon-bridge-api.v0.1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_service_fails_protocol_mismatch() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = probe_service(
            _service(server.server_address[1], protocol="wrong.protocol"),
            request_id="req-health",
        )
        assert result.healthy is False
        assert result.reachable is True
        assert result.observed_protocol == "daemon-bridge-api.v0.1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
