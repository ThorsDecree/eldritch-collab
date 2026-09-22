from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from typing import Any
import uuid

from .model import Manifest
from .runner import run_recipe
from .service_model import ServiceManifest


PROTOCOL = "vestigia.house-mechanic-api.v0.1"
MAX_REQUEST_BYTES = 16_384
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class HouseMechanicAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def read_token(path: Path) -> str:
    wanted = path.expanduser()
    if not wanted.is_file():
        raise ValueError("House Mechanic token file does not exist")
    size = wanted.stat().st_size
    if size <= 0 or size > 4096:
        raise ValueError("House Mechanic token file size is invalid")
    token = wanted.read_text(encoding="utf-8").strip()
    if len(token) < 16:
        raise ValueError("House Mechanic token must contain at least 16 characters")
    return token


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def api(self) -> "HouseMechanicServer":
        return self.server.api  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        request_id = self._request_id()
        try:
            if self.path == "/health":
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "healthy": True,
                        "recipe_count": len(self.api.recipes.recipes),
                        "service_count": len(self.api.services.services),
                        "process_authority": False,
                        "request_id": request_id,
                    },
                )
                return
            self._require_auth()
            if self.path == "/v1/capabilities":
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "operations": {
                            "recipe.list": {
                                "effect": "read",
                                "caller_supplies_argv": False,
                            },
                            "recipe.run": {
                                "effect": "bounded_local_process",
                                "caller_supplies_argv": False,
                                "caller_supplies_cwd": False,
                                "caller_supplies_env": False,
                                "max_parallel": self.api.max_parallel,
                            },
                            "service.list": {
                                "effect": "read",
                                "process_authority": False,
                            },
                        },
                    },
                )
                return
            if self.path == "/v1/recipes":
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "recipes": [
                            {
                                "id": recipe.id,
                                "description": recipe.description,
                                "sha256": recipe.digest(),
                            }
                            for recipe in self.api.recipes.recipes.values()
                        ],
                    },
                )
                return
            if self.path == "/v1/services":
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "services": [
                            service.public_dict()
                            for service in self.api.services.services.values()
                        ],
                    },
                )
                return
            raise HouseMechanicAPIError(404, "not_found", "route not found")
        except HouseMechanicAPIError as exc:
            self._error(exc, request_id)
        except Exception:
            self._error(
                HouseMechanicAPIError(500, "internal_error", "request failed"),
                request_id,
            )

    def do_POST(self) -> None:
        request_id = self._request_id()
        try:
            self._require_auth()
            if self.path != "/v1/run":
                raise HouseMechanicAPIError(404, "not_found", "route not found")
            payload = self._json_body()
            unknown = set(payload) - {"recipe_id"}
            if unknown:
                raise HouseMechanicAPIError(
                    400,
                    "invalid_request",
                    f"unknown fields: {sorted(unknown)}",
                )
            recipe_id = payload.get("recipe_id")
            if not isinstance(recipe_id, str) or not recipe_id.strip():
                raise HouseMechanicAPIError(
                    400,
                    "invalid_request",
                    "recipe_id must be a non-empty string",
                )
            recipe = self.api.recipes.recipes.get(recipe_id.strip())
            if recipe is None:
                raise HouseMechanicAPIError(
                    404,
                    "unknown_recipe",
                    "recipe is not present in the operator manifest",
                )
            if not self.api.run_slots.acquire(blocking=False):
                raise HouseMechanicAPIError(
                    409,
                    "busy",
                    "House Mechanic has no free recipe execution slot",
                )
            try:
                receipt = run_recipe(
                    recipe,
                    self.api.repo_root,
                    request_id=request_id,
                )
            finally:
                self.api.run_slots.release()
            self._send(
                200,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "receipt": receipt.to_dict(),
                },
            )
        except HouseMechanicAPIError as exc:
            self._error(exc, request_id)
        except Exception:
            self._error(
                HouseMechanicAPIError(500, "internal_error", "request failed"),
                request_id,
            )

    def _request_id(self) -> str:
        supplied = self.headers.get("X-Request-ID", "").strip()
        if supplied and _REQUEST_ID.fullmatch(supplied):
            return supplied
        return f"hm_req_{uuid.uuid4().hex}"

    def _require_auth(self) -> None:
        raw = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not raw.startswith(prefix):
            raise HouseMechanicAPIError(401, "unauthorized", "bearer token required")
        supplied = raw[len(prefix):].strip()
        if not hmac.compare_digest(supplied, self.api.token):
            raise HouseMechanicAPIError(401, "unauthorized", "bearer token rejected")

    def _json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise HouseMechanicAPIError(
                411,
                "length_required",
                "Content-Length is required",
            )
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "invalid Content-Length",
            ) from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise HouseMechanicAPIError(
                413,
                "request_too_large",
                "request body exceeds the configured ceiling",
            )
        raw = self.rfile.read(length)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HouseMechanicAPIError(
                400,
                "invalid_json",
                "request body must be one UTF-8 JSON object",
            ) from exc
        if not isinstance(decoded, dict):
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "request body must be a JSON object",
            )
        return decoded

    def _error(self, exc: HouseMechanicAPIError, request_id: str) -> None:
        self._send(
            exc.status,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            },
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class HouseMechanicServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        *,
        repo_root: Path,
        recipes: Manifest,
        services: ServiceManifest,
        token_file: Path,
        port: int = 8770,
        max_parallel: int = 1,
    ):
        if not 0 <= int(port) <= 65535:
            raise ValueError("House Mechanic port must be between 0 and 65535")
        if not 1 <= int(max_parallel) <= 16:
            raise ValueError("House Mechanic max_parallel must be between 1 and 16")
        self.repo_root = repo_root.resolve()
        self.recipes = recipes
        self.services = services
        self.token = read_token(token_file)
        self.max_parallel = int(max_parallel)
        self.run_slots = threading.BoundedSemaphore(self.max_parallel)
        super().__init__(("127.0.0.1", int(port)), _Handler)
        self.api = self
