from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection
import json
from pathlib import Path
from typing import Any, Literal


PROTOCOL = "vestigia.house-mechanic-api.v0.9"
_TOKEN_MAX_BYTES = 4_096
_SUPPORTED_METHODS = {"GET", "POST"}


class HouseMechanicClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DevActionFilter:
    mode: Literal["wildcard", "deny_all", "exact"]
    actions: tuple[str, ...]

    def allows(self, action: str) -> bool:
        if self.mode == "wildcard":
            return True
        if self.mode == "deny_all":
            return False
        return action in self.actions


def parse_dev_actions(raw: str | None) -> DevActionFilter:
    if raw is None:
        return DevActionFilter(mode="wildcard", actions=())
    stripped = raw.strip()
    if stripped == "*":
        return DevActionFilter(mode="wildcard", actions=())
    if not stripped:
        return DevActionFilter(mode="deny_all", actions=())
    actions = tuple(
        sorted(
            {
                item.strip()
                for item in raw.split(",")
                if item.strip()
            }
        )
    )
    if "*" in actions:
        raise ValueError(
            "VESTIGIA_MCP_DEV_ACTIONS must be '*' alone or exact action names"
        )
    if not actions:
        return DevActionFilter(mode="deny_all", actions=())
    return DevActionFilter(mode="exact", actions=actions)


class HouseMechanicClient:
    """Bounded loopback projection of one House Mechanic supervisor."""

    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        token_path: Path | None,
        timeout_seconds: int,
        max_response_bytes: int,
        action_filter: DevActionFilter,
    ) -> None:
        normalized_host = host.strip().lower()
        if normalized_host == "localhost":
            normalized_host = "127.0.0.1"
        if normalized_host != "127.0.0.1":
            raise ValueError("House Mechanic host must be loopback")
        if port <= 0 or port > 65_535:
            raise ValueError("House Mechanic port must be between 1 and 65535")
        if timeout_seconds <= 0:
            raise ValueError("House Mechanic timeout must be positive")
        if max_response_bytes <= 0:
            raise ValueError("House Mechanic response ceiling must be positive")
        self.enabled = bool(enabled)
        self.host = normalized_host
        self.port = int(port)
        self.token_path = token_path.expanduser() if token_path is not None else None
        self.timeout_seconds = int(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.action_filter = action_filter

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise HouseMechanicClientError(
                "House Mechanic integration is not enabled for this MCP deployment"
            )

    def _read_token(self) -> str:
        if self.token_path is None:
            raise HouseMechanicClientError(
                "House Mechanic token path is not configured"
            )
        try:
            if not self.token_path.is_file():
                raise HouseMechanicClientError(
                    "House Mechanic token file is not available"
                )
            size = self.token_path.stat().st_size
            if size <= 0 or size > _TOKEN_MAX_BYTES:
                raise HouseMechanicClientError(
                    "House Mechanic token file size is invalid"
                )
            token = self.token_path.read_text(encoding="utf-8").strip()
        except HouseMechanicClientError:
            raise
        except OSError as exc:
            raise HouseMechanicClientError(
                "House Mechanic token file is not readable"
            ) from exc
        if not token:
            raise HouseMechanicClientError("House Mechanic token is empty")
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
        if not self._safe_path(path):
            raise HouseMechanicClientError(
                "House Mechanic path is outside the bounded local API contract"
            )
        verb = method.upper()
        if verb not in _SUPPORTED_METHODS:
            raise HouseMechanicClientError(
                "House Mechanic method is outside the bounded API contract"
            )

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
            connection.request(verb, path, body=body, headers=headers)
            response = connection.getresponse()
            declared_length = response.getheader("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > self.max_response_bytes:
                        raise HouseMechanicClientError(
                            "House Mechanic response exceeds configured byte ceiling"
                        )
                except ValueError:
                    pass
            raw = response.read(self.max_response_bytes + 1)
            status = int(response.status)
        except HouseMechanicClientError:
            raise
        except (OSError, TimeoutError) as exc:
            raise HouseMechanicClientError(
                "House Mechanic loopback request failed"
            ) from exc
        finally:
            connection.close()

        if len(raw) > self.max_response_bytes:
            raise HouseMechanicClientError(
                "House Mechanic response exceeds configured byte ceiling"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HouseMechanicClientError(
                "House Mechanic returned invalid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise HouseMechanicClientError(
                "House Mechanic response must be a JSON object"
            )

        if status < 200 or status >= 300:
            error = decoded.get("error")
            code = (
                str(error.get("code") or "").strip()
                if isinstance(error, dict)
                else ""
            ) or None
            message = (
                str(error.get("message") or "").strip()
                if isinstance(error, dict)
                else ""
            )
            suffix = f": {message}" if message else ""
            raise HouseMechanicClientError(
                f"House Mechanic returned HTTP {status}{suffix}",
                status=status,
                code=code,
            )

        if request_id and str(decoded.get("request_id") or "") != request_id:
            raise HouseMechanicClientError(
                "House Mechanic response request_id did not match the MCP request"
            )
        return decoded

    @staticmethod
    def _require_protocol(response: dict[str, Any]) -> None:
        protocol = str(response.get("protocol") or "")
        if protocol != PROTOCOL:
            raise HouseMechanicClientError(
                f"Unsupported House Mechanic protocol: {protocol or '(missing)'}"
            )

    @staticmethod
    def _safe_path(path: object) -> bool:
        if not isinstance(path, str):
            return False
        if not path.startswith("/v1/") and path != "/health":
            return False
        if any(token in path for token in ("://", "?", "#", "\\", "%")):
            return False
        return ".." not in path.split("/")

    @classmethod
    def _projectable_mutation(
        cls,
        metadata: object,
    ) -> tuple[bool, str | None]:
        if not isinstance(metadata, dict):
            return False, "metadata_not_object"
        if metadata.get("mutation") is not True:
            return False, "not_mutation"
        if metadata.get("enabled") is False:
            return False, "disabled"
        method = metadata.get("method")
        if method != "POST":
            return False, "unsupported_mutation_method"
        if not cls._safe_path(metadata.get("path")):
            return False, "unsafe_route"
        schema = metadata.get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return False, "invalid_input_schema"
        if schema.get("additionalProperties") is not False:
            return False, "input_schema_not_closed"
        if not isinstance(schema.get("properties"), dict):
            return False, "invalid_input_schema"
        if not isinstance(schema.get("required"), list):
            return False, "invalid_input_schema"
        return True, None

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
                "VESTIGIA_MCP_HOUSE_MECHANIC_ENABLED is not enabled for this deployment."
            )
            return result
        try:
            self._read_token()
            result["token_readable"] = True
        except HouseMechanicClientError as exc:
            result["token_error"] = str(exc)
        try:
            health = self._request(
                "GET",
                "/health",
                authenticated=False,
            )
        except HouseMechanicClientError as exc:
            result["error"] = str(exc)
            return result
        result["health"] = health
        result["protocol"] = health.get("protocol")
        result["protocol_compatible"] = health.get("protocol") == PROTOCOL
        result["available"] = bool(
            health.get("healthy") and result["protocol_compatible"]
        )
        return result

    def capabilities(self, *, request_id: str | None = None) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v1/capabilities",
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        operations = response.get("operations")
        if not isinstance(operations, dict):
            raise HouseMechanicClientError(
                "House Mechanic capabilities response is missing operations"
            )
        return response

    def projected_mutations(
        self,
        response: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        capabilities = response if response is not None else self.capabilities()
        operations = capabilities.get("operations")
        if not isinstance(operations, dict):
            raise HouseMechanicClientError(
                "House Mechanic capabilities response is missing operations"
            )
        projected: dict[str, dict[str, Any]] = {}
        for action, metadata in operations.items():
            if not isinstance(action, str):
                continue
            eligible, _ = self._projectable_mutation(metadata)
            if not eligible or not self.action_filter.allows(action):
                continue
            projected[action] = dict(metadata)
        return projected

    def projection_rejections(
        self,
        response: dict[str, Any],
    ) -> dict[str, str]:
        operations = response.get("operations")
        if not isinstance(operations, dict):
            raise HouseMechanicClientError(
                "House Mechanic capabilities response is missing operations"
            )
        rejected: dict[str, str] = {}
        for action, metadata in operations.items():
            if not isinstance(action, str):
                continue
            eligible, reason = self._projectable_mutation(metadata)
            if not eligible:
                rejected[action] = reason or "ineligible"
            elif not self.action_filter.allows(action):
                rejected[action] = "denied_by_dev_action_filter"
        return rejected

    def call(
        self,
        action: str,
        arguments: dict[str, Any],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or not action.strip():
            raise HouseMechanicClientError("House Mechanic action must be non-empty")
        if not isinstance(arguments, dict):
            raise HouseMechanicClientError(
                "House Mechanic arguments must be a JSON object"
            )
        canonical = action.strip()
        capabilities = self.capabilities(request_id=request_id)
        operations = capabilities["operations"]
        metadata = operations.get(canonical)
        if metadata is None:
            raise HouseMechanicClientError(
                f"Unknown House Mechanic action: {canonical}"
            )
        eligible, reason = self._projectable_mutation(metadata)
        if not eligible:
            raise HouseMechanicClientError(
                f"House Mechanic action is not a projected mutation: {reason}"
            )
        if not self.action_filter.allows(canonical):
            raise HouseMechanicClientError(
                f"House Mechanic action is not allowed by VESTIGIA_MCP_DEV_ACTIONS: {canonical}"
            )
        response = self._request(
            str(metadata["method"]),
            str(metadata["path"]),
            payload=arguments,
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def process_status(
        self,
        service_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/process-status",
            payload={"service_id": service_id},
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def health(
        self,
        service_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/health-check",
            payload={"service_id": service_id},
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def process_logs(
        self,
        service_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/process-logs",
            payload={"service_id": service_id},
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def recent_receipts(
        self,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v1/receipts",
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response

    def inspect_receipt(
        self,
        receipt_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/receipt",
            payload={"receipt_id": receipt_id},
            authenticated=True,
            request_id=request_id,
        )
        self._require_protocol(response)
        return response
