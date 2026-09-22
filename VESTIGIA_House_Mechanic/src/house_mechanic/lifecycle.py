from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .health import HealthResult, probe_service
from .model import Manifest
from .processes import ProcessOwnershipError, ProcessRegistry
from .service_model import Service


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LifecycleController:
    def __init__(
        self,
        *,
        repo_root: Path,
        recipes: Manifest,
        processes: ProcessRegistry,
        health_probe_timeout_seconds: float = 3.0,
        health_wait_seconds: float = 10.0,
        health_poll_seconds: float = 0.1,
        stop_timeout_seconds: float = 5.0,
        health_max_response_bytes: int = 65_536,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.recipes = recipes
        self.processes = processes
        self.health_probe_timeout_seconds = float(health_probe_timeout_seconds)
        self.health_wait_seconds = float(health_wait_seconds)
        self.health_poll_seconds = float(health_poll_seconds)
        self.stop_timeout_seconds = float(stop_timeout_seconds)
        self.health_max_response_bytes = int(health_max_response_bytes)

    @staticmethod
    def _require_owned(service: Service) -> None:
        if not service.mechanic_owned:
            raise LifecycleError(
                "external_service",
                "service is declared external and has no lifecycle authority",
            )

    def _probe(self, service: Service, request_id: str) -> HealthResult:
        return probe_service(
            service,
            request_id=request_id,
            timeout_seconds=self.health_probe_timeout_seconds,
            max_response_bytes=self.health_max_response_bytes,
        )

    def _wait_healthy(
        self,
        service: Service,
        request_id: str,
    ) -> tuple[HealthResult, dict[str, object]]:
        deadline = time.monotonic() + self.health_wait_seconds
        last = self._probe(service, request_id)
        status = self.processes.status(service)
        while (
            not last.healthy
            and status.state == "running"
            and time.monotonic() < deadline
        ):
            time.sleep(self.health_poll_seconds)
            last = self._probe(service, request_id)
            status = self.processes.status(service)
        return last, status.to_dict()

    def _wait_unhealthy(
        self,
        service: Service,
        request_id: str,
    ) -> HealthResult:
        deadline = time.monotonic() + self.health_wait_seconds
        last = self._probe(service, request_id)
        while last.healthy and time.monotonic() < deadline:
            time.sleep(self.health_poll_seconds)
            last = self._probe(service, request_id)
        return last

    def start(self, service: Service, *, request_id: str) -> dict[str, Any]:
        self._require_owned(service)
        if service.start_recipe is None:
            raise LifecycleError(
                "start_recipe_missing",
                "mechanic-owned service has no declared start recipe",
            )
        if service.health is None:
            raise LifecycleError(
                "health_probe_required",
                "bounded start requires a declared health probe",
            )

        before = self.processes.status(service)
        if before.state == "running":
            raise LifecycleError(
                "already_running",
                "service already has a running process owned by this supervisor",
            )

        preflight = self._probe(service, request_id)
        if preflight.healthy:
            raise LifecycleError(
                "health_already_healthy_unowned",
                (
                    "declared health endpoint was already healthy before launch; "
                    "refusing to attribute that endpoint to a new child process"
                ),
            )

        recipe = self.recipes.recipes.get(service.start_recipe)
        if recipe is None:
            raise LifecycleError(
                "start_recipe_missing",
                "declared start recipe is not present in the loaded recipe manifest",
            )

        launched = self.processes.launch_owned(
            service,
            recipe,
            self.repo_root,
        )
        final_health, after = self._wait_healthy(service, request_id)
        verified = bool(
            final_health.healthy
            and after.get("state") == "running"
            and after.get("generation_id") == launched.generation_id
        )
        return {
            "action": "start",
            "service_id": service.id,
            "service_sha256": service.digest(),
            "start_recipe": recipe.id,
            "start_recipe_sha256": recipe.digest(),
            "action_occurred": True,
            "verified": verified,
            "outcome": "running_healthy" if verified else "started_unverified",
            "before": before.to_dict(),
            "after": after,
            "generation_id": launched.generation_id,
            "preflight_health": preflight.to_dict(),
            "final_health": final_health.to_dict(),
            "health_transition_required": True,
            "health_generation_bound": False,
            "health_attribution": "temporal_after_owned_launch",
        }

    def stop(
        self,
        service: Service,
        *,
        request_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        self._require_owned(service)
        before = self.processes.status(service)
        try:
            stopped = self.processes.stop_owned(
                service,
                expected_generation_id=generation_id,
                timeout_seconds=self.stop_timeout_seconds,
            )
        except ProcessOwnershipError as exc:
            raise LifecycleError(exc.code, exc.message) from exc

        final_health = (
            self._wait_unhealthy(service, request_id)
            if service.health is not None
            else None
        )
        process_exit_verified = bool(
            stopped["after"]["state"] == "exited"
            and stopped["after"]["generation_id"] == generation_id
        )
        endpoint_absent_verified = (
            True if final_health is None else not final_health.healthy
        )
        verified = bool(process_exit_verified and endpoint_absent_verified)
        if verified:
            outcome = "stopped"
        elif not process_exit_verified:
            outcome = "process_exit_unverified"
        else:
            outcome = "process_stopped_endpoint_still_healthy"
        return {
            "action": "stop",
            "service_id": service.id,
            "service_sha256": service.digest(),
            "action_occurred": bool(stopped["action_occurred"]),
            "verified": verified,
            "outcome": outcome,
            "before": before.to_dict(),
            "after": stopped["after"],
            "generation_id": generation_id,
            "termination": stopped["termination"],
            "final_health": final_health.to_dict() if final_health else None,
            "process_exit_verified": process_exit_verified,
            "endpoint_absent_verified": endpoint_absent_verified,
        }

    def restart(
        self,
        service: Service,
        *,
        request_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        self._require_owned(service)
        stop_result = self.stop(
            service,
            request_id=request_id,
            generation_id=generation_id,
        )
        if not stop_result["verified"]:
            return {
                "action": "restart",
                "service_id": service.id,
                "service_sha256": service.digest(),
                "action_occurred": bool(stop_result["action_occurred"]),
                "verified": False,
                "outcome": "stop_unverified",
                "old_generation_id": generation_id,
                "new_generation_id": None,
                "stop": stop_result,
                "start": None,
            }

        try:
            start_result = self.start(service, request_id=request_id)
        except LifecycleError as exc:
            return {
                "action": "restart",
                "service_id": service.id,
                "service_sha256": service.digest(),
                "action_occurred": True,
                "verified": False,
                "outcome": "stopped_start_blocked",
                "old_generation_id": generation_id,
                "new_generation_id": None,
                "stop": stop_result,
                "start": None,
                "start_error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }

        return {
            "action": "restart",
            "service_id": service.id,
            "service_sha256": service.digest(),
            "action_occurred": True,
            "verified": bool(start_result["verified"]),
            "outcome": "restarted_healthy" if start_result["verified"] else "restarted_unverified",
            "old_generation_id": generation_id,
            "new_generation_id": start_result["generation_id"],
            "stop": stop_result,
            "start": start_result,
        }
