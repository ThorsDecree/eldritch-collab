from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .model import Manifest


SERVICE_SCHEMA_VERSION = "vestigia.house-mechanic-services.v0.2"
LEGACY_SERVICE_SCHEMA_VERSION = "vestigia.house-mechanic-services.v0.1"
_ALLOWED_TOP = {"schema_version", "services"}
_ALLOWED_SERVICE = {
    "id",
    "description",
    "ownership",
    "start_recipe",
    "stop_recipe",
    "health",
}
_ALLOWED_OWNERSHIP = {"external", "mechanic_child"}
_SERVICE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ALLOWED_HEALTH = {
    "kind",
    "host",
    "port",
    "path",
    "expected_status",
    "expected_protocol",
}


class ServiceManifestError(ValueError):
    pass


@dataclass(frozen=True)
class HealthProbe:
    kind: str
    host: str
    port: int
    path: str
    expected_status: int
    expected_protocol: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "expected_status": self.expected_status,
            "expected_protocol": self.expected_protocol,
        }


@dataclass(frozen=True)
class Service:
    id: str
    description: str
    ownership: str
    start_recipe: str | None
    stop_recipe: str | None
    health: HealthProbe | None

    @property
    def mechanic_owned(self) -> bool:
        return self.ownership == "mechanic_child"

    def digest(self) -> str:
        payload = {
            "id": self.id,
            "description": self.description,
            "ownership": self.ownership,
            "start_recipe": self.start_recipe,
            "stop_recipe": self.stop_recipe,
            "health": self.health.to_dict() if self.health else None,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "description": self.description,
            "ownership": self.ownership,
            "start_recipe": self.start_recipe,
            "stop_recipe": self.stop_recipe,
            "health": self.health.to_dict() if self.health else None,
            "sha256": self.digest(),
            "process_authority": self.mechanic_owned,
            "lifecycle_authority_exposed": self.mechanic_owned,
        }


@dataclass(frozen=True)
class ServiceManifest:
    services: dict[str, Service]


def _optional_recipe(
    service_id: str,
    field: str,
    value: object,
    recipes: Manifest,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ServiceManifestError(f"{service_id}: {field} must be a non-empty recipe ID")
    normalized = value.strip()
    if normalized not in recipes.recipes:
        raise ServiceManifestError(
            f"{service_id}: {field} references unknown recipe: {normalized}"
        )
    return normalized


def _health(service_id: str, value: object) -> HealthProbe | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ServiceManifestError(f"{service_id}: health must be an object")
    unknown = set(value) - _ALLOWED_HEALTH
    if unknown:
        raise ServiceManifestError(
            f"{service_id}: unknown health fields: {sorted(unknown)}"
        )

    kind = value.get("kind")
    if kind != "http":
        raise ServiceManifestError(f"{service_id}: only http health probes are supported")

    host = str(value.get("host", "127.0.0.1")).strip().lower()
    if host == "localhost":
        host = "127.0.0.1"
    if host != "127.0.0.1":
        raise ServiceManifestError(f"{service_id}: health host must be loopback")

    port = value.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ServiceManifestError(f"{service_id}: invalid health port")

    path = value.get("path", "/health")
    if not isinstance(path, str) or not path.startswith("/") or "://" in path:
        raise ServiceManifestError(f"{service_id}: health path must be a local absolute path")
    if len(path) > 512:
        raise ServiceManifestError(f"{service_id}: health path is too long")

    expected_status = value.get("expected_status", 200)
    if not isinstance(expected_status, int) or not 100 <= expected_status <= 599:
        raise ServiceManifestError(f"{service_id}: invalid expected_status")

    expected_protocol = value.get("expected_protocol")
    if expected_protocol is not None:
        if not isinstance(expected_protocol, str) or not expected_protocol.strip():
            raise ServiceManifestError(
                f"{service_id}: expected_protocol must be a non-empty string"
            )
        expected_protocol = expected_protocol.strip()
        if len(expected_protocol) > 128:
            raise ServiceManifestError(f"{service_id}: expected_protocol is too long")

    return HealthProbe(
        kind="http",
        host=host,
        port=port,
        path=path,
        expected_status=expected_status,
        expected_protocol=expected_protocol,
    )


def load_service_manifest(path: Path, recipes: Manifest) -> ServiceManifest:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ServiceManifestError("service manifest must be an object")
    unknown = set(data) - _ALLOWED_TOP
    if unknown:
        raise ServiceManifestError(
            f"unknown service manifest fields: {sorted(unknown)}"
        )

    schema_version = data.get("schema_version")
    if schema_version not in {
        LEGACY_SERVICE_SCHEMA_VERSION,
        SERVICE_SCHEMA_VERSION,
    }:
        raise ServiceManifestError("unsupported service manifest schema_version")

    rows = data.get("services")
    if not isinstance(rows, list):
        raise ServiceManifestError("services must be a list")

    out: dict[str, Service] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ServiceManifestError("service must be an object")
        unknown = set(row) - _ALLOWED_SERVICE
        if unknown:
            raise ServiceManifestError(
                f"unknown service fields: {sorted(unknown)}"
            )
        service_id = row.get("id")
        if not isinstance(service_id, str):
            raise ServiceManifestError("service id must be a string")
        service_id = service_id.strip()
        if not _SERVICE_ID.fullmatch(service_id):
            raise ServiceManifestError(
                "service id must be 1-128 filename-safe characters"
            )
        if service_id in out:
            raise ServiceManifestError("service id must be unique")

        if schema_version == LEGACY_SERVICE_SCHEMA_VERSION:
            if "ownership" in row:
                raise ServiceManifestError(
                    f"{service_id}: ownership requires service schema v0.2"
                )
            ownership = "external"
        else:
            ownership = str(row.get("ownership", "external")).strip()
            if ownership not in _ALLOWED_OWNERSHIP:
                raise ServiceManifestError(
                    f"{service_id}: ownership must be external or mechanic_child"
                )

        start_recipe = _optional_recipe(
            service_id,
            "start_recipe",
            row.get("start_recipe"),
            recipes,
        )
        stop_recipe = _optional_recipe(
            service_id,
            "stop_recipe",
            row.get("stop_recipe"),
            recipes,
        )

        if (
            schema_version == SERVICE_SCHEMA_VERSION
            and ownership == "external"
            and (start_recipe is not None or stop_recipe is not None)
        ):
            raise ServiceManifestError(
                f"{service_id}: external services cannot declare lifecycle recipes"
            )

        out[service_id] = Service(
            id=service_id,
            description=str(row.get("description", "")),
            ownership=ownership,
            start_recipe=start_recipe,
            stop_recipe=stop_recipe,
            health=_health(service_id, row.get("health")),
        )
    return ServiceManifest(out)
