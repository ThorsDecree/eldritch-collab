from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from vestigia_mcp.porchlight_bridge import PairingTokenStore, PorchlightBridgeServer


ORIGIN = "chrome-extension://porchlight-test"


class RecorderService:
    def __init__(self) -> None:
        self.calls = []

    def share(self, request):
        self.calls.append(request)
        return {
            "shared_directly": True,
            "canonical_changed": True,
            "capture_id": "capture-1",
        }


def make_bridge(tmp_path: Path, *, max_body_bytes: int = 10_000):
    service = RecorderService()
    tokens = PairingTokenStore(tmp_path / "porchlight-token")
    bridge = PorchlightBridgeServer(
        service,
        tokens,
        host="127.0.0.1",
        port=0,
        extension_origin=ORIGIN,
        max_body_bytes=max_body_bytes,
    )
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    return bridge, thread, service, tokens


def call(bridge, token: str, path: str, body=None, *, origin: str = ORIGIN):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{bridge.server_address[1]}{path}",
        data=data,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": origin,
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def close_bridge(bridge, thread) -> None:
    bridge.shutdown()
    bridge.server_close()
    thread.join(timeout=2)


def test_bridge_health_pairing_and_valid_share(tmp_path: Path) -> None:
    bridge, thread, service, tokens = make_bridge(tmp_path)
    try:
        status, health = call(bridge, tokens.read(), "/health")
        assert status == 200
        assert health["protocol"] == "porchlight-bridge.v1"

        status, pairing = call(bridge, tokens.read(), "/v1/pair/verify", {})
        assert status == 200
        assert pairing["paired"] is True

        status, result = call(
            bridge,
            tokens.read(),
            "/v1/shares",
            {
                "url": "https://example.test",
                "title": "Example",
                "content": "body",
                "mode": "page",
            },
        )
        assert status == 200
        assert result["capture_id"] == "capture-1"
        assert len(service.calls) == 1
    finally:
        close_bridge(bridge, thread)


def test_bridge_rejects_bad_auth_and_origin_before_service(tmp_path: Path) -> None:
    bridge, thread, service, tokens = make_bridge(tmp_path)
    try:
        payload = {"url": "https://example.test", "content": "secret", "mode": "page"}
        status, _ = call(bridge, "wrong-token", "/v1/shares", payload)
        assert status == 401
        status, _ = call(bridge, tokens.read(), "/v1/shares", payload, origin="https://evil.test")
        assert status == 403
        assert service.calls == []
    finally:
        close_bridge(bridge, thread)


def test_bridge_rejects_malformed_and_oversized_json(tmp_path: Path) -> None:
    bridge, thread, service, tokens = make_bridge(tmp_path, max_body_bytes=64)
    try:
        request = Request(
            f"http://127.0.0.1:{bridge.server_address[1]}/v1/shares",
            data=b"{not-json",
            method="POST",
            headers={
                "Authorization": f"Bearer {tokens.read()}",
                "Origin": ORIGIN,
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(HTTPError) as malformed:
            urlopen(request, timeout=2)
        assert malformed.value.code == 400

        status, _ = call(
            bridge,
            tokens.read(),
            "/v1/shares",
            {"content": "x" * 200},
        )
        assert status == 413
        assert service.calls == []
    finally:
        close_bridge(bridge, thread)


def test_bridge_token_rotation_invalidates_old_token(tmp_path: Path) -> None:
    bridge, thread, _, tokens = make_bridge(tmp_path)
    try:
        old = tokens.read()
        new = tokens.rotate()
        assert new != old
        assert call(bridge, old, "/v1/pair/verify", {})[0] == 401
        assert call(bridge, new, "/v1/pair/verify", {})[0] == 200
    finally:
        close_bridge(bridge, thread)


def test_bridge_rejects_non_loopback_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        PorchlightBridgeServer(
            RecorderService(),
            PairingTokenStore(tmp_path / "token"),
            host="0.0.0.0",
            port=0,
            extension_origin=ORIGIN,
        )


def test_bridge_answers_extension_cors_preflight(tmp_path: Path) -> None:
    bridge, thread, _, _ = make_bridge(tmp_path)
    try:
        request = Request(
            f"http://127.0.0.1:{bridge.server_address[1]}/v1/shares",
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert "POST" in response.headers["Access-Control-Allow-Methods"]
            assert "authorization" in response.headers["Access-Control-Allow-Headers"].lower()
    finally:
        close_bridge(bridge, thread)
