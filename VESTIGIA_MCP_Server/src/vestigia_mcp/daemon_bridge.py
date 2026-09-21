from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from typing import Any


PROTOCOL = "daemon-bridge-api.v0.1"
QUERY_MAX_CHARS = 32_000
_TOKEN_MAX_BYTES = 4_096


class DaemonBridgeError(RuntimeError):
    pass


class DaemonBridgeClient:
    """Bounded loopback client for one independent Daemon-Bridge process."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        token_path: Path | None,
        deployment_id: str,
        timeout_seconds: int = 120,
        max_response_bytes: int = 262_144,
    ) -> None:
        normalized_host = host.strip().lower()
        if normalized_host == "localhost":
            normalized_host = "127.0.0.1"
        if normalized_host != "127.0.0.1":
            raise ValueError("Daemon-Bridge host must be loopback")
        if port <= 0 or port > 65_535:
            raise ValueError("Daemon-Bridge port must be between 1 and 65535")
        if timeout_seconds <= 0:
            raise ValueError("Daemon-Bridge timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("Daemon-Bridge response ceiling must be positive")

        self.enabled = bool(enabled)
        self.host = normalized_host
        self.port = int(port)
        self.token_path = token_path.expanduser() if token_path is not None else None
        self.deployment_id = deployment_id
        self.timeout_seconds = int(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise DaemonBridgeError(
                "Daemon-Bridge integration is not enabled for this MCP deployment"
            )

    def _read_token(self) -> str:
        if self.token_path is None:
            raise DaemonBridgeError("Daemon-Bridge token path is not configured")
        try:
            if not self.token_path.is_file():
                raise DaemonBridgeError("Daemon-Bridge token file is not available")
            size = self.token_path.stat().st_size
            if size <= 0 or size > _TOKEN_MAX_BYTES:
                raise DaemonBridgeError("Daemon-Bridge token file size is invalid")
            token = self.token_path.read_text(encoding="utf-8").strip()
        except DaemonBridgeError:
            raise
        except OSError as exc:
            raise DaemonBridgeError("Daemon-Bridge token file is not readable") from exc
        if not token:
            raise DaemonBridgeError("Daemon-Bridge token is empty")
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        authenticated: bool,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        if not path.startswith("/") or "://" in path:
            raise DaemonBridgeError("Daemon-Bridge path must be a local absolute HTTP path")

        headers = {
            "Accept": "application/json",
            "Connection": "close",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self._read_token()}"
        if request_id:
            headers["X-Request-ID"] = request_id

        body: bytes | None = None
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))

        connection = HTTPConnection(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
        )
        try:
            connection.request(method.upper(), path, body=body, headers=headers)
            response = connection.getresponse()
            declared_length = response.getheader("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > self.max_response_bytes:
                        raise DaemonBridgeError(
                            "Daemon-Bridge response exceeds configured byte ceiling"
                        )
                except ValueError:
                    pass
            raw = response.read(self.max_response_bytes + 1)
            status = int(response.status)
        except DaemonBridgeError:
            raise
        except (OSError, TimeoutError) as exc:
            raise DaemonBridgeError("Daemon-Bridge loopback request failed") from exc
        finally:
            connection.close()

        if len(raw) > self.max_response_bytes:
            raise DaemonBridgeError(
                "Daemon-Bridge response exceeds configured byte ceiling"
            )

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DaemonBridgeError("Daemon-Bridge returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise DaemonBridgeError("Daemon-Bridge response must be a JSON object")

        if status < 200 or status >= 300:
            error = decoded.get("error")
            message = (
                str(error.get("message") or "").strip()
                if isinstance(error, dict)
                else ""
            )
            suffix = f": {message}" if message else ""
            raise DaemonBridgeError(
                f"Daemon-Bridge returned HTTP {status}{suffix}"
            )

        if request_id and str(decoded.get("request_id") or "") != request_id:
            raise DaemonBridgeError(
                "Daemon-Bridge response request_id did not match the MCP request"
            )
        return decoded

    @staticmethod
    def _require_protocol(response: dict[str, Any]) -> None:
        protocol = str(response.get("protocol") or "")
        if protocol != PROTOCOL:
            raise DaemonBridgeError(
                f"Unsupported Daemon-Bridge protocol: {protocol or '(missing)'}"
            )

    def status(self) -> dict[str, object]:
        result: dict[str, object] = {
            "configured": self.enabled,
            "host": self.host,
            "port": self.port,
            "protocol_expected": PROTOCOL,
            "token_path": str(self.token_path) if self.token_path is not None else None,
            "token_readable": False,
            "available": False,
        }
        if not self.enabled:
            result["reason"] = (
                "VESTIGIA_MCP_DAEMON_BRIDGE_ENABLED is not enabled for this deployment."
            )
            return result

        try:
            self._read_token()
            result["token_readable"] = True
        except DaemonBridgeError as exc:
            result["token_error"] = str(exc)

        try:
            health = self._request(
                "GET",
                "/health",
                authenticated=False,
            )
        except DaemonBridgeError as exc:
            result["error"] = str(exc)
            return result

        result["health"] = health
        result["available"] = bool(health.get("healthy"))
        result["protocol"] = health.get("protocol")
        result["protocol_compatible"] = health.get("protocol") == PROTOCOL
        if not result["protocol_compatible"]:
            result["available"] = False
        return result

    def residents(self, *, request_id: str | None = None) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v1/residents",
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def capabilities(self, *, request_id: str | None = None) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v1/capabilities",
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def query(
        self,
        *,
        resident_id: str,
        content: str,
        model_route: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        resident = resident_id.strip()
        prompt = content.strip()
        route = (model_route or "default_dialogue").strip().lower()

        if not resident or len(resident) > 128:
            raise DaemonBridgeError("resident_id must contain 1 to 128 characters")
        if not prompt:
            raise DaemonBridgeError("Daemon-Bridge query content must not be blank")
        if len(prompt) > QUERY_MAX_CHARS:
            raise DaemonBridgeError(
                f"Daemon-Bridge query content exceeds {QUERY_MAX_CHARS} characters"
            )
        if not route or len(route) > 64:
            raise DaemonBridgeError("model_route must contain 1 to 64 characters")

        caller = f"mcp:{self.deployment_id}"
        response = self._request(
            "POST",
            "/v1/query",
            payload={
                "resident_id": resident,
                "content": prompt,
                "caller_id": caller,
                "user_key": caller,
                "model_route": route,
            },
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)

        expected = {
            "conversation_persistence": "none",
            "channel_context": False,
            "outward_action": False,
            "mode_mutation": False,
            "anchor_mutation": False,
        }
        for key, wanted in expected.items():
            if response.get(key) != wanted:
                raise DaemonBridgeError(
                    f"Daemon-Bridge query contract mismatch for {key}"
                )
        if not isinstance(response.get("answer"), str):
            raise DaemonBridgeError("Daemon-Bridge query response is missing answer text")
        if not str(response.get("bridge_receipt_id") or "").strip():
            raise DaemonBridgeError(
                "Daemon-Bridge query response is missing its Bridge receipt"
            )
        return response
