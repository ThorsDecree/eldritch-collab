from pathlib import Path
import json

import pytest

from house_mechanic.model import load_manifest
from house_mechanic.service_model import ServiceManifestError, load_service_manifest


def _recipes(tmp_path: Path):
    path = tmp_path / "recipes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "bridge.start",
                        "argv": ["python", "-V"],
                        "cwd": ".",
                    },
                    {
                        "id": "bridge.stop",
                        "argv": ["python", "-V"],
                        "cwd": ".",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_manifest(path, tmp_path)


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "services.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_service_manifest_resolves_recipe_references(tmp_path: Path) -> None:
    manifest = load_service_manifest(
        _write(
            tmp_path,
            {
                "schema_version": "vestigia.house-mechanic-services.v0.1",
                "services": [
                    {
                        "id": "daemon-bridge",
                        "description": "Bridge",
                        "start_recipe": "bridge.start",
                        "stop_recipe": "bridge.stop",
                        "health": {
                            "kind": "http",
                            "host": "localhost",
                            "port": 8766,
                            "path": "/health",
                            "expected_status": 200,
                            "expected_protocol": "daemon-bridge-api.v0.1",
                        },
                    }
                ],
            },
        ),
        _recipes(tmp_path),
    )
    service = manifest.services["daemon-bridge"]
    assert service.health is not None
    assert service.health.host == "127.0.0.1"
    assert service.start_recipe == "bridge.start"
    assert service.ownership == "external"
    assert service.public_dict()["process_authority"] is False
    assert service.public_dict()["lifecycle_authority_exposed"] is False


def test_service_manifest_rejects_unknown_recipe(tmp_path: Path) -> None:
    with pytest.raises(ServiceManifestError, match="unknown recipe"):
        load_service_manifest(
            _write(
                tmp_path,
                {
                    "schema_version": "vestigia.house-mechanic-services.v0.1",
                    "services": [
                        {
                            "id": "daemon-bridge",
                            "start_recipe": "bridge.missing",
                        }
                    ],
                },
            ),
            _recipes(tmp_path),
        )


def test_service_manifest_rejects_non_loopback_health(tmp_path: Path) -> None:
    with pytest.raises(ServiceManifestError, match="loopback"):
        load_service_manifest(
            _write(
                tmp_path,
                {
                    "schema_version": "vestigia.house-mechanic-services.v0.1",
                    "services": [
                        {
                            "id": "daemon-bridge",
                            "health": {
                                "kind": "http",
                                "host": "0.0.0.0",
                                "port": 8766,
                            },
                        }
                    ],
                },
            ),
            _recipes(tmp_path),
        )


def test_v02_manifest_types_mechanic_owned_service(tmp_path: Path) -> None:
    manifest = load_service_manifest(
        _write(
            tmp_path,
            {
                "schema_version": "vestigia.house-mechanic-services.v0.2",
                "services": [
                    {
                        "id": "daemon-bridge",
                        "ownership": "mechanic_child",
                        "start_recipe": "bridge.start",
                        "stop_recipe": "bridge.stop",
                    }
                ],
            },
        ),
        _recipes(tmp_path),
    )
    service = manifest.services["daemon-bridge"]
    assert service.ownership == "mechanic_child"
    assert service.mechanic_owned is True
    assert service.public_dict()["process_authority"] is True
    assert service.public_dict()["lifecycle_authority_exposed"] is True


def test_v02_external_service_rejects_lifecycle_recipes(tmp_path: Path) -> None:
    with pytest.raises(ServiceManifestError, match="external services cannot"):
        load_service_manifest(
            _write(
                tmp_path,
                {
                    "schema_version": "vestigia.house-mechanic-services.v0.2",
                    "services": [
                        {
                            "id": "daemon-bridge",
                            "ownership": "external",
                            "start_recipe": "bridge.start",
                        }
                    ],
                },
            ),
            _recipes(tmp_path),
        )


def test_service_id_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ServiceManifestError, match="filename-safe"):
        load_service_manifest(
            _write(
                tmp_path,
                {
                    "schema_version": "vestigia.house-mechanic-services.v0.2",
                    "services": [
                        {
                            "id": "../escape",
                            "ownership": "mechanic_child",
                        }
                    ],
                },
            ),
            _recipes(tmp_path),
        )
