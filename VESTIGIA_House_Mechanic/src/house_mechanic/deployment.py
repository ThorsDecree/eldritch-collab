from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import threading
from typing import Any
import uuid

from .lifecycle import LifecycleController, LifecycleError
from .service_model import Service
from .tasking import TaskError, TaskSupervisor


DEPLOYMENT_SCHEMA = "vestigia.house-mechanic-deployment.v0.1"
_ACTIVE_STATES = {
    "candidate_running",
    "last_known_good_running",
    "candidate_unverified",
    "rollback_failed",
}
_SERVICE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class DeploymentError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DeploymentRecord:
    service_id: str
    repository_id: str
    state: str
    last_known_good_commit: str | None = None
    last_known_good_task_id: str | None = None
    active_deployment_id: str | None = None
    active_kind: str | None = None
    active_commit: str | None = None
    active_task_id: str | None = None
    active_generation_id: str | None = None
    active_worktree_path: str | None = None
    suspended_from_state: str | None = None
    suspended_at: str | None = None
    cleanup_pending: list[dict[str, Any]] = field(default_factory=list)
    last_outcome: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": DEPLOYMENT_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeploymentRecord":
        if data.get("schema_version") != DEPLOYMENT_SCHEMA:
            raise ValueError("unsupported deployment schema")
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        payload = {name: data[name] for name in fields if name in data}
        payload.setdefault("cleanup_pending", [])
        return cls(**payload)


class DeploymentLedger:
    """Durable deployment references without durable process ownership."""

    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._suspend_restart_authority()

    def _path(self, service_id: str) -> Path:
        if not _SERVICE_ID.fullmatch(service_id):
            raise DeploymentError("invalid_service_id", "service_id is outside the bounded grammar")
        return self.directory / f"{service_id}.json"

    def _write(self, record: DeploymentRecord) -> None:
        path = self._path(record.service_id)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        raw = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _suspend_restart_authority(self) -> None:
        for path in self.directory.glob("*.json"):
            try:
                record = DeploymentRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if record.state in _ACTIVE_STATES and record.active_generation_id is not None:
                now = datetime.now(UTC).isoformat()
                record.suspended_from_state = record.state
                record.suspended_at = now
                record.state = "suspended_unverified"
                record.last_outcome = "supervisor_restart_lost_process_authority"
                record.updated_at = now
                self._write(record)

    def get(self, service_id: str) -> DeploymentRecord | None:
        with self._lock:
            path = self._path(service_id)
            if not path.is_file():
                return None
            return DeploymentRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def get_or_create(self, service: Service) -> DeploymentRecord:
        if service.deployment is None:
            raise DeploymentError("deployment_not_configured", "service has no deployment binding")
        with self._lock:
            existing = self.get(service.id)
            if existing is not None:
                if existing.repository_id != service.deployment.repository_id:
                    raise DeploymentError(
                        "deployment_binding_changed",
                        "durable deployment repository does not match current service manifest",
                    )
                return existing
            record = DeploymentRecord(
                service_id=service.id,
                repository_id=service.deployment.repository_id,
                state="idle",
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._write(record)
            return record

    def save(self, record: DeploymentRecord) -> DeploymentRecord:
        with self._lock:
            self._write(record)
            return record

    def list(self) -> list[DeploymentRecord]:
        result: list[DeploymentRecord] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                result.append(DeploymentRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return result


class DeploymentController:
    def __init__(
        self,
        *,
        tasks: TaskSupervisor,
        lifecycle: LifecycleController,
        ledger: DeploymentLedger,
    ) -> None:
        self.tasks = tasks
        self.lifecycle = lifecycle
        self.ledger = ledger
        self._lock = threading.RLock()

    @staticmethod
    def _require_deployable(service: Service) -> None:
        if not service.mechanic_owned or service.deployment is None:
            raise DeploymentError(
                "deployment_not_configured",
                "service is not configured for mechanic-owned deployment",
            )
        if service.start_recipe is None or service.health is None:
            raise DeploymentError(
                "deployment_contract_incomplete",
                "deployable service requires start_recipe and health",
            )

    @staticmethod
    def _deployment_id() -> str:
        return f"hm_deploy_{uuid.uuid4().hex}"

    def _queue_cleanup(
        self,
        record: DeploymentRecord,
        *,
        deployment_id: str | None,
        worktree_path: str,
        expected_commit: str | None,
        safe_basis: str,
        error: TaskError,
    ) -> dict[str, Any]:
        existing = next(
            (
                row
                for row in record.cleanup_pending
                if row.get("worktree_path") == worktree_path
            ),
            None,
        )
        now = datetime.now(UTC).isoformat()
        if existing is None:
            existing = {
                "cleanup_id": f"hm_cleanup_{uuid.uuid4().hex}",
                "deployment_id": deployment_id,
                "worktree_path": worktree_path,
                "expected_commit": expected_commit,
                "safe_basis": safe_basis,
                "created_at": now,
                "attempts": 0,
            }
            record.cleanup_pending.append(existing)
        existing["attempts"] = int(existing.get("attempts", 0)) + 1
        existing["last_attempt_at"] = now
        existing["last_error"] = {"code": error.code, "message": error.message}
        record.updated_at = now
        self.ledger.save(record)
        return dict(existing)

    def _cleanup_path(
        self,
        record: DeploymentRecord,
        *,
        deployment_id: str | None,
        worktree_path: str,
        expected_commit: str | None,
        safe_basis: str,
    ) -> dict[str, Any]:
        path = Path(worktree_path)
        try:
            self.tasks.worktrees.remove_detached(
                record.repository_id,
                path,
                expected_commit=expected_commit,
            )
            record.cleanup_pending = [
                row
                for row in record.cleanup_pending
                if row.get("worktree_path") != worktree_path
            ]
            record.updated_at = datetime.now(UTC).isoformat()
            self.ledger.save(record)
            return {
                "removed": True,
                "path_present": path.exists(),
                "worktree_path": worktree_path,
                "expected_commit": expected_commit,
                "safe_basis": safe_basis,
            }
        except TaskError as exc:
            pending = self._queue_cleanup(
                record,
                deployment_id=deployment_id,
                worktree_path=worktree_path,
                expected_commit=expected_commit,
                safe_basis=safe_basis,
                error=exc,
            )
            return {
                "removed": False,
                "path_present": path.exists(),
                "worktree_path": worktree_path,
                "expected_commit": expected_commit,
                "safe_basis": safe_basis,
                "cleanup_pending": pending,
                "error": {"code": exc.code, "message": exc.message},
            }

    def _cleanup_checkout(
        self,
        record: DeploymentRecord,
        *,
        safe_basis: str,
    ) -> dict[str, Any] | None:
        if not record.active_worktree_path:
            return None
        return self._cleanup_path(
            record,
            deployment_id=record.active_deployment_id,
            worktree_path=record.active_worktree_path,
            expected_commit=record.active_commit,
            safe_basis=safe_basis,
        )

    def _materialize(
        self,
        *,
        service: Service,
        commit: str,
    ) -> tuple[str, Path]:
        assert service.deployment is not None
        deployment_id = self._deployment_id()
        try:
            path = self.tasks.worktrees.materialize_detached(
                repository_id=service.deployment.repository_id,
                service_id=service.id,
                deployment_id=deployment_id,
                commit=commit,
            )
        except TaskError as exc:
            raise DeploymentError(exc.code, exc.message) from exc
        return deployment_id, path

    def _save_active(
        self,
        record: DeploymentRecord,
        *,
        state: str,
        deployment_id: str,
        kind: str,
        commit: str,
        task_id: str | None,
        generation_id: str | None,
        worktree: Path,
        outcome: str,
    ) -> DeploymentRecord:
        record.state = state
        record.active_deployment_id = deployment_id
        record.active_kind = kind
        record.active_commit = commit
        record.active_task_id = task_id
        record.active_generation_id = generation_id
        record.active_worktree_path = str(worktree)
        record.suspended_from_state = None
        record.suspended_at = None
        record.last_outcome = outcome
        record.updated_at = datetime.now(UTC).isoformat()
        return self.ledger.save(record)

    def _clear_active(self, record: DeploymentRecord, *, state: str, outcome: str) -> DeploymentRecord:
        record.state = state
        record.active_deployment_id = None
        record.active_kind = None
        record.active_commit = None
        record.active_task_id = None
        record.active_generation_id = None
        record.active_worktree_path = None
        record.suspended_from_state = None
        record.suspended_at = None
        record.last_outcome = outcome
        record.updated_at = datetime.now(UTC).isoformat()
        return self.ledger.save(record)

    def status(self, service: Service) -> dict[str, Any]:
        self._require_deployable(service)
        assert service.deployment is not None
        record = self.ledger.get(service.id)
        if record is None:
            record = DeploymentRecord(
                service_id=service.id,
                repository_id=service.deployment.repository_id,
                state="idle",
                updated_at="",
            )
        return {
            "deployment": record.to_dict(),
            "process": self.lifecycle.processes.status(service).to_dict(),
        }

    def reconciliation(
        self,
        service: Service,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        self._require_deployable(service)
        assert service.deployment is not None
        record = self.ledger.get(service.id)
        if record is None:
            record = DeploymentRecord(
                service_id=service.id,
                repository_id=service.deployment.repository_id,
                state="idle",
                updated_at="",
            )
        process = self.lifecycle.processes.status(service)
        health = self.lifecycle.observe_health(service, request_id=request_id)

        checkout: dict[str, Any] | None = None
        if record.active_worktree_path:
            path = Path(record.active_worktree_path)
            checkout = {
                "worktree_path": record.active_worktree_path,
                "path_present": path.exists(),
                "expected_commit": record.active_commit,
            }
            if path.exists():
                try:
                    snapshot = self.tasks.worktrees.snapshot(path)
                    checkout.update(
                        {
                            "observed_commit": snapshot.head,
                            "commit_matches": (
                                record.active_commit is not None
                                and snapshot.head == record.active_commit
                            ),
                            "dirty": snapshot.dirty,
                            "status_lines": list(snapshot.status_lines),
                        }
                    )
                except TaskError as exc:
                    checkout["inspection_error"] = {
                        "code": exc.code,
                        "message": exc.message,
                    }

        if record.state == "suspended_unverified":
            if health.healthy:
                interpretation = (
                    "declared endpoint is healthy but no process is owned by this "
                    "supervisor instance; pre-restart or external process may still be serving"
                )
            else:
                interpretation = (
                    "declared endpoint is not healthy, but process death and checkout "
                    "release are not proven after supervisor restart"
                )
            operator_boundary_required = True
        else:
            interpretation = "no restart-suspension reconciliation boundary is active"
            operator_boundary_required = False

        return {
            "operation": "reconcile",
            "action_occurred": False,
            "verified": record.state != "suspended_unverified",
            "outcome": (
                "operator_boundary_required"
                if operator_boundary_required
                else "reconciliation_not_required"
            ),
            "deployment": record.to_dict(),
            "process": process.to_dict(),
            "health": health.to_dict(),
            "active_checkout": checkout,
            "cleanup_pending_count": len(record.cleanup_pending),
            "operator_boundary_required": operator_boundary_required,
            "automatic_process_adoption": False,
            "automatic_active_checkout_cleanup": False,
            "interpretation": interpretation,
        }

    def retry_cleanup(
        self,
        service: Service,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        self._require_deployable(service)
        allowed_basis = {
            "verified_stop",
            "owned_process_exited",
            "launch_failed_before_verified_service",
        }
        with self._lock:
            record = self.ledger.get_or_create(service)
            before = record.to_dict()
            if not record.cleanup_pending:
                return {
                    "operation": "cleanup_retry",
                    "action_occurred": False,
                    "verified": True,
                    "outcome": "cleanup_complete",
                    "removed_count": 0,
                    "results": [],
                    "before": before,
                    "after": record.to_dict(),
                }
            results: list[dict[str, Any]] = []
            retained: list[dict[str, Any]] = []
            removed_count = 0
            state_changed = False
            for pending in list(record.cleanup_pending):
                path_text = str(pending.get("worktree_path") or "")
                if not path_text:
                    retained.append(pending)
                    results.append(
                        {
                            "cleanup_id": pending.get("cleanup_id"),
                            "removed": False,
                            "outcome": "invalid_pending_record",
                        }
                    )
                    continue
                if (
                    record.active_worktree_path is not None
                    and path_text == record.active_worktree_path
                ):
                    retained.append(pending)
                    results.append(
                        {
                            "cleanup_id": pending.get("cleanup_id"),
                            "removed": False,
                            "outcome": "active_checkout_preserved",
                        }
                    )
                    continue
                safe_basis = str(pending.get("safe_basis") or "")
                if safe_basis not in allowed_basis:
                    retained.append(pending)
                    results.append(
                        {
                            "cleanup_id": pending.get("cleanup_id"),
                            "removed": False,
                            "outcome": "unrecognized_safe_basis",
                        }
                    )
                    continue

                try:
                    self.tasks.worktrees.remove_detached(
                        record.repository_id,
                        Path(path_text),
                        expected_commit=pending.get("expected_commit"),
                    )
                    removed_count += 1
                    state_changed = True
                    results.append(
                        {
                            "cleanup_id": pending.get("cleanup_id"),
                            "removed": True,
                            "outcome": "removed",
                            "worktree_path": path_text,
                            "safe_basis": safe_basis,
                        }
                    )
                except TaskError as exc:
                    pending["attempts"] = int(pending.get("attempts", 0)) + 1
                    pending["last_attempt_at"] = datetime.now(UTC).isoformat()
                    state_changed = True
                    pending["last_error"] = {
                        "code": exc.code,
                        "message": exc.message,
                    }
                    retained.append(pending)
                    results.append(
                        {
                            "cleanup_id": pending.get("cleanup_id"),
                            "removed": False,
                            "outcome": "retry_failed",
                            "worktree_path": path_text,
                            "safe_basis": safe_basis,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                            },
                        }
                    )

            if state_changed:
                record.cleanup_pending = retained
                record.updated_at = datetime.now(UTC).isoformat()
                self.ledger.save(record)
            return {
                "operation": "cleanup_retry",
                "action_occurred": state_changed,
                "verified": len(retained) == 0,
                "outcome": (
                    "cleanup_complete"
                    if not retained
                    else "cleanup_pending_remains"
                ),
                "removed_count": removed_count,
                "results": results,
                "before": before,
                "after": record.to_dict(),
            }

    def _start_last_known_good(
        self,
        *,
        service: Service,
        record: DeploymentRecord,
        request_id: str,
        trigger: str,
    ) -> dict[str, Any]:
        commit = record.last_known_good_commit
        if commit is None:
            return {
                "attempted": False,
                "verified": False,
                "outcome": "last_known_good_missing",
            }
        deployment_id, worktree = self._materialize(service=service, commit=commit)
        try:
            started = self.lifecycle.start(
                service,
                request_id=request_id,
                source_root=worktree,
            )
        except LifecycleError as exc:
            cleanup = self._cleanup_path(
                record,
                deployment_id=deployment_id,
                worktree_path=str(worktree),
                expected_commit=commit,
                safe_basis="launch_failed_before_verified_service",
            )
            record.state = "rollback_failed"
            record.last_outcome = "rollback_start_blocked"
            record.updated_at = datetime.now(UTC).isoformat()
            self.ledger.save(record)
            return {
                "attempted": True,
                "verified": False,
                "outcome": "rollback_start_blocked",
                "deployment_id": deployment_id,
                "commit": commit,
                "worktree_path": str(worktree),
                "cleanup": cleanup,
                "error": {"code": exc.code, "message": exc.message},
            }

        generation_id = started.get("generation_id")
        if started.get("verified"):
            self._save_active(
                record,
                state="last_known_good_running",
                deployment_id=deployment_id,
                kind="last_known_good",
                commit=commit,
                task_id=record.last_known_good_task_id,
                generation_id=str(generation_id) if generation_id else None,
                worktree=worktree,
                outcome=f"rollback_verified:{trigger}",
            )
            return {
                "attempted": True,
                "verified": True,
                "outcome": "rolled_back_healthy",
                "deployment_id": deployment_id,
                "commit": commit,
                "worktree_path": str(worktree),
                "start": started,
            }

        self._save_active(
            record,
            state="rollback_failed",
            deployment_id=deployment_id,
            kind="last_known_good",
            commit=commit,
            task_id=record.last_known_good_task_id,
            generation_id=str(generation_id) if generation_id else None,
            worktree=worktree,
            outcome="rollback_started_unverified",
        )
        return {
            "attempted": True,
            "verified": False,
            "outcome": "rollback_started_unverified",
            "deployment_id": deployment_id,
            "commit": commit,
            "worktree_path": str(worktree),
            "start": started,
        }

    def deploy_candidate(
        self,
        service: Service,
        *,
        request_id: str,
        task_id: str,
        holder_id: str,
        authority_generation: int,
        expected_generation_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_deployable(service)
        assert service.deployment is not None
        with self._lock:
            record = self.ledger.get_or_create(service)
            if record.state == "suspended_unverified":
                raise DeploymentError(
                    "deployment_suspended_unverified",
                    "supervisor restart lost process authority; deployment requires operator reconciliation",
                )
            try:
                task, snapshot = self.tasks.deployment_candidate_source(
                    task_id=task_id,
                    holder_id=holder_id,
                    authority_generation=authority_generation,
                    repository_id=service.deployment.repository_id,
                )
            except TaskError as exc:
                raise DeploymentError(exc.code, exc.message) from exc

            before_record = record.to_dict()
            before_process = self.lifecycle.processes.status(service)
            stopped_current = None
            cleanup_previous = None
            if before_process.state == "running":
                if not expected_generation_id:
                    raise DeploymentError(
                        "current_generation_required",
                        "candidate deployment requires the exact currently owned generation",
                    )
                if before_process.generation_id != expected_generation_id:
                    raise DeploymentError(
                        "generation_mismatch",
                        "expected_generation_id is not the currently owned generation",
                    )
                stopped_current = self.lifecycle.stop(
                    service,
                    request_id=request_id,
                    generation_id=expected_generation_id,
                )
                if not stopped_current.get("verified"):
                    return {
                        "operation": "deploy_candidate",
                        "action_occurred": bool(stopped_current.get("action_occurred")),
                        "verified": False,
                        "outcome": "current_stop_unverified",
                        "before": before_record,
                        "task": task.to_dict(),
                        "candidate_commit": snapshot.head,
                        "stop_current": stopped_current,
                        "after": record.to_dict(),
                    }
                if record.active_generation_id == expected_generation_id:
                    cleanup_previous = self._cleanup_checkout(record, safe_basis="verified_stop")
                    self._clear_active(record, state="idle", outcome="previous_generation_stopped")
            elif before_process.state == "exited" and record.active_worktree_path:
                cleanup_previous = self._cleanup_checkout(
                    record,
                    safe_basis="owned_process_exited",
                )
                self._clear_active(record, state="idle", outcome="previous_generation_already_exited")

            deployment_id, worktree = self._materialize(
                service=service,
                commit=snapshot.head,
            )

            try:
                started = self.lifecycle.start(
                    service,
                    request_id=request_id,
                    source_root=worktree,
                )
            except LifecycleError as exc:
                candidate_cleanup = self._cleanup_path(
                    record,
                    deployment_id=deployment_id,
                    worktree_path=str(worktree),
                    expected_commit=snapshot.head,
                    safe_basis="launch_failed_before_verified_service",
                )
                rollback = self._start_last_known_good(
                    service=service,
                    record=record,
                    request_id=request_id,
                    trigger="candidate_start_blocked",
                )
                outcome = (
                    "candidate_start_blocked_rolled_back"
                    if rollback.get("verified")
                    else "candidate_start_blocked"
                )
                record.last_outcome = outcome
                record.updated_at = datetime.now(UTC).isoformat()
                self.ledger.save(record)
                return {
                    "operation": "deploy_candidate",
                    "action_occurred": True,
                    "verified": False,
                    "outcome": outcome,
                    "before": before_record,
                    "task": task.to_dict(),
                    "candidate_commit": snapshot.head,
                    "deployment_id": deployment_id,
                    "candidate_start_error": {"code": exc.code, "message": exc.message},
                    "candidate_cleanup": candidate_cleanup,
                    "stop_current": stopped_current,
                    "cleanup_previous": cleanup_previous,
                    "rollback": rollback,
                    "after": record.to_dict(),
                }

            generation_id = str(started.get("generation_id") or "")
            if started.get("verified"):
                self._save_active(
                    record,
                    state="candidate_running",
                    deployment_id=deployment_id,
                    kind="candidate",
                    commit=snapshot.head,
                    task_id=task.task_id,
                    generation_id=generation_id or None,
                    worktree=worktree,
                    outcome="candidate_running_healthy",
                )
                return {
                    "operation": "deploy_candidate",
                    "action_occurred": True,
                    "verified": True,
                    "outcome": "candidate_running_healthy",
                    "before": before_record,
                    "task": task.to_dict(),
                    "candidate_commit": snapshot.head,
                    "deployment_id": deployment_id,
                    "stop_current": stopped_current,
                    "cleanup_previous": cleanup_previous,
                    "start": started,
                    "after": record.to_dict(),
                }

            candidate_stop = None
            if generation_id:
                try:
                    candidate_stop = self.lifecycle.stop(
                        service,
                        request_id=request_id,
                        generation_id=generation_id,
                    )
                except LifecycleError as exc:
                    candidate_stop = {
                        "verified": False,
                        "outcome": "candidate_stop_error",
                        "error": {"code": exc.code, "message": exc.message},
                    }
            candidate_cleanup = None
            if candidate_stop is not None and candidate_stop.get("verified"):
                candidate_cleanup = self._cleanup_path(
                    record,
                    deployment_id=deployment_id,
                    worktree_path=str(worktree),
                    expected_commit=snapshot.head,
                    safe_basis="verified_stop",
                )
                self._clear_active(record, state="idle", outcome="candidate_unhealthy_stopped")
            else:
                self._save_active(
                    record,
                    state="candidate_unverified",
                    deployment_id=deployment_id,
                    kind="candidate",
                    commit=snapshot.head,
                    task_id=task.task_id,
                    generation_id=generation_id or None,
                    worktree=worktree,
                    outcome="candidate_unhealthy_stop_unverified",
                )
                return {
                    "operation": "deploy_candidate",
                    "action_occurred": True,
                    "verified": False,
                    "outcome": "candidate_unhealthy_stop_unverified",
                    "before": before_record,
                    "task": task.to_dict(),
                    "candidate_commit": snapshot.head,
                    "deployment_id": deployment_id,
                    "start": started,
                    "candidate_stop": candidate_stop,
                    "after": record.to_dict(),
                }

            rollback = self._start_last_known_good(
                service=service,
                record=record,
                request_id=request_id,
                trigger="candidate_unhealthy",
            )
            outcome = (
                "candidate_unhealthy_rolled_back"
                if rollback.get("verified")
                else "candidate_unhealthy_no_verified_rollback"
            )
            record.last_outcome = outcome
            record.updated_at = datetime.now(UTC).isoformat()
            self.ledger.save(record)
            return {
                "operation": "deploy_candidate",
                "action_occurred": True,
                "verified": False,
                "outcome": outcome,
                "before": before_record,
                "task": task.to_dict(),
                "candidate_commit": snapshot.head,
                "deployment_id": deployment_id,
                "stop_current": stopped_current,
                "cleanup_previous": cleanup_previous,
                "start": started,
                "candidate_stop": candidate_stop,
                "candidate_cleanup": candidate_cleanup,
                "rollback": rollback,
                "after": record.to_dict(),
            }

    def promote(
        self,
        service: Service,
        *,
        request_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        self._require_deployable(service)
        with self._lock:
            record = self.ledger.get_or_create(service)
            if record.state != "candidate_running" or record.active_kind != "candidate":
                raise DeploymentError("candidate_not_running", "promotion requires a running candidate")
            if record.active_generation_id != generation_id:
                raise DeploymentError("generation_mismatch", "promotion generation does not match active candidate")
            verification = self.lifecycle.verify_running(
                service,
                request_id=request_id,
                generation_id=generation_id,
            )
            if not verification.get("verified"):
                raise DeploymentError("candidate_not_healthy", "candidate is not currently verified healthy")
            before = record.to_dict()
            record.last_known_good_commit = record.active_commit
            record.last_known_good_task_id = record.active_task_id
            record.active_kind = "last_known_good"
            record.state = "last_known_good_running"
            record.last_outcome = "promoted_last_known_good"
            record.updated_at = datetime.now(UTC).isoformat()
            self.ledger.save(record)
            return {
                "operation": "promote",
                "action_occurred": True,
                "verified": True,
                "outcome": "promoted_last_known_good",
                "before": before,
                "verification": verification,
                "after": record.to_dict(),
            }

    def rollback(
        self,
        service: Service,
        *,
        request_id: str,
        expected_generation_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_deployable(service)
        with self._lock:
            record = self.ledger.get_or_create(service)
            if record.state == "suspended_unverified":
                raise DeploymentError(
                    "deployment_suspended_unverified",
                    "supervisor restart lost process authority; rollback requires operator reconciliation",
                )
            if record.last_known_good_commit is None:
                raise DeploymentError("last_known_good_missing", "no last-known-good commit is recorded")
            before = record.to_dict()
            process = self.lifecycle.processes.status(service)
            stop_current = None
            cleanup_current = None
            if process.state == "running":
                if not expected_generation_id:
                    raise DeploymentError(
                        "current_generation_required",
                        "rollback requires the exact currently owned generation",
                    )
                if process.generation_id != expected_generation_id:
                    raise DeploymentError(
                        "generation_mismatch",
                        "expected_generation_id is not the currently owned generation",
                    )
                stop_current = self.lifecycle.stop(
                    service,
                    request_id=request_id,
                    generation_id=expected_generation_id,
                )
                if not stop_current.get("verified"):
                    return {
                        "operation": "rollback",
                        "action_occurred": bool(stop_current.get("action_occurred")),
                        "verified": False,
                        "outcome": "current_stop_unverified",
                        "before": before,
                        "stop_current": stop_current,
                        "after": record.to_dict(),
                    }
                if record.active_generation_id == expected_generation_id:
                    cleanup_current = self._cleanup_checkout(record, safe_basis="verified_stop")
                    self._clear_active(record, state="idle", outcome="rollback_current_stopped")
            elif process.state == "exited" and record.active_worktree_path:
                cleanup_current = self._cleanup_checkout(
                    record,
                    safe_basis="owned_process_exited",
                )
                self._clear_active(record, state="idle", outcome="rollback_current_already_exited")

            rollback = self._start_last_known_good(
                service=service,
                record=record,
                request_id=request_id,
                trigger="explicit",
            )
            return {
                "operation": "rollback",
                "action_occurred": True,
                "verified": bool(rollback.get("verified")),
                "outcome": rollback.get("outcome"),
                "before": before,
                "stop_current": stop_current,
                "cleanup_current": cleanup_current,
                "rollback": rollback,
                "after": record.to_dict(),
            }
