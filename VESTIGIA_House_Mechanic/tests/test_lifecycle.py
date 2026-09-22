from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import time

import pytest

from house_mechanic.health import probe_service
from house_mechanic.lifecycle import LifecycleController, LifecycleError
from house_mechanic.model import load_manifest
from house_mechanic.processes import ProcessRegistry
from house_mechanic.service_model import load_service_manifest


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _fixtures(tmp_path: Path):
    port = _free_port()
    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "service.start",
                        "argv": [
                            sys.executable,
                            "-u",
                            "-m",
                            "http.server",
                            str(port),
                            "--bind",
                            "127.0.0.1",
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
                        "id": "fixture",
                        "ownership": "mechanic_child",
                        "start_recipe": "service.start",
                        "health": {
                            "kind": "http",
                            "host": "127.0.0.1",
                            "port": port,
                            "path": "/",
                            "expected_status": 200,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    services = load_service_manifest(services_path, recipes)
    registry = ProcessRegistry(tmp_path / "state")
    controller = LifecycleController(
        repo_root=tmp_path,
        recipes=recipes,
        processes=registry,
        health_probe_timeout_seconds=0.5,
        health_wait_seconds=5.0,
        health_poll_seconds=0.05,
        stop_timeout_seconds=2.0,
    )
    return recipes, services, registry, controller


def test_start_stop_and_restart_require_exact_owned_generation(tmp_path: Path) -> None:
    _recipes, services, registry, controller = _fixtures(tmp_path)
    service = services.services["fixture"]

    try:
        started = controller.start(service, request_id="req-start")
        assert started["verified"] is True, {
            "started": started,
            "logs": registry.logs(service),
        }
        assert started["outcome"] == "running_healthy"
        assert started["preflight_health"]["healthy"] is False
        first_generation = started["generation_id"]
        assert first_generation

        with pytest.raises(LifecycleError, match="generation"):
            controller.stop(
                service,
                request_id="req-wrong-stop",
                generation_id="hm_proc_not_the_owned_generation",
            )

        restarted = controller.restart(
            service,
            request_id="req-restart",
            generation_id=first_generation,
        )
        assert restarted["verified"] is True
        assert restarted["old_generation_id"] == first_generation
        second_generation = restarted["new_generation_id"]
        assert second_generation
        assert second_generation != first_generation

        stopped = controller.stop(
            service,
            request_id="req-stop",
            generation_id=second_generation,
        )
        assert stopped["verified"] is True
        assert stopped["process_exit_verified"] is True
        assert stopped["endpoint_absent_verified"] is True
        assert stopped["after"]["state"] == "exited"
    finally:
        registry.terminate_all_for_shutdown()


def test_start_refuses_preexisting_healthy_endpoint(tmp_path: Path) -> None:
    recipes, services, registry, controller = _fixtures(tmp_path)
    service = services.services["fixture"]

    first = ProcessRegistry(tmp_path / "other-state")
    try:
        first.launch_owned(service, recipes.recipes["service.start"], tmp_path)
        deadline = time.monotonic() + 5
        observed = probe_service(
            service,
            request_id="req-preexisting-wait",
            timeout_seconds=0.5,
        )
        while not observed.healthy and time.monotonic() < deadline:
            time.sleep(0.05)
            observed = probe_service(
                service,
                request_id="req-preexisting-wait",
                timeout_seconds=0.5,
            )
        assert observed.healthy is True, {
            "health": observed.to_dict(),
            "logs": first.logs(service),
        }

        with pytest.raises(
            LifecycleError,
            match="already healthy before launch",
        ) as exc:
            controller.start(service, request_id="req-preexisting")
        assert exc.value.code == "health_already_healthy_unowned"
        assert registry.status(service).state == "not_started"
    finally:
        first.terminate_all_for_shutdown()
        registry.terminate_all_for_shutdown()
