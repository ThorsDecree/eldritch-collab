from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import time

from .service_model import Service


@dataclass(frozen=True)
class HealthResult:
    request_id: str
    service_id: str
    service_sha256: str
    checked_at: str
    healthy: bool
    reachable: bool
    expected_status: int | None
    observed_status: int | None
    expected_protocol: str | None
    observed_protocol: str | None
    latency_ms: float
    response_bytes_captured: int
    response_truncated: bool
    captured_response_sha256: str | None
    error_type: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def probe_service(
    service: Service,
    *,
    request_id: str,
    timeout_seconds: float = 3.0,
    max_response_bytes: int = 65_536,
) -> HealthResult:
    checked_at = datetime.now(UTC).isoformat()
    probe = service.health
    if probe is None:
        return HealthResult(
            request_id=request_id,
            service_id=service.id,
            service_sha256=service.digest(),
            checked_at=checked_at,
            healthy=False,
            reachable=False,
            expected_status=None,
            observed_status=None,
            expected_protocol=None,
            observed_protocol=None,
            latency_ms=0.0,
            response_bytes_captured=0,
            response_truncated=False,
            captured_response_sha256=None,
            error_type="no_health_probe",
        )

    started = time.monotonic()
    connection = HTTPConnection(
        probe.host,
        probe.port,
        timeout=max(0.1, float(timeout_seconds)),
    )
    observed_status: int | None = None
    observed_protocol: str | None = None
    captured = b""
    truncated = False
    error_type: str | None = None
    reachable = False

    try:
        connection.request(
            "GET",
            probe.path,
            headers={
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        reachable = True
        observed_status = int(response.status)
        captured = response.read(max_response_bytes + 1)
        if len(captured) > max_response_bytes:
            captured = captured[:max_response_bytes]
            truncated = True

        if probe.expected_protocol is not None:
            try:
                decoded = json.loads(captured.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_type = "invalid_json"
            else:
                if isinstance(decoded, dict):
                    value = decoded.get("protocol")
                    if value is not None:
                        observed_protocol = str(value)
                else:
                    error_type = "invalid_json_object"
    except (OSError, TimeoutError, HTTPException) as exc:
        error_type = type(exc).__name__
    finally:
        connection.close()

    status_ok = observed_status == probe.expected_status
    protocol_ok = (
        True
        if probe.expected_protocol is None
        else observed_protocol == probe.expected_protocol
    )
    healthy = bool(reachable and status_ok and protocol_ok and error_type is None)
    digest = hashlib.sha256(captured).hexdigest() if captured else None
    return HealthResult(
        request_id=request_id,
        service_id=service.id,
        service_sha256=service.digest(),
        checked_at=checked_at,
        healthy=healthy,
        reachable=reachable,
        expected_status=probe.expected_status,
        observed_status=observed_status,
        expected_protocol=probe.expected_protocol,
        observed_protocol=observed_protocol,
        latency_ms=round((time.monotonic() - started) * 1000.0, 3),
        response_bytes_captured=len(captured),
        response_truncated=truncated,
        captured_response_sha256=digest,
        error_type=error_type,
    )
