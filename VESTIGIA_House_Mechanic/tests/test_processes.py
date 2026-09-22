from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from house_mechanic.model import load_manifest
from house_mechanic.processes import ProcessRegistry
from house_mechanic.service_model import load_service_manifest


def _fixtures(tmp_path: Path):
    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "fixture.long",
                        "argv": [
                            sys.executable,
                            "-u",
                            "-c",
                            (
                                "import time; "
                                "print('owned-ready', flush=True); "
                                "time.sleep(1.0)"
                            ),
                        ],
                        "cwd": ".",
                        "env_profile": "python",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    recipes = load_manifest(recipes_path, tmp_path)
    services_path = tmp_path / "services.json"
    services_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-services.v0.2",
                "services": [
                    {
                        "id": "owned",
                        "ownership": "mechanic_child",
                        "start_recipe": "fixture.long",
                    },
                    {
                        "id": "external",
                        "ownership": "external",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    services = load_service_manifest(services_path, recipes)
    return recipes, services


def test_registry_distinguishes_external_and_owned_processes(tmp_path: Path) -> None:
    recipes, services = _fixtures(tmp_path)
    registry = ProcessRegistry(tmp_path / "state")
    owned = services.services["owned"]
    external = services.services["external"]

    assert registry.status(external).state == "external_unowned"
    assert registry.status(owned).state == "not_started"

    launched = registry.launch_owned(
        owned,
        recipes.recipes["fixture.long"],
        tmp_path,
    )
    assert launched.process_owned is True
    assert launched.state == "running"
    assert launched.pid is not None
    assert launched.generation_id is not None
    assert launched.ownership_survives_supervisor_restart is False

    deadline = time.monotonic() + 2
    logs = registry.logs(owned)
    while "owned-ready" not in logs["stdout_tail"] and time.monotonic() < deadline:
        time.sleep(0.02)
        logs = registry.logs(owned)
    assert "owned-ready" in logs["stdout_tail"]

    deadline = time.monotonic() + 3
    status = registry.status(owned)
    while status.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
        status = registry.status(owned)
    assert status.state == "exited"
    assert status.exit_code == 0
    assert status.process_owned is True


def test_new_registry_does_not_adopt_old_pid_state(tmp_path: Path) -> None:
    recipes, services = _fixtures(tmp_path)
    owned = services.services["owned"]
    first = ProcessRegistry(tmp_path / "state")
    first.launch_owned(owned, recipes.recipes["fixture.long"], tmp_path)
    try:
        second = ProcessRegistry(tmp_path / "state")
        status = second.status(owned)
        assert status.state == "not_started"
        assert status.process_owned is False
        assert status.pid is None
    finally:
        first.terminate_all_for_shutdown()
