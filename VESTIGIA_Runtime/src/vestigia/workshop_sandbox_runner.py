from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import platform
import tempfile
from typing import Any

from .utils import new_id, sha256_text, stable_json, utc_now_iso
from .workshop_sandbox_backend import (
    BACKEND_ID,
    BACKEND_VERSION,
    PROFILE,
    SandboxLimits,
    _hash_id,
    _limits,
    backend_descriptor,
)
from .workshop_sandbox_process import (
    _harvest_output_files,
    _run_process,
    _validate_output_envelope,
)
from .workshop_sandbox_store import (
    _cleanup_staged_artifacts,
    _public_artifact_ref,
    _stage_artifact,
    ensure_schema,
)


_SCRIPT_ID = "vestigia.canonical.say-hi"
_SCRIPT_VERSION = 1

CANONICAL_SAY_HI_SOURCE = """from __future__ import annotations

import json
import sys

payload = json.load(sys.stdin)
name = str(payload[\"arguments\"].get(\"name\", \"friend\"))[:80]
json.dump(
    {
        \"schema_version\": \"vestigia.script-output.v0.1\",
        \"value\": {\"text\": f\"I made this machine make a machine say hi to {name}.\"},
        \"artifacts\": [],
        \"warnings\": [],
    },
    sys.stdout,
)
"""
CANONICAL_SAY_HI_HASH = sha256_text(CANONICAL_SAY_HI_SOURCE)


def _subject(script_id: str, script_version: int, script_hash: str) -> dict[str, Any]:
    return {
        "kind": "script",
        "id": script_id,
        "version": script_version,
        "content_hash": script_hash,
    }


def _grant(
    house: Any,
    *,
    subject: dict[str, Any],
    plan_hash: str,
    limits: SandboxLimits,
    interface: str,
    now: str,
) -> dict[str, Any]:
    compute = {
        "capability": "sandbox.local_compute",
        "scope": {
            "backend_id": BACKEND_ID,
            "profile": PROFILE,
            "network": "none_requested",
            "filesystem": "ephemeral_working_root",
        },
        "effects": ["compute", "artifact_write"],
        "outward_effect": "none",
        "cost_class": "local_low",
        "confirmation": "resident",
        "expires_at": None,
    }
    return {
        "schema_version": "vestigia.workshop-grant.v0.1",
        "subject": subject,
        "requested": [compute],
        "granted": [compute],
        "denied": [],
        "limits": {
            "wall_seconds": limits.wall_seconds,
            "tool_calls": 0,
            "provider_calls": 0,
            "outward_actions": 0,
            "image_generations": 0,
            "artifact_bytes": limits.artifact_bytes,
            "nested_depth": 0,
        },
        "approval": {
            "resident_required": True,
            "operator_required": False,
            "status": "approved",
            "resident_actor_id": _hash_id(house.resident_id),
            "approved_at": now,
            "expires_at": None,
            "plan_hash": plan_hash,
        },
        "scope_context": {
            "resident_id_hash": _hash_id(house.resident_id),
            "room_id_hash": _hash_id(house.room_id),
            "interface": interface[:80],
        },
    }


def _budget(limits: SandboxLimits, *, remaining: bool) -> dict[str, int]:
    return {
        "steps": 0 if remaining else 1,
        "tool_calls": 0,
        "provider_calls": 0,
        "outward_actions": 0,
        "image_generations": 0,
        "artifact_bytes": limits.artifact_bytes,
        "wall_seconds": 0 if remaining else limits.wall_seconds,
        "nested_depth": 0,
    }


def _safe_error(category: str, message: str) -> dict[str, Any]:
    return {
        "category": category,
        "safe_message": message,
        "diagnostic_code": f"workshop.{category}",
        "private_details_omitted": True,
    }


def execute_source(
    house: Any,
    *,
    source: str,
    script_id: str,
    script_version: int,
    arguments: dict[str, Any],
    context: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one already-authorized immutable source version.

    This is the internal seam for the future script shelf. The resident-facing v0.1
    capability calls it only with the bundled canonical source. Imported or inline
    source is not accepted through TOOL_ACTION.
    """

    ensure_schema(house)
    context = context or {}
    payload = payload or {}
    descriptor = backend_descriptor(house)
    if not descriptor["health"]["callable_now"]:
        raise PermissionError(str(descriptor["health"]["reason"] or "sandbox unavailable"))
    if not isinstance(arguments, dict):
        raise ValueError("sandbox arguments must be an object")
    limits = _limits(house, payload)
    script_hash = sha256_text(source)
    subject = _subject(script_id, script_version, script_hash)
    now = utc_now_iso()
    execution_id = new_id("workshop_exec")
    input_envelope = {
        "schema_version": "vestigia.script-input.v0.1",
        "execution_id": execution_id,
        "arguments": arguments,
        "mounts": [],
    }
    input_bytes = stable_json(input_envelope).encode("utf-8")
    if len(input_bytes) > limits.input_bytes:
        raise ValueError("sandbox input exceeded the configured byte ceiling")
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    plan_basis = {
        "schema_version": "vestigia.workshop-plan.v0.1",
        "execution_id": execution_id,
        "subject": subject,
        "input_hash": input_hash,
        "backend": {
            "id": descriptor["backend_id"],
            "version": descriptor["version"],
            "profile": PROFILE,
            "guarantees_hash": descriptor["guarantees_hash"],
        },
        "limits": asdict(limits),
        "outward_effect": "none",
    }
    plan_hash = sha256_text(stable_json(plan_basis))
    grant = _grant(
        house,
        subject=subject,
        plan_hash=plan_hash,
        limits=limits,
        interface=str(context.get("interface") or "runtime"),
        now=now,
    )
    grant_hash = sha256_text(stable_json(grant))
    started_at = utc_now_iso()
    trace: list[dict[str, Any]] = [
        {
            "sequence": 1,
            "event_type": "plan_created",
            "status": "succeeded",
            "created_at": now,
            "outward_effect": "none",
            "receipt_ids": [],
            "message": "Immutable local-only sandbox plan created.",
        },
        {
            "sequence": 2,
            "event_type": "execution_started",
            "status": "running",
            "created_at": started_at,
            "outward_effect": "none",
            "receipt_ids": [],
            "message": "Fresh local-process working root started.",
        },
    ]
    output_envelope: dict[str, Any] | None = None
    harvested: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    staged_artifacts: list[dict[str, Any]] = []
    output_refs: list[dict[str, Any]] = []
    warnings = [
        "local_process is not hostile-code isolation",
        "network denial is not enforced by this backend",
        "host filesystem denial is not enforced by this backend",
    ]
    tmp_parent = house.home / "workshop" / "tmp"
    with tempfile.TemporaryDirectory(prefix="sandbox-", dir=tmp_parent) as temporary:
        root = Path(temporary)
        for name in ("input", "output", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        process_result = _run_process(
            source=source,
            input_bytes=input_bytes,
            root=root,
            limits=limits,
        )
        if process_result.status == "succeeded":
            try:
                output_envelope = _validate_output_envelope(process_result.stdout)
            except ValueError as exc:
                process_result.status = "failed"
                process_result.error_category = "malformed_result"
                process_result.safe_message = str(exc)
        if process_result.status == "succeeded" and output_envelope is not None:
            try:
                harvested = _harvest_output_files(
                    root,
                    limits,
                    list(output_envelope.get("artifacts", [])),
                )
            except ValueError as exc:
                process_result.status = "failed"
                process_result.error_category = "artifact_rejected"
                process_result.safe_message = str(exc)
        if process_result.status == "succeeded" and output_envelope is not None:
            try:
                envelope_bytes = stable_json(output_envelope).encode("utf-8")
                staged_artifacts.append(
                    _stage_artifact(
                        house,
                        kind="script_value",
                        media_type="application/json",
                        data=envelope_bytes,
                        suffix=".json",
                        now=utc_now_iso(),
                    )
                )
                for item in harvested:
                    suffix = Path(str(item["relative_path"])).suffix or ".bin"
                    staged_artifacts.append(
                        _stage_artifact(
                            house,
                            kind="script_file",
                            media_type=str(item["media_type"]),
                            data=bytes(item["data"]),
                            suffix=suffix,
                            now=utc_now_iso(),
                        )
                    )
                output_refs = [_public_artifact_ref(item) for item in staged_artifacts]
            except Exception:
                _cleanup_staged_artifacts(staged_artifacts)
                raise
    completed_at = utc_now_iso()
    if process_result.status != "succeeded":
        error = _safe_error(
            process_result.error_category or "unknown",
            process_result.safe_message or "The local workshop process failed.",
        )
    output_hash = (
        sha256_text(stable_json(output_envelope)) if output_envelope is not None else None
    )
    status = process_result.status
    trace.append(
        {
            "sequence": 3,
            "event_type": "execution_completed",
            "status": status,
            "created_at": completed_at,
            "outward_effect": "none",
            "receipt_ids": [],
            "output_refs": [item["object_id"] for item in output_refs],
            "message": (
                "Sandbox output validated and collected as private workshop artifacts."
                if status == "succeeded"
                else (error or {}).get("safe_message", "Sandbox execution failed.")
            ),
        }
    )
    execution = {
        "schema_version": "vestigia.workshop-execution.v0.1",
        "execution_id": execution_id,
        "subject": subject,
        "parent": None,
        "plan_hash": plan_hash,
        "resident_id_hash": _hash_id(house.resident_id),
        "room_id_hash": _hash_id(house.room_id),
        "status": status,
        "sandbox": {
            "backend_id": descriptor["backend_id"],
            "backend_version": descriptor["version"],
            "profile": PROFILE,
            "guarantees_hash": descriptor["guarantees_hash"],
            "interpreter_environment_id": (
                f"{platform.python_implementation().casefold()}-{platform.python_version()}-isolated"
            ),
        },
        "inputs": {"arguments": {"payload_hash": input_hash}, "objects": [], "mounts": []},
        "grant": grant,
        "budgets": {
            "initial": _budget(limits, remaining=False),
            "remaining": _budget(limits, remaining=True),
        },
        "trace": trace,
        "checkpoint": None,
        "outward_effect": "none",
        "created_at": now,
        "started_at": started_at,
        "completed_at": completed_at,
        "expires_at": None,
        "warnings": warnings,
    }
    receipt_id = new_id("workshop_receipt")
    receipt = {
        "schema_version": "vestigia.workshop-receipt.v0.1",
        "receipt_id": receipt_id,
        "action": "sandbox.run",
        "status": status,
        "execution_id": execution_id,
        "step_id": None,
        "subject": subject,
        "plan_hash": plan_hash,
        "grant_hash": grant_hash,
        "resident_id_hash": _hash_id(house.resident_id),
        "room_id_hash": _hash_id(house.room_id),
        "input_refs": [],
        "output_refs": [
            {
                "object_id": item["object_id"],
                "content_hash": item["content_hash"],
                "privacy": "private",
                "media_type": item["media_type"],
            }
            for item in output_refs
        ],
        "child_receipt_ids": [],
        "outward_effect": "none",
        "result_complete": status == "succeeded",
        "retry": {
            "safe": True,
            "requires_reconciliation": False,
            "reason": "Local-only immutable input and no broker or outward effects.",
            "maximum_additional_attempts": 1,
        },
        "usage": {
            "wall_ms": process_result.wall_ms,
            "cpu_ms": None,
            "peak_memory_bytes": None,
            "tool_calls": 0,
            "provider_calls": 0,
            "outward_actions": 0,
            "input_bytes": len(input_bytes),
            "output_bytes": len(process_result.stdout) + len(process_result.stderr),
            "artifacts": len(output_refs),
        },
        "backend": {
            "id": descriptor["backend_id"],
            "version": descriptor["version"],
            "profile": PROFILE,
            "guarantees_hash": descriptor["guarantees_hash"],
        },
        "error": error,
        "warnings": warnings + list((output_envelope or {}).get("warnings", [])),
        "created_at": now,
        "completed_at": completed_at,
    }
    trace[-1]["receipt_ids"] = [receipt_id]
    execution_json = stable_json(execution)
    receipt_json = stable_json(receipt)
    try:
        with house.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO workshop_sandbox_executions
                (id, resident_id, room_id, script_id, script_version, script_hash,
                 plan_hash, grant_hash, backend_id, backend_version, profile,
                 guarantees_hash, status, input_hash, output_hash, exit_code,
                 timed_out, output_limited, wall_ms, error_category, safe_message,
                 execution_json, created_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    house.resident_id,
                    house.room_id,
                    script_id,
                    script_version,
                    script_hash,
                    plan_hash,
                    grant_hash,
                    descriptor["backend_id"],
                    descriptor["version"],
                    PROFILE,
                    descriptor["guarantees_hash"],
                    status,
                    input_hash,
                    output_hash,
                    process_result.exit_code,
                    int(process_result.timed_out),
                    int(process_result.output_limited),
                    process_result.wall_ms,
                    process_result.error_category,
                    process_result.safe_message,
                    execution_json,
                    now,
                    started_at,
                    completed_at,
                ),
            )
            for item in staged_artifacts:
                connection.execute(
                    """
                    INSERT INTO workshop_artifacts
                    (id, resident_id, room_id, execution_id, kind, media_type,
                     content_hash, size_bytes, storage_path, privacy, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'private', 'active', ?)
                    """,
                    (
                        item["object_id"],
                        house.resident_id,
                        house.room_id,
                        execution_id,
                        item["kind"],
                        item["media_type"],
                        item["content_hash"],
                        item["size_bytes"],
                        item["storage_path"],
                        item["created_at"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO workshop_receipts
                (id, resident_id, room_id, execution_id, action, status, receipt_json, created_at)
                VALUES (?, ?, ?, ?, 'sandbox.run', ?, ?, ?)
                """,
                (
                    receipt_id,
                    house.resident_id,
                    house.room_id,
                    execution_id,
                    status,
                    receipt_json,
                    now,
                ),
            )
    except Exception:
        _cleanup_staged_artifacts(staged_artifacts)
        raise
    return {
        "mode": "run",
        "status": status,
        "execution_id": execution_id,
        "workshop_receipt_id": receipt_id,
        "subject": subject,
        "plan_hash": plan_hash,
        "grant_hash": grant_hash,
        "backend": descriptor,
        "value": (output_envelope or {}).get("value"),
        "requested_follow_up": (output_envelope or {}).get("requested_follow_up", []),
        "follow_up_executed": False,
        "artifacts": output_refs,
        "error": error,
        "usage": receipt["usage"],
        "outward_effect": "none",
        "authority_changed": False,
        "memory_adopted": False,
        "published": False,
    }
