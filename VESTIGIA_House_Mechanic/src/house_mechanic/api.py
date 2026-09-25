from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from typing import Any
import uuid

from .deployment import DeploymentController, DeploymentError, DeploymentLedger
from .health import probe_service
from .lifecycle import LifecycleController, LifecycleError
from .model import Manifest
from .processes import ProcessRegistry
from .receipts import ReceiptStore
from .runner import run_recipe
from .service_model import ServiceManifest
from .tasking import RepositoryManifest, TaskError, TaskLedger, TaskSupervisor, WorktreeManager


PROTOCOL = "vestigia.house-mechanic-api.v0.7"
MAX_REQUEST_BYTES = 16_384
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_GENERATION_ID = re.compile(r"^hm_proc_[0-9a-f]{32}$")


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
                        "receipt_persistence": True,
                        "process_authority": True,
                        "process_authority_scope": "mechanic_child_only",
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
                                "durable_receipt": True,
                                "max_parallel": self.api.max_parallel,
                            },
                            "service.list": {
                                "effect": "read",
                                "process_authority": False,
                            },
                            "service.health": {
                                "effect": "loopback_read",
                                "process_authority": False,
                                "redirects_followed": False,
                            },
                            "service.process_status": {
                                "effect": "read",
                                "lifecycle_authority": False,
                                "ownership_scope": "supervisor_instance",
                            },
                            "service.process_logs": {
                                "effect": "read",
                                "lifecycle_authority": False,
                                "tail_limit_bytes": 16384,
                            },
                            "service.start": {
                                "effect": "owned_process_mutation",
                                "ownership_required": "mechanic_child",
                                "health_transition_required": True,
                                "health_generation_bound": False,
                            },
                            "service.stop": {
                                "effect": "owned_process_mutation",
                                "ownership_required": "mechanic_child",
                                "exact_generation_required": True,
                            },
                            "service.restart": {
                                "effect": "owned_process_mutation",
                                "ownership_required": "mechanic_child",
                                "exact_generation_required": True,
                                "health_transition_required": True,
                            },
                            "receipt.recent": {
                                "effect": "read",
                                "raw_full_output_persisted": False,
                            },
                            "receipt.inspect": {
                                "effect": "read",
                                "raw_full_output_persisted": False,
                            },
                            "task.list": {
                                "effect": "read",
                                "enabled": self.api.tasks is not None,
                            },
                            "task.acquire": {
                                "effect": "worktree_mutation",
                                "enabled": self.api.tasks is not None,
                                "caller_supplies_worktree_path": False,
                                "caller_supplies_branch_name": False,
                                "durable_receipt": True,
                            },
                            "task.mutate": {
                                "effect": "bounded_worktree_mutation",
                                "enabled": self.api.tasks is not None,
                                "authority_tuple": ["task_id", "holder_id", "authority_generation"],
                                "durable_receipt": True,
                            },
                            "task.renew": {
                                "effect": "lease_mutation",
                                "enabled": self.api.tasks is not None,
                                "authority_tuple": ["task_id", "holder_id", "authority_generation"],
                                "durable_receipt": True,
                            },
                            "task.extend_budget": {
                                "effect": "budget_mutation",
                                "enabled": self.api.tasks is not None,
                                "authority_tuple": ["task_id", "holder_id", "authority_generation"],
                                "durable_receipt": True,
                            },
                            "task.refresh_base": {
                                "effect": "bounded_git_rebase",
                                "enabled": self.api.tasks is not None,
                                "authority_tuple": ["task_id", "holder_id", "authority_generation"],
                                "caller_supplies_git_argv": False,
                                "conflicts_auto_resolved": False,
                                "durable_receipt": True,
                            },
                            "task.abort_refresh": {
                                "effect": "bounded_git_rebase_abort",
                                "enabled": self.api.tasks is not None,
                                "authority_tuple": ["task_id", "holder_id", "authority_generation"],
                                "durable_receipt": True,
                            },
                            "deployment.status": {
                                "effect": "read",
                                "enabled": self.api.deployments is not None,
                            },
                            "deployment.candidate": {
                                "effect": "owned_service_deployment",
                                "enabled": self.api.deployments is not None,
                                "candidate_source": "clean_checkpointed_task_commit",
                                "automatic_conflict_resolution": False,
                                "automatic_rollback_when_lkg_exists": True,
                                "durable_receipt": True,
                            },
                            "deployment.promote": {
                                "effect": "last_known_good_mutation",
                                "enabled": self.api.deployments is not None,
                                "exact_generation_required": True,
                                "health_verification_required": True,
                                "durable_receipt": True,
                            },
                            "deployment.rollback": {
                                "effect": "owned_service_deployment",
                                "enabled": self.api.deployments is not None,
                                "exact_generation_required_when_running": True,
                                "durable_receipt": True,
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

            if self.path == "/v1/receipts":
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "receipts": self.api.receipts.recent(limit=50),
                    },
                )
                return

            if self.path == "/v1/tasks":
                tasks = self._require_tasks()
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "tasks": [record.to_dict() for record in tasks.ledger.list()],
                    },
                )
                return

            if self.path == "/v1/deployments":
                deployments = self._require_deployments()
                rows = []
                for service in self.api.services.services.values():
                    if service.deployment is None:
                        continue
                    rows.append(deployments.status(service))
                self._send(
                    200,
                    {
                        "protocol": PROTOCOL,
                        "request_id": request_id,
                        "deployments": rows,
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

            if self.path == "/v1/run":
                self._run_recipe(request_id)
                return

            if self.path == "/v1/health-check":
                self._health_check(request_id)
                return

            if self.path == "/v1/receipt":
                self._inspect_receipt(request_id)
                return

            if self.path == "/v1/process-status":
                self._process_status(request_id)
                return

            if self.path == "/v1/process-logs":
                self._process_logs(request_id)
                return

            if self.path == "/v1/process-start":
                self._process_start(request_id)
                return

            if self.path == "/v1/process-stop":
                self._process_stop(request_id)
                return

            if self.path == "/v1/process-restart":
                self._process_restart(request_id)
                return

            task_routes = {
                "/v1/task-show": self._task_show,
                "/v1/task-acquire": self._task_acquire,
                "/v1/task-resume": self._task_resume,
                "/v1/task-recover": self._task_recover,
                "/v1/task-renew": self._task_renew,
                "/v1/task-extend-budget": self._task_extend_budget,
                "/v1/task-refresh-base": self._task_refresh_base,
                "/v1/task-abort-refresh": self._task_abort_refresh,
                "/v1/deploy-candidate": self._deploy_candidate,
                "/v1/deploy-promote": self._deploy_promote,
                "/v1/deploy-rollback": self._deploy_rollback,
                "/v1/iteration-begin": self._iteration_begin,
                "/v1/iteration-checkpoint": self._iteration_checkpoint,
                "/v1/handoff-offer": self._handoff_offer,
                "/v1/handoff-respond": self._handoff_respond,
                "/v1/task-finish": self._task_finish,
                "/v1/task-cleanup": self._task_cleanup,
            }
            task_handler = task_routes.get(self.path)
            if task_handler is not None:
                task_handler(request_id)
                return

            raise HouseMechanicAPIError(404, "not_found", "route not found")
        except HouseMechanicAPIError as exc:
            self._error(exc, request_id)
        except Exception:
            self._error(
                HouseMechanicAPIError(500, "internal_error", "request failed"),
                request_id,
            )

    def _run_recipe(self, request_id: str) -> None:
        payload = self._json_body()
        self._require_exact_fields(payload, {"recipe_id"})
        recipe_id = payload.get("recipe_id")
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "recipe_id must be a non-empty string",
            )

        recipe_id = recipe_id.strip()
        recipe = self.api.recipes.recipes.get(recipe_id)
        if recipe is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_recipe",
                "recipe is not present in the operator manifest",
            )
        if recipe_id in self.api.lifecycle_recipe_ids:
            raise HouseMechanicAPIError(
                403,
                "lifecycle_recipe_reserved",
                (
                    "recipe is reserved for a typed service lifecycle action "
                    "and cannot run through the generic recipe endpoint"
                ),
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

        try:
            durable = self.api.receipts.append_run(receipt)
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "execution_occurred": True,
                    "receipt_persisted": False,
                    "receipt": receipt.to_dict(),
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": (
                            "recipe execution completed but durable receipt "
                            "persistence failed"
                        ),
                    },
                },
            )
            return

        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "execution_occurred": True,
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "receipt": receipt.to_dict(),
            },
        )

    def _health_check(self, request_id: str) -> None:
        payload = self._json_body()
        self._require_exact_fields(payload, {"service_id"})
        service_id = payload.get("service_id")
        if not isinstance(service_id, str) or not service_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "service_id must be a non-empty string",
            )
        service = self.api.services.services.get(service_id.strip())
        if service is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_service",
                "service is not present in the operator manifest",
            )

        result = probe_service(
            service,
            request_id=request_id,
            timeout_seconds=self.api.health_timeout_seconds,
            max_response_bytes=self.api.health_max_response_bytes,
        )
        try:
            durable = self.api.receipts.append_health(result)
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "observation_occurred": True,
                    "receipt_persisted": False,
                    "health": result.to_dict(),
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": (
                            "health observation completed but durable receipt "
                            "persistence failed"
                        ),
                    },
                },
            )
            return

        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "observation_occurred": True,
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "health": result.to_dict(),
            },
        )

    def _service_from_payload(
        self,
        payload: dict[str, Any],
    ):
        self._require_exact_fields(payload, {"service_id"})
        service_id = payload.get("service_id")
        if not isinstance(service_id, str) or not service_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "service_id must be a non-empty string",
            )
        service = self.api.services.services.get(service_id.strip())
        if service is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_service",
                "service is not present in the operator manifest",
            )
        return service

    def _process_status(self, request_id: str) -> None:
        service = self._service_from_payload(self._json_body())
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "process": self.api.processes.status(service).to_dict(),
                "lifecycle_authority_exposed": service.mechanic_owned,
            },
        )

    def _process_logs(self, request_id: str) -> None:
        service = self._service_from_payload(self._json_body())
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "logs": self.api.processes.logs(service),
                "lifecycle_authority_exposed": service.mechanic_owned,
            },
        )

    @staticmethod
    def _generation_from_payload(payload: dict[str, Any]) -> str:
        generation_id = payload.get("generation_id")
        if not isinstance(generation_id, str):
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "generation_id must be a string",
            )
        generation_id = generation_id.strip()
        if not _GENERATION_ID.fullmatch(generation_id):
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "generation_id must match an issued House Mechanic generation",
            )
        return generation_id

    @staticmethod
    def _raise_lifecycle(exc: LifecycleError) -> None:
        status = 403 if exc.code == "external_service" else 409
        raise HouseMechanicAPIError(status, exc.code, exc.message)

    def _persist_lifecycle(
        self,
        *,
        request_id: str,
        result: dict[str, Any],
    ) -> None:
        try:
            durable = self.api.receipts.append_lifecycle(
                request_id=request_id,
                action=str(result.get("action") or "unknown"),
                evidence=result,
            )
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "action_occurred": bool(result.get("action_occurred")),
                    "verified": bool(result.get("verified")),
                    "receipt_persisted": False,
                    "lifecycle": result,
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": (
                            "lifecycle action completed but durable receipt "
                            "persistence failed"
                        ),
                    },
                },
            )
            return

        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "action_occurred": bool(result.get("action_occurred")),
                "verified": bool(result.get("verified")),
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "lifecycle": result,
            },
        )

    def _acquire_lifecycle(self) -> None:
        if not self.api.lifecycle_lock.acquire(blocking=False):
            raise HouseMechanicAPIError(
                409,
                "lifecycle_busy",
                "another lifecycle mutation is already in progress",
            )

    def _process_start(self, request_id: str) -> None:
        payload = self._json_body()
        service = self._service_from_payload(payload)
        self._acquire_lifecycle()
        try:
            try:
                result = self.api.lifecycle.start(
                    service,
                    request_id=request_id,
                )
            except LifecycleError as exc:
                self._raise_lifecycle(exc)
        finally:
            self.api.lifecycle_lock.release()
        self._persist_lifecycle(request_id=request_id, result=result)

    def _process_stop(self, request_id: str) -> None:
        payload = self._json_body()
        self._require_exact_fields(payload, {"service_id", "generation_id"})
        service_id = payload.get("service_id")
        if not isinstance(service_id, str) or not service_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "service_id must be a non-empty string",
            )
        service = self.api.services.services.get(service_id.strip())
        if service is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_service",
                "service is not present in the operator manifest",
            )
        generation_id = self._generation_from_payload(payload)

        self._acquire_lifecycle()
        try:
            try:
                result = self.api.lifecycle.stop(
                    service,
                    request_id=request_id,
                    generation_id=generation_id,
                )
            except LifecycleError as exc:
                self._raise_lifecycle(exc)
        finally:
            self.api.lifecycle_lock.release()
        self._persist_lifecycle(request_id=request_id, result=result)

    def _process_restart(self, request_id: str) -> None:
        payload = self._json_body()
        self._require_exact_fields(payload, {"service_id", "generation_id"})
        service_id = payload.get("service_id")
        if not isinstance(service_id, str) or not service_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "service_id must be a non-empty string",
            )
        service = self.api.services.services.get(service_id.strip())
        if service is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_service",
                "service is not present in the operator manifest",
            )
        generation_id = self._generation_from_payload(payload)

        self._acquire_lifecycle()
        try:
            try:
                result = self.api.lifecycle.restart(
                    service,
                    request_id=request_id,
                    generation_id=generation_id,
                )
            except LifecycleError as exc:
                self._raise_lifecycle(exc)
        finally:
            self.api.lifecycle_lock.release()
        self._persist_lifecycle(request_id=request_id, result=result)

    def _require_deployments(self) -> DeploymentController:
        if self.api.deployments is None:
            raise HouseMechanicAPIError(
                503,
                "deployment_not_configured",
                "House Mechanic deployment is not configured for this supervisor",
            )
        return self.api.deployments

    @staticmethod
    def _raise_deployment(exc: DeploymentError) -> None:
        if exc.code in {"deployment_not_configured"}:
            status = 404
        elif exc.code in {"wrong_holder"}:
            status = 403
        elif exc.code.startswith("invalid_"):
            status = 400
        else:
            status = 409
        raise HouseMechanicAPIError(status, exc.code, exc.message)

    def _persist_deployment(
        self,
        *,
        request_id: str,
        result: dict[str, Any],
    ) -> None:
        operation = str(result.get("operation") or "unknown")
        try:
            durable = self.api.receipts.append_deployment(
                request_id=request_id,
                operation=operation,
                evidence=result,
            )
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "action_occurred": bool(result.get("action_occurred")),
                    "verified": bool(result.get("verified")),
                    "receipt_persisted": False,
                    "deployment": result,
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": "deployment action occurred but durable receipt persistence failed",
                    },
                },
            )
            return
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "action_occurred": bool(result.get("action_occurred")),
                "verified": bool(result.get("verified")),
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "deployment": result,
            },
        )

    def _deployment_service(self, service_id: object):
        if not isinstance(service_id, str) or not service_id.strip():
            raise HouseMechanicAPIError(400, "invalid_request", "service_id must be a non-empty string")
        service = self.api.services.services.get(service_id.strip())
        if service is None:
            raise HouseMechanicAPIError(404, "unknown_service", "service is not present in the operator manifest")
        return service

    def _deploy_candidate(self, request_id: str) -> None:
        deployments = self._require_deployments()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {
                "service_id",
                "task_id",
                "holder_id",
                "authority_generation",
                "expected_generation_id",
            },
        )
        service = self._deployment_service(payload.get("service_id"))
        expected = payload.get("expected_generation_id")
        if expected is not None and not isinstance(expected, str):
            raise HouseMechanicAPIError(400, "invalid_request", "expected_generation_id must be a string or null")
        try:
            result = deployments.deploy_candidate(
                service,
                request_id=request_id,
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                expected_generation_id=expected,
            )
        except (DeploymentError, TypeError, ValueError) as exc:
            if isinstance(exc, DeploymentError):
                self._raise_deployment(exc)
            raise HouseMechanicAPIError(400, "invalid_request", "authority_generation must be an integer")
        self._persist_deployment(request_id=request_id, result=result)

    def _deploy_promote(self, request_id: str) -> None:
        deployments = self._require_deployments()
        payload = self._json_body()
        self._require_exact_fields(payload, {"service_id", "generation_id"})
        service = self._deployment_service(payload.get("service_id"))
        generation_id = self._generation_from_payload(payload)
        try:
            result = deployments.promote(
                service,
                request_id=request_id,
                generation_id=generation_id,
            )
        except DeploymentError as exc:
            self._raise_deployment(exc)
        self._persist_deployment(request_id=request_id, result=result)

    def _deploy_rollback(self, request_id: str) -> None:
        deployments = self._require_deployments()
        payload = self._json_body()
        self._require_exact_fields(payload, {"service_id", "expected_generation_id"})
        service = self._deployment_service(payload.get("service_id"))
        expected = payload.get("expected_generation_id")
        if expected is not None:
            if not isinstance(expected, str):
                raise HouseMechanicAPIError(400, "invalid_request", "expected_generation_id must be a string or null")
            expected = expected.strip()
            if not _GENERATION_ID.fullmatch(expected):
                raise HouseMechanicAPIError(
                    400,
                    "invalid_request",
                    "expected_generation_id must match an issued House Mechanic generation",
                )
        try:
            result = deployments.rollback(
                service,
                request_id=request_id,
                expected_generation_id=expected,
            )
        except DeploymentError as exc:
            self._raise_deployment(exc)
        self._persist_deployment(request_id=request_id, result=result)

    def _require_tasks(self) -> TaskSupervisor:
        if self.api.tasks is None:
            raise HouseMechanicAPIError(
                503,
                "tasking_not_configured",
                "House Mechanic tasking is not configured for this supervisor",
            )
        return self.api.tasks

    @staticmethod
    def _raise_task(exc: TaskError) -> None:
        if exc.code in {"unknown_task", "unknown_repository"}:
            status = 404
        elif exc.code in {"wrong_holder"}:
            status = 403
        elif exc.code.startswith("invalid_") or exc.code in {"base_ref_not_allowed"}:
            status = 400
        else:
            status = 409
        raise HouseMechanicAPIError(status, exc.code, exc.message)

    def _persist_task_transition(
        self,
        *,
        request_id: str,
        operation: str,
        before: dict[str, Any] | None,
        after: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        evidence = {
            "before": before,
            "after": after,
            "action_occurred": True,
            **(extra or {}),
        }
        try:
            durable = self.api.receipts.append_task_transition(
                request_id=request_id,
                operation=operation,
                evidence=evidence,
            )
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "action_occurred": True,
                    "receipt_persisted": False,
                    "task": after,
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": "task mutation occurred but durable receipt persistence failed",
                    },
                },
            )
            return
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "action_occurred": True,
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "task": after,
                **(extra or {}),
            },
        )

    def _task_show(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id"})
        try:
            record, snapshot = tasks.show(str(payload.get("task_id") or ""))
        except TaskError as exc:
            self._raise_task(exc)
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "task": record.to_dict(),
                "git": snapshot.to_dict() if snapshot else None,
            },
        )

    def _task_acquire(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {"repository_id", "holder_id", "purpose", "base_ref", "lease_seconds", "iteration_limit"},
        )
        try:
            record = tasks.acquire(
                repository_id=str(payload.get("repository_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                purpose=str(payload.get("purpose") or ""),
                base_ref=payload.get("base_ref"),
                lease_seconds=payload.get("lease_seconds"),
                iteration_limit=payload.get("iteration_limit"),
            )
            _, snapshot = tasks.show(record.task_id)
        except TaskError as exc:
            self._raise_task(exc)
        self._persist_task_transition(
            request_id=request_id,
            operation="acquire",
            before=None,
            after=record.to_dict(),
            extra={"git": snapshot.to_dict() if snapshot else None},
        )

    def _task_resume(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "lease_seconds"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.resume(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                lease_seconds=payload.get("lease_seconds"),
            )
        except TaskError as exc:
            self._raise_task(exc)
        self._persist_task_transition(request_id=request_id, operation="resume", before=before, after=record.to_dict())

    def _task_recover(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "lease_seconds"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.recover_interrupted_iteration(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                lease_seconds=payload.get("lease_seconds"),
            )
        except TaskError as exc:
            self._raise_task(exc)
        self._persist_task_transition(request_id=request_id, operation="recover_interrupted_iteration", before=before, after=record.to_dict())

    def _task_renew(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {"task_id", "holder_id", "authority_generation", "lease_seconds"},
        )
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.renew(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                lease_seconds=payload.get("lease_seconds"),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "authority_generation must be an integer",
            )
        self._persist_task_transition(
            request_id=request_id,
            operation="renew",
            before=before,
            after=record.to_dict(),
        )

    def _task_extend_budget(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {"task_id", "holder_id", "authority_generation", "additional_iterations"},
        )
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.extend_budget(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                additional_iterations=int(payload.get("additional_iterations")),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "authority_generation and additional_iterations must be integers",
            )
        self._persist_task_transition(
            request_id=request_id,
            operation="extend_budget",
            before=before,
            after=record.to_dict(),
        )

    def _task_refresh_base(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {"task_id", "holder_id", "authority_generation", "base_ref"},
        )
        task_id = str(payload.get("task_id") or "")
        try:
            before = tasks.ledger.get(task_id).to_dict()
            record, candidate, success, snapshot, detail = tasks.refresh_base(
                task_id=task_id,
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                base_ref=payload.get("base_ref"),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "authority_generation must be an integer",
            )
        self._persist_task_transition(
            request_id=request_id,
            operation="refresh_base",
            before=before,
            after=record.to_dict(),
            extra={
                "candidate_base_commit": candidate,
                "refresh_succeeded": success,
                "git": snapshot.to_dict(),
                "git_detail": detail,
            },
        )

    def _task_abort_refresh(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(
            payload,
            {"task_id", "holder_id", "authority_generation"},
        )
        task_id = str(payload.get("task_id") or "")
        try:
            before = tasks.ledger.get(task_id).to_dict()
            record, snapshot = tasks.abort_refresh(
                task_id=task_id,
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "authority_generation must be an integer",
            )
        self._persist_task_transition(
            request_id=request_id,
            operation="abort_refresh",
            before=before,
            after=record.to_dict(),
            extra={"git": snapshot.to_dict()},
        )

    def _iteration_begin(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "authority_generation"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record, snapshot = tasks.begin_iteration(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(400, "invalid_request", "authority_generation must be an integer")
        self._persist_task_transition(
            request_id=request_id,
            operation="iteration_begin",
            before=before,
            after=record.to_dict(),
            extra={"git": snapshot.to_dict()},
        )

    def _iteration_checkpoint(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "authority_generation", "iteration_id", "outcome"})
        task_id = str(payload.get("task_id") or "")
        try:
            before = tasks.ledger.get(task_id).to_dict()
            record, commit, snapshot = tasks.checkpoint_iteration(
                task_id=task_id,
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                iteration_id=str(payload.get("iteration_id") or ""),
                outcome=str(payload.get("outcome") or ""),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(400, "invalid_request", "authority_generation must be an integer")
        evidence = {
            "task_id": task_id,
            "iteration_id": payload.get("iteration_id"),
            "holder_id": record.holder_id,
            "authority_generation": record.authority_generation,
            "before": before,
            "after": record.to_dict(),
            "checkpoint_commit": commit,
            "git": snapshot.to_dict(),
            "outcome": payload.get("outcome"),
            "action_occurred": True,
        }
        try:
            durable = self.api.receipts.append_iteration_checkpoint(
                request_id=request_id,
                evidence=evidence,
            )
        except Exception:
            self._send(
                500,
                {
                    "protocol": PROTOCOL,
                    "request_id": request_id,
                    "action_occurred": True,
                    "receipt_persisted": False,
                    "task": record.to_dict(),
                    "checkpoint_commit": commit,
                    "error": {
                        "code": "receipt_persistence_failed",
                        "message": "iteration checkpoint occurred but durable receipt persistence failed",
                    },
                },
            )
            return
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "action_occurred": True,
                "receipt_persisted": True,
                "durable_receipt_id": durable["receipt_id"],
                "task": record.to_dict(),
                "checkpoint_commit": commit,
                "git": snapshot.to_dict(),
            },
        )

    def _handoff_offer(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "authority_generation", "recipient_id"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.handoff_offer(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                recipient_id=str(payload.get("recipient_id") or ""),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(400, "invalid_request", "authority_generation must be an integer")
        self._persist_task_transition(request_id=request_id, operation="handoff_offer", before=before, after=record.to_dict())

    def _handoff_respond(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "recipient_id", "accept", "lease_seconds"})
        if not isinstance(payload.get("accept"), bool):
            raise HouseMechanicAPIError(400, "invalid_request", "accept must be boolean")
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.handoff_respond(
                task_id=str(payload.get("task_id") or ""),
                recipient_id=str(payload.get("recipient_id") or ""),
                accept=bool(payload.get("accept")),
                lease_seconds=payload.get("lease_seconds"),
            )
        except TaskError as exc:
            self._raise_task(exc)
        self._persist_task_transition(request_id=request_id, operation="handoff_respond", before=before, after=record.to_dict())

    def _task_finish(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id", "authority_generation", "state", "reason"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.finish(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
                authority_generation=int(payload.get("authority_generation")),
                state=str(payload.get("state") or ""),
                reason=payload.get("reason"),
            )
        except (TaskError, TypeError, ValueError) as exc:
            if isinstance(exc, TaskError):
                self._raise_task(exc)
            raise HouseMechanicAPIError(400, "invalid_request", "authority_generation must be an integer")
        self._persist_task_transition(request_id=request_id, operation="finish", before=before, after=record.to_dict())

    def _task_cleanup(self, request_id: str) -> None:
        tasks = self._require_tasks()
        payload = self._json_body()
        self._require_exact_fields(payload, {"task_id", "holder_id"})
        try:
            before = tasks.ledger.get(str(payload.get("task_id") or "")).to_dict()
            record = tasks.cleanup(
                task_id=str(payload.get("task_id") or ""),
                holder_id=str(payload.get("holder_id") or ""),
            )
        except TaskError as exc:
            self._raise_task(exc)
        self._persist_task_transition(request_id=request_id, operation="cleanup", before=before, after=record.to_dict())

    def _inspect_receipt(self, request_id: str) -> None:
        payload = self._json_body()
        self._require_exact_fields(payload, {"receipt_id"})
        receipt_id = payload.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                "receipt_id must be a non-empty string",
            )
        receipt = self.api.receipts.get(receipt_id.strip())
        if receipt is None:
            raise HouseMechanicAPIError(
                404,
                "unknown_receipt",
                "receipt was not found",
            )
        self._send(
            200,
            {
                "protocol": PROTOCOL,
                "request_id": request_id,
                "receipt": receipt,
            },
        )

    @staticmethod
    def _require_exact_fields(payload: dict[str, Any], allowed: set[str]) -> None:
        unknown = set(payload) - allowed
        if unknown:
            raise HouseMechanicAPIError(
                400,
                "invalid_request",
                f"unknown fields: {sorted(unknown)}",
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
        receipt_file: Path,
        port: int = 8770,
        max_parallel: int = 1,
        health_timeout_seconds: float = 3.0,
        health_max_response_bytes: int = 65_536,
        lifecycle_health_wait_seconds: float = 10.0,
        lifecycle_stop_timeout_seconds: float = 5.0,
        repositories: RepositoryManifest | None = None,
        worktree_root: Path | None = None,
        task_state_dir: Path | None = None,
        deployment_state_dir: Path | None = None,
    ):
        if not 0 <= int(port) <= 65535:
            raise ValueError("House Mechanic port must be between 0 and 65535")
        if not 1 <= int(max_parallel) <= 16:
            raise ValueError("House Mechanic max_parallel must be between 1 and 16")
        if not 0.1 <= float(health_timeout_seconds) <= 30.0:
            raise ValueError("health timeout must be between 0.1 and 30 seconds")
        if not 1_024 <= int(health_max_response_bytes) <= 1_048_576:
            raise ValueError(
                "health response ceiling must be between 1024 and 1048576 bytes"
            )
        if not 0.1 <= float(lifecycle_health_wait_seconds) <= 120.0:
            raise ValueError(
                "lifecycle health wait must be between 0.1 and 120 seconds"
            )
        if not 0.1 <= float(lifecycle_stop_timeout_seconds) <= 30.0:
            raise ValueError(
                "lifecycle stop timeout must be between 0.1 and 30 seconds"
            )

        self.repo_root = repo_root.resolve()
        self.recipes = recipes
        self.services = services
        self.lifecycle_recipe_ids = {
            recipe_id
            for service in services.services.values()
            for recipe_id in (service.start_recipe, service.stop_recipe)
            if recipe_id is not None
        }
        self.token = read_token(token_file)
        self.receipts = ReceiptStore(receipt_file)
        self.processes = ProcessRegistry(receipt_file.parent / "process_state")
        self.lifecycle = LifecycleController(
            repo_root=self.repo_root,
            recipes=self.recipes,
            processes=self.processes,
            health_probe_timeout_seconds=health_timeout_seconds,
            health_wait_seconds=lifecycle_health_wait_seconds,
            stop_timeout_seconds=lifecycle_stop_timeout_seconds,
            health_max_response_bytes=health_max_response_bytes,
        )
        self.lifecycle_lock = threading.Lock()
        self.max_parallel = int(max_parallel)
        self.health_timeout_seconds = float(health_timeout_seconds)
        self.health_max_response_bytes = int(health_max_response_bytes)
        self.run_slots = threading.BoundedSemaphore(self.max_parallel)
        if (repositories is None) != (worktree_root is None):
            raise ValueError("repositories and worktree_root must be configured together")
        if repositories is None and (
            task_state_dir is not None or deployment_state_dir is not None
        ):
            raise ValueError(
                "task/deployment state paths require configured repositories"
            )
        if repositories is None:
            self.tasks = None
            self.deployments = None
        else:
            task_dir = task_state_dir or (receipt_file.parent / "task_state")
            self.tasks = TaskSupervisor(
                ledger=TaskLedger(task_dir),
                worktrees=WorktreeManager(
                    repo_root=self.repo_root,
                    repositories=repositories,
                    worktree_root=worktree_root,
                ),
            )
            if any(service.deployment is not None for service in services.services.values()):
                deployment_dir = deployment_state_dir or (receipt_file.parent / "deployment_state")
                self.deployments = DeploymentController(
                    tasks=self.tasks,
                    lifecycle=self.lifecycle,
                    ledger=DeploymentLedger(deployment_dir),
                )
            else:
                self.deployments = None
        super().__init__(("127.0.0.1", int(port)), _Handler)
        self.api = self

    def server_close(self) -> None:
        self.processes.terminate_all_for_shutdown()
        super().server_close()
