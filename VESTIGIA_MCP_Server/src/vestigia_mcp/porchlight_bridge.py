from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


AuditCallback = Callable[
    [dict[str, object], str, str | None, str | None],
    None,
]


def _share_audit_arguments(payload: dict[str, Any]) -> dict[str, object]:
    content = payload.get("content")
    content_text = content if isinstance(content, str) else None
    return {
        "url": payload.get("url") if isinstance(payload.get("url"), str) else None,
        "title": payload.get("title") if isinstance(payload.get("title"), str) else None,
        "mode": payload.get("mode") if isinstance(payload.get("mode"), str) else None,
        "captured_at": (
            payload.get("captured_at")
            if isinstance(payload.get("captured_at"), str)
            else None
        ),
        "previous_snapshot_sha256": (
            payload.get("previous_snapshot_sha256")
            if isinstance(payload.get("previous_snapshot_sha256"), str)
            else None
        ),
        "screenshot_included": payload.get("screenshot_base64") is not None,
        "content_sha256": (
            hashlib.sha256(content_text.encode("utf-8")).hexdigest()
            if content_text is not None
            else None
        ),
        "content_bytes": (
            len(content_text.encode("utf-8")) if content_text is not None else None
        ),
    }


from .adapters.archive import ArchiveError, ArchiveSource
from .archive_mutation import ArchiveMutationStore
from .audit import AuditLedger
from .config import Settings
from .policy import PolicyEngine
from .porchlight_share import PorchlightShareRequest, PorchlightShareService
from .receipt_garden import ReceiptGarden


class PairingTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self.rotate()

    def read(self) -> str:
        with self._lock:
            try:
                token = self.path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError("Porchlight pairing token is unavailable") from exc
            if not token:
                raise RuntimeError("Porchlight pairing token is empty")
            return token

    def rotate(self) -> str:
        token = secrets.token_urlsafe(32)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        with self._lock:
            try:
                temporary.write_text(token + "\n", encoding="utf-8")
                try:
                    temporary.chmod(0o600)
                except OSError:
                    pass
                temporary.replace(self.path)
                try:
                    self.path.chmod(0o600)
                except OSError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        return token


class _BridgeHandler(BaseHTTPRequestHandler):
    server: "_BridgeHTTPServer"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path != "/health":
            self._error(404, "not_found", "Unknown Porchlight bridge route")
            return
        self._send(200, {"protocol": "porchlight-bridge.v1", "healthy": True})

    def do_OPTIONS(self) -> None:
        if self.path not in {"/v1/pair/verify", "/v1/shares"}:
            self._error(404, "not_found", "Unknown Porchlight bridge route")
            return
        if self.headers.get("Origin") != self.server.extension_origin:
            self._error(403, "origin_denied", "Extension origin is not paired")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.server.extension_origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self) -> None:
        request_id = f"bridge_req_{uuid.uuid4().hex}"
        if self.path not in {"/v1/pair/verify", "/v1/shares"}:
            self._error(404, "not_found", "Unknown Porchlight bridge route", request_id)
            return
        if self.headers.get("Origin") != self.server.extension_origin:
            self._error(403, "origin_denied", "Extension origin is not paired", request_id)
            return
        if not self._authorized():
            self._error(401, "unauthorized", "Porchlight bridge authentication failed", request_id)
            return
        try:
            payload = self._read_json()
            if self.path == "/v1/pair/verify":
                self._send(
                    200,
                    {
                        "paired": True,
                        "protocol": "porchlight-bridge.v1",
                        "origin": self.server.extension_origin,
                    },
                    request_id,
                )
                return
            result = self.server.share(payload, request_id=request_id)
            self._send(200, result, request_id)
        except _BridgeHTTPError as exc:
            self._error(exc.status, exc.code, exc.message, request_id)
        except ArchiveError as exc:
            message = str(exc)
            conflict = any(
                marker in message.lower()
                for marker in ("expected_base_sha256", "conflict", "changed after")
            )
            self._error(
                409 if conflict else 400,
                "conflict" if conflict else "invalid_request",
                "Porchlight share could not be accepted",
                request_id,
            )
        except (OSError, RuntimeError) as exc:
            self.server.log_exception(exc)
            self._error(503, "bridge_unavailable", "Porchlight bridge service unavailable", request_id)
        except Exception as exc:
            self.server.log_exception(exc)
            self._error(503, "bridge_failure", "Porchlight bridge service failed", request_id)

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return False
        try:
            expected = self.server.tokens.read()
        except RuntimeError:
            return False
        return hmac.compare_digest(token, expected)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise _BridgeHTTPError(400, "length_required", "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise _BridgeHTTPError(400, "invalid_length", "Content-Length is invalid") from exc
        if length < 0:
            raise _BridgeHTTPError(400, "invalid_length", "Content-Length is invalid")
        if length > self.server.max_body_bytes:
            raise _BridgeHTTPError(413, "body_too_large", "Request body exceeds the bridge limit")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise _BridgeHTTPError(400, "incomplete_body", "Request body is incomplete")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _BridgeHTTPError(400, "invalid_json", "Request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise _BridgeHTTPError(400, "invalid_json", "Request body must be a JSON object")
        return value

    def _send(self, status: int, value: dict[str, object], request_id: str | None = None) -> None:
        body = json.dumps(
            {**value, **({"request_id": request_id} if request_id else {})},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.headers.get("Origin") == self.server.extension_origin:
            self.send_header("Access-Control-Allow-Origin", self.server.extension_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _error(
        self,
        status: int,
        code: str,
        message: str,
        request_id: str | None = None,
    ) -> None:
        self._send(
            status,
            {"error": {"code": code, "message": message}},
            request_id,
        )

    def log_message(self, format: str, *args: object) -> None:
        return


class _BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _BridgeHTTPError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class PorchlightBridgeServer:
    def __init__(
        self,
        service: PorchlightShareService,
        tokens: PairingTokenStore,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        extension_origin: str = "chrome-extension://porchlight",
        max_body_bytes: int = 1_200_000,
        audit: AuditCallback | None = None,
    ) -> None:
        normalized_host = host.strip().lower()
        if normalized_host == "localhost":
            normalized_host = "127.0.0.1"
        if normalized_host != "127.0.0.1":
            raise ValueError("Porchlight bridge must bind to loopback")
        if port < 0 or port > 65535:
            raise ValueError("Porchlight bridge port is invalid")
        if not extension_origin.strip():
            raise ValueError("Porchlight bridge extension origin is required")
        if max_body_bytes <= 0:
            raise ValueError("Porchlight bridge body limit must be positive")
        self.service = service
        self.tokens = tokens
        self.extension_origin = extension_origin.strip()
        self.max_body_bytes = max_body_bytes
        self.audit = audit
        self._httpd = _BridgeHTTPServer((normalized_host, port), _BridgeHandler)
        self._httpd.bridge = self
        self._httpd.extension_origin = self.extension_origin
        self._httpd.max_body_bytes = self.max_body_bytes
        self._httpd.tokens = self.tokens
        self._httpd.share = self.share
        self._httpd.log_exception = self.log_exception

    @property
    def server_address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()

    def server_close(self) -> None:
        self._httpd.server_close()

    def share(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, object]:
        arguments = _share_audit_arguments(payload)
        try:
            required = ("url", "title", "content", "mode")
            if any(not isinstance(payload.get(key), str) for key in required):
                raise _BridgeHTTPError(
                    400, "invalid_request", "Porchlight share fields are invalid"
                )
            screenshot = None
            encoded = payload.get("screenshot_base64")
            if encoded is not None:
                if not isinstance(encoded, str):
                    raise _BridgeHTTPError(
                        400, "invalid_request", "Screenshot payload is invalid"
                    )
                try:
                    screenshot = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise _BridgeHTTPError(
                        400, "invalid_request", "Screenshot payload is invalid"
                    ) from exc
            request = PorchlightShareRequest(
                url=payload["url"],
                title=payload["title"],
                content=payload["content"],
                mode=payload["mode"],
                captured_at=payload.get("captured_at"),
                previous_snapshot_sha256=payload.get("previous_snapshot_sha256"),
                screenshot_png=screenshot,
            )
            result = self.service.share(request)
        except Exception as exc:
            self._record_audit(
                arguments,
                "error",
                request_id,
                type(exc).__name__,
            )
            raise
        self._record_audit(arguments, "ok", request_id)
        return result

    def _record_audit(
        self,
        arguments: dict[str, object],
        outcome: str,
        request_id: str | None,
        detail: str | None = None,
    ) -> None:
        if self.audit is not None:
            self.audit(arguments, outcome, request_id, detail)

    def log_exception(self, exc: Exception) -> None:
        # Deliberately do not log request data or service exception text.
        return


def create_porchlight_bridge(settings: Settings) -> PorchlightBridgeServer:
    mutations = ArchiveMutationStore(
        settings.live_archive_root,
        settings.state_dir,
        settings.deployment_id,
        write_prefixes=settings.archive_write_prefixes,
        max_bytes=settings.archive_write_max_bytes,
    )

    def reader(path: str) -> str | None:
        if settings.live_archive_root is None:
            return None
        source = ArchiveSource(settings.live_archive_root)
        if source.entry(path) is None:
            return None
        return source.read_text(path, settings.archive_write_max_bytes)

    service = PorchlightShareService(
        mutations,
        reader,
        screenshot_max_bytes=settings.porchlight_screenshot_max_bytes,
    )
    ledger = AuditLedger(settings.state_dir, settings.deployment_id)
    receipt_garden = ReceiptGarden(settings.state_dir, settings.deployment_id)
    share_capability = PolicyEngine().require_allowed("archive.share_porchlight")

    def record_share_audit(
        arguments: dict[str, object],
        outcome: str,
        request_id: str | None,
        detail: str | None = None,
    ) -> None:
        event = ledger.record(
            share_capability,
            arguments,
            outcome,
            request_id=request_id,
            authority="porchlight_bridge",
            detail=detail,
        )
        receipt_garden.record_audit_event(event)

    return PorchlightBridgeServer(
        service,
        PairingTokenStore(settings.porchlight_bridge_token_path or settings.state_dir / "porchlight-token"),
        host=settings.porchlight_bridge_host,
        port=settings.porchlight_bridge_port,
        extension_origin=settings.porchlight_bridge_extension_origin,
        max_body_bytes=settings.porchlight_bridge_max_body_bytes,
        audit=record_share_audit,
    )
