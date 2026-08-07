from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .utils import new_id, sha256_text, stable_json, utc_now_iso
from .workshop_sandbox_backend import _limits, backend_descriptor
from .workshop_sandbox_runner import execute_source
from .workshop_script_contracts import (
    CONTRACT_SUBSET_VERSION,
    default_input_schema,
    default_output_schema,
    validate_schema,
    validate_value,
)
from .workshop_script_inspector import INSPECTOR_VERSION, RULESET_HASH, inspect_source
from .workshop_script_store import (
    ensure_schema,
    get_script,
    json_field,
    latest_evidence,
    list_scripts,
    mark_activations,
    next_version,
    observatory_summary,
    read_source,
    record_event,
    script_identity,
    set_state,
    store_source,
    validate_script_id,
)


_INSTALLED = False
_SAFE_STATES = {
    "received",
    "draft",
    "inspected",
    "tested",
    "approved",
    "active",
    "disabled",
    "superseded",
    "rejected",
    "quarantined",
    "deferred",
    "archived",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _hash_id(value: str) -> str:
    return sha256_text(str(value))[:32]


def _environment_id(descriptor: dict[str, Any]) -> str:
    interpreter = descriptor.get("interpreter") or {}
    implementation = str(interpreter.get("implementation") or "python").casefold()
    version = str(interpreter.get("version") or "unknown")
    return f"{implementation}-{version}-isolated"


def _now_dt(value: str) -> datetime:
    clean = str(value).strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    parsed = datetime.fromisoformat(clean)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _default_script_id(name: str) -> str:
    slug = _SLUG_RE.sub("-", str(name).casefold()).strip("-")[:100] or "script"
    return validate_script_id(f"resident.{slug}")


def _script_limits(house: Any, wall_seconds: Any = None) -> dict[str, Any]:
    backend_limits = _limits(house, {"wall_seconds": wall_seconds})
    return {
        "wall_seconds": backend_limits.wall_seconds,
        "memory_mb": 256,
        "processes": 1,
        "files_created": backend_limits.artifact_files,
        "input_bytes": backend_limits.input_bytes,
        "output_bytes": backend_limits.artifact_bytes,
        "stdout_bytes": backend_limits.stdout_bytes,
        "stderr_bytes": backend_limits.stderr_bytes,
        "trace_events": 128,
    }


def _pending_grant(
    house: Any,
    *,
    script_id: str,
    version: int,
    source_hash: str,
    limits: dict[str, Any],
) -> dict[str, Any]:
    subject = {
        "kind": "script",
        "id": script_id,
        "version": version,
        "content_hash": source_hash,
    }
    compute = {
        "capability": "sandbox.local_compute",
        "scope": {"profile": "local_process", "backend_id": "local.process"},
        "effects": ["compute", "artifact_write"],
        "outward_effect": "none",
        "cost_class": "local_low",
        "confirmation": "resident",
        "expires_at": None,
    }
    basis = {
        "subject": subject,
        "requested": [compute],
        "limits": {
            "wall_seconds": int(limits["wall_seconds"]),
            "artifact_bytes": int(limits["output_bytes"]),
        },
    }
    return {
        "schema_version": "vestigia.workshop-grant.v0.1",
        "subject": subject,
        "requested": [compute],
        "granted": [],
        "denied": [],
        "limits": {
            "wall_seconds": int(limits["wall_seconds"]),
            "tool_calls": 0,
            "provider_calls": 0,
            "outward_actions": 0,
            "image_generations": 0,
            "artifact_bytes": int(limits["output_bytes"]),
            "nested_depth": 0,
        },
        "approval": {
            "resident_required": True,
            "operator_required": False,
            "status": "pending",
            "expires_at": None,
            "plan_hash": sha256_text(stable_json(basis)),
        },
        "scope_context": {
            "resident_id_hash": _hash_id(house.resident_id),
            "room_id_hash": _hash_id(house.room_id),
            "interface": "workshop",
        },
    }


def _approved_grant(house: Any, row: Any) -> dict[str, Any]:
    pending = json_field(row, "requested_grant_json")
    grant = json.loads(stable_json(pending))
    grant["granted"] = list(grant["requested"])
    grant["approval"] = dict(grant["approval"])
    grant["approval"].update(
        {
            "status": "approved",
            "resident_actor_id": _hash_id(house.resident_id),
            "approved_at": utc_now_iso(),
        }
    )
    return grant


def _insert_script(
    house: Any,
    *,
    script_id: str,
    version: int,
    state: str,
    name: str,
    description: str,
    source_ref: dict[str, Any],
    provenance: dict[str, Any],
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    minimum_profile: str,
    allowed_backends: list[str],
    determinism: str,
    limits: dict[str, Any],
) -> dict[str, Any]:
    if state not in _SAFE_STATES:
        raise ValueError("unsupported script lifecycle state")
    descriptor = backend_descriptor(house)
    environment_id = _environment_id(descriptor)
    requested_grant = _pending_grant(
        house,
        script_id=script_id,
        version=version,
        source_hash=str(source_ref["sha256"]),
        limits=limits,
    )
    now = utc_now_iso()
    input_hash = sha256_text(stable_json(input_schema))
    output_hash = sha256_text(stable_json(output_schema))
    grant_hash = sha256_text(stable_json(requested_grant))
    privacy = {"source": "private", "default_outputs": "private", "shareable": False}
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workshop_scripts
            (resident_id, script_id, version, state, name, description, language,
             environment_id, source_object_id, source_hash, source_size, source_path,
             provenance_json, input_schema_json, input_schema_hash, output_schema_json,
             output_schema_hash, requested_grant_json, requested_grant_hash, limits_json,
             allowed_backends_json, minimum_profile, determinism, privacy_json,
             quarantine_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'python', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                house.resident_id,
                script_id,
                version,
                state,
                name,
                description,
                environment_id,
                source_ref["object_id"],
                source_ref["sha256"],
                source_ref["size_bytes"],
                source_ref["storage_path"],
                stable_json(provenance),
                stable_json(input_schema),
                input_hash,
                stable_json(output_schema),
                output_hash,
                stable_json(requested_grant),
                grant_hash,
                stable_json(limits),
                stable_json(allowed_backends),
                minimum_profile,
                determinism,
                stable_json(privacy),
                now,
                now,
            ),
        )
        event_id = record_event(
            house,
            script_id=script_id,
            version=version,
            event_type="script_drafted" if state == "draft" else "script_received",
            from_state=None,
            to_state=state,
            evidence_id=str(source_ref["object_id"]),
            connection=connection,
        )
    return {
        "script_id": script_id,
        "version": version,
        "state": state,
        "source": {
            "object_id": source_ref["object_id"],
            "sha256": source_ref["sha256"],
            "size_bytes": source_ref["size_bytes"],
            "media_type": "text/x-python",
        },
        "event_id": event_id,
        "callable": False,
        "source_executed": False,
    }


def _draft(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "Untitled script").strip()[:120]
    if not name:
        raise ValueError("script name cannot be empty")
    script_id = validate_script_id(payload["script_id"]) if payload.get("script_id") else _default_script_id(name)
    version = next_version(house, script_id)
    source = str(payload.get("source") or "")
    source_ref = store_source(house, source)
    input_schema = validate_schema(payload.get("input_schema") or default_input_schema(), label="input_schema")
    output_schema = validate_schema(payload.get("output_schema") or default_output_schema(), label="output_schema")
    determinism = str(payload.get("determinism") or "unknown").strip().lower()
    if determinism not in {"deterministic", "nondeterministic", "unknown"}:
        raise ValueError("determinism must be deterministic, nondeterministic, or unknown")
    limits = _script_limits(house, payload.get("wall_seconds"))
    provenance = {
        "authored_by": {"lane": "resident", "actor_id": _hash_id(house.resident_id)},
        "supplied_by": {"lane": "resident", "actor_id": _hash_id(house.resident_id)},
        "derived_from": list(payload.get("derived_from") or [])[:64],
        "source_object_ids": [],
        "note": "Resident-authored source drafted inside the local Workshop.",
    }
    return {
        "mode": "draft",
        **_insert_script(
            house,
            script_id=script_id,
            version=version,
            state="draft",
            name=name,
            description=str(payload.get("description") or "")[:2000],
            source_ref=source_ref,
            provenance=provenance,
            input_schema=input_schema,
            output_schema=output_schema,
            minimum_profile="local_process",
            allowed_backends=["local.process"],
            determinism=determinism,
            limits=limits,
        ),
        "next_required": "inspect",
    }


def _receive(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "Received script").strip()[:120]
    script_id = validate_script_id(str(payload.get("script_id") or ""))
    version = int(payload.get("version") or 1)
    if version < 1:
        raise ValueError("version must be at least 1")
    source_ref = store_source(house, str(payload.get("source") or ""))
    try:
        existing = get_script(house, script_id, version)
    except ValueError:
        existing = None
    if existing is not None:
        if str(existing["source_hash"]) == str(source_ref["sha256"]):
            return {
                "mode": "receive",
                "status": "duplicate",
                "script_id": script_id,
                "version": version,
                "source_hash": source_ref["sha256"],
                "state": str(existing["state"]),
                "callable": str(existing["state"]) == "active",
                "source_executed": False,
            }
        set_state(
            house,
            existing,
            "quarantined",
            event_type="version_digest_conflict",
            reason=f"conflicting candidate digest {source_ref['sha256']}",
        )
        mark_activations(house, script_id, version, "quarantined")
        return {
            "mode": "receive",
            "status": "quarantined_conflict",
            "script_id": script_id,
            "version": version,
            "existing_source_hash": str(existing["source_hash"]),
            "candidate_source_hash": source_ref["sha256"],
            "source_executed": False,
            "callable": False,
        }
    authored_lane = str(payload.get("authored_lane") or "unknown").strip().lower()
    if authored_lane not in {"resident", "operator", "participant", "model", "extension", "imported", "unknown"}:
        raise ValueError("unsupported authored_lane")
    actor_id = str(payload.get("authored_actor_id") or "unknown")[:160]
    supplier = str(payload.get("supplied_actor_id") or "interface")[:160]
    input_schema = validate_schema(payload.get("input_schema") or default_input_schema(), label="input_schema")
    output_schema = validate_schema(payload.get("output_schema") or default_output_schema(), label="output_schema")
    determinism = str(payload.get("determinism") or "unknown").strip().lower()
    if determinism not in {"deterministic", "nondeterministic", "unknown"}:
        raise ValueError("determinism must be deterministic, nondeterministic, or unknown")
    limits = _script_limits(house, payload.get("wall_seconds"))
    provenance = {
        "authored_by": {
            "lane": authored_lane,
            "actor_id": actor_id,
            "claimed_name": str(payload.get("authored_name") or "")[:160],
        },
        "supplied_by": {"lane": "imported", "actor_id": supplier},
        "derived_from": list(payload.get("derived_from") or [])[:64],
        "source_object_ids": list(payload.get("source_object_ids") or [])[:64],
        "note": str(payload.get("provenance_note") or "Received source remains inert.")[:1000],
    }
    return {
        "mode": "receive",
        **_insert_script(
            house,
            script_id=script_id,
            version=version,
            state="received",
            name=name,
            description=str(payload.get("description") or "")[:2000],
            source_ref=source_ref,
            provenance=provenance,
            input_schema=input_schema,
            output_schema=output_schema,
            minimum_profile="hardened",
            allowed_backends=[],
            determinism=determinism,
            limits=limits,
        ),
        "next_required": "inspect",
        "execution_boundary": "hardened backend required for received/imported source",
    }


def _evidence_summary(house: Any, row: Any) -> dict[str, Any]:
    script_id = str(row["script_id"])
    version = int(row["version"])
    inspection = latest_evidence(house, "workshop_script_inspections", script_id, version)
    test = latest_evidence(house, "workshop_script_tests", script_id, version)
    approval = latest_evidence(house, "workshop_script_approvals", script_id, version)
    activation = latest_evidence(house, "workshop_script_activations", script_id, version)
    return {
        "inspection": (
            {
                "id": inspection["id"],
                "classification": inspection["classification"],
                "parse_ok": bool(inspection["parse_ok"]),
                "ruleset_hash": inspection["ruleset_hash"],
                "created_at": inspection["created_at"],
            }
            if inspection
            else None
        ),
        "test": (
            {
                "id": test["id"],
                "status": test["status"],
                "backend_id": test["backend_id"],
                "environment_id": test["environment_id"],
                "created_at": test["created_at"],
            }
            if test
            else None
        ),
        "approval": (
            {
                "id": approval["id"],
                "status": approval["status"],
                "grant_hash": approval["grant_hash"],
                "created_at": approval["created_at"],
            }
            if approval
            else None
        ),
        "activation": (
            {
                "id": activation["id"],
                "status": activation["status"],
                "grant_hash": activation["grant_hash"],
                "expires_at": activation["expires_at"],
                "created_at": activation["created_at"],
            }
            if activation
            else None
        ),
    }


def _script_card(house: Any, row: Any) -> dict[str, Any]:
    provenance = json_field(row, "provenance_json")
    limits = json_field(row, "limits_json")
    allowed_backends = json_field(row, "allowed_backends_json")
    return {
        "schema_version": "vestigia.resident-script.v0.1",
        "id": str(row["script_id"]),
        "name": str(row["name"]),
        "version": int(row["version"]),
        "state": str(row["state"]),
        "description": str(row["description"]),
        "language": {
            "name": "python",
            "version_range": "current-runtime-interpreter",
            "environment_id": str(row["environment_id"]),
        },
        "source": {
            "object_id": str(row["source_object_id"]),
            "sha256": str(row["source_hash"]),
            "size_bytes": int(row["source_size"]),
            "media_type": "text/x-python",
        },
        "provenance": provenance,
        "input_schema": json_field(row, "input_schema_json"),
        "output_schema": json_field(row, "output_schema_json"),
        "requested_grant": json_field(row, "requested_grant_json"),
        "sandbox": {
            "minimum_profile": str(row["minimum_profile"]),
            "allowed_backends": allowed_backends,
            "hostile_code_assumed": False,
        },
        "limits": limits,
        "determinism": {"declared": str(row["determinism"]), "sources": []},
        "privacy": json_field(row, "privacy_json"),
        "quarantine_reason_present": bool(row["quarantine_reason"]),
        "identity": script_identity(row),
        "evidence": _evidence_summary(house, row),
        "callable": str(row["state"]) == "active",
        "source_included": False,
        "contract_subset": CONTRACT_SUBSET_VERSION,
    }


def _inspect(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) in {"active", "superseded", "archived", "rejected", "quarantined"}:
        raise ValueError("this lifecycle state is not reopened by static inspection")
    try:
        source = read_source(house, row)
    except Exception as exc:
        set_state(house, row, "quarantined", event_type="source_integrity_failed", reason=str(exc))
        mark_activations(house, str(row["script_id"]), int(row["version"]), "quarantined")
        raise
    provenance = json_field(row, "provenance_json")
    authored_lane = str((provenance.get("authored_by") or {}).get("lane") or "unknown")
    report = inspect_source(source, authored_lane=authored_lane)
    inspection_id = new_id("script_inspection")
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workshop_script_inspections
            (id, resident_id, script_id, version, source_hash, inspector_version,
             ruleset_hash, classification, parse_ok, report_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inspection_id,
                house.resident_id,
                row["script_id"],
                row["version"],
                row["source_hash"],
                INSPECTOR_VERSION,
                RULESET_HASH,
                report["classification"],
                int(bool(report["parse_ok"])),
                stable_json(report),
                utc_now_iso(),
            ),
        )
    if not report["parse_ok"]:
        set_state(
            house,
            row,
            "quarantined",
            event_type="inspection_quarantined",
            evidence_id=inspection_id,
            reason=report["safe_message"],
        )
        mark_activations(house, str(row["script_id"]), int(row["version"]), "quarantined")
    else:
        set_state(house, row, "inspected", event_type="script_inspected", evidence_id=inspection_id)
        mark_activations(house, str(row["script_id"]), int(row["version"]), "stale")
    return {
        "mode": "inspect",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "inspection_id": inspection_id,
        "inspection": report,
        "source_executed": False,
        "callable": False,
        "next_required": "test" if report["classification"] == "local_process_eligible" else "hardened_backend",
    }


def _latest_current_inspection(house: Any, row: Any) -> Any:
    inspection = latest_evidence(
        house, "workshop_script_inspections", str(row["script_id"]), int(row["version"])
    )
    if inspection is None or str(inspection["source_hash"]) != str(row["source_hash"]):
        raise PermissionError("current exact source has no local inspection receipt")
    if not bool(inspection["parse_ok"]):
        raise PermissionError("current inspection is not executable")
    return inspection


def _test(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) not in {"inspected", "tested"}:
        raise PermissionError("script must be inspected before testing")
    inspection = _latest_current_inspection(house, row)
    if str(inspection["classification"]) != "local_process_eligible":
        raise PermissionError("this script requires hardened isolation; local_process testing refused")
    if str(row["minimum_profile"]) != "local_process":
        raise PermissionError("this source provenance requires hardened isolation")
    allowed = set(json_field(row, "allowed_backends_json"))
    descriptor = backend_descriptor(house)
    if descriptor["backend_id"] not in allowed or not descriptor["health"]["callable_now"]:
        raise PermissionError("approved test backend is not callable")
    arguments = payload.get("arguments") or {}
    input_schema = json_field(row, "input_schema_json")
    validate_value(arguments, input_schema, path="$.arguments")
    source = read_source(house, row)
    limits = json_field(row, "limits_json")
    wall = min(int(payload.get("wall_seconds") or limits["wall_seconds"]), int(limits["wall_seconds"]))
    result = execute_source(
        house,
        source=source,
        script_id=str(row["script_id"]),
        script_version=int(row["version"]),
        arguments=arguments,
        context=context,
        payload={"wall_seconds": wall},
    )
    report: dict[str, Any] = {
        "sandbox_status": result["status"],
        "value_contract_ok": False,
        "contract_error": None,
        "outward_effect": result["outward_effect"],
        "authority_changed": result["authority_changed"],
        "memory_adopted": result["memory_adopted"],
        "published": result["published"],
    }
    status = "failed"
    output_hash: str | None = None
    if result["status"] == "succeeded":
        try:
            validate_value(result.get("value"), json_field(row, "output_schema_json"), path="$.value")
            report["value_contract_ok"] = True
            status = "succeeded"
            output_hash = sha256_text(stable_json(result.get("value")))
        except ValueError as exc:
            report["contract_error"] = str(exc)
    error_category = str(((result.get("error") or {}).get("category") or ""))
    test_id = new_id("script_test")
    input_hash = sha256_text(stable_json(arguments))
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workshop_script_tests
            (id, resident_id, script_id, version, source_hash, inspection_id,
             sandbox_execution_id, status, backend_id, backend_version, guarantees_hash,
             environment_id, input_hash, output_hash, report_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_id,
                house.resident_id,
                row["script_id"],
                row["version"],
                row["source_hash"],
                inspection["id"],
                result["execution_id"],
                status,
                descriptor["backend_id"],
                descriptor["version"],
                descriptor["guarantees_hash"],
                _environment_id(descriptor),
                input_hash,
                output_hash,
                stable_json(report),
                utc_now_iso(),
            ),
        )
    if error_category == "artifact_rejected":
        set_state(
            house,
            row,
            "quarantined",
            event_type="test_undeclared_behavior_quarantined",
            evidence_id=test_id,
            reason="sandbox rejected undeclared or unsafe artifact behavior",
        )
        next_required = "review_quarantine"
    elif status == "succeeded":
        set_state(house, row, "tested", event_type="script_tested", evidence_id=test_id)
        next_required = "approve"
    else:
        set_state(house, row, "inspected", event_type="script_test_failed", evidence_id=test_id)
        next_required = "revise_or_retest"
    return {
        "mode": "test",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "test_id": test_id,
        "status": status,
        "report": report,
        "sandbox_execution_id": result["execution_id"],
        "workshop_receipt_id": result["workshop_receipt_id"],
        "artifacts": result.get("artifacts", []),
        "requested_follow_up": result.get("requested_follow_up", []),
        "follow_up_executed": False,
        "next_required": next_required,
        "callable": False,
    }


def _approve(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) != "tested":
        raise PermissionError("script must have a current successful local test before approval")
    inspection = _latest_current_inspection(house, row)
    test = latest_evidence(house, "workshop_script_tests", str(row["script_id"]), int(row["version"]))
    if test is None or str(test["status"]) != "succeeded" or str(test["inspection_id"]) != str(inspection["id"]):
        raise PermissionError("current inspection does not have a successful bound test")
    grant = _approved_grant(house, row)
    grant_hash = sha256_text(stable_json(grant))
    approval_id = new_id("script_approval")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workshop_script_approvals
            (id, resident_id, script_id, version, source_hash, inspection_id,
             test_id, grant_hash, grant_json, status, approved_by, created_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, NULL)
            """,
            (
                approval_id,
                house.resident_id,
                row["script_id"],
                row["version"],
                row["source_hash"],
                inspection["id"],
                test["id"],
                grant_hash,
                stable_json(grant),
                _hash_id(house.resident_id),
                now,
            ),
        )
    set_state(house, row, "approved", event_type="script_approved", evidence_id=approval_id)
    return {
        "mode": "approve",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "approval_id": approval_id,
        "grant_hash": grant_hash,
        "granted_capabilities": [item["capability"] for item in grant["granted"]],
        "outward_actions": grant["limits"]["outward_actions"],
        "provider_calls": grant["limits"]["provider_calls"],
        "callable": False,
        "next_required": "activate",
    }


def _activation_inputs(house: Any, row: Any) -> tuple[Any, Any, Any, dict[str, Any]]:
    inspection = _latest_current_inspection(house, row)
    test = latest_evidence(house, "workshop_script_tests", str(row["script_id"]), int(row["version"]))
    approval = latest_evidence(house, "workshop_script_approvals", str(row["script_id"]), int(row["version"]))
    if test is None or str(test["status"]) != "succeeded" or str(test["inspection_id"]) != str(inspection["id"]):
        raise PermissionError("activation requires the current successful test")
    if approval is None or str(approval["status"]) != "approved" or str(approval["test_id"]) != str(test["id"]):
        raise PermissionError("activation requires the current exact approval")
    descriptor = backend_descriptor(house)
    if not descriptor["health"]["callable_now"]:
        raise PermissionError("sandbox backend is not callable")
    if str(row["minimum_profile"]) != "local_process" or descriptor["backend_id"] not in set(json_field(row, "allowed_backends_json")):
        raise PermissionError("this script requires an unavailable hardened backend")
    if str(inspection["classification"]) != "local_process_eligible" or str(inspection["ruleset_hash"]) != RULESET_HASH:
        raise PermissionError("static inspection rules changed or no longer permit local_process")
    if (
        str(test["backend_id"]) != str(descriptor["backend_id"])
        or str(test["backend_version"]) != str(descriptor["version"])
        or str(test["guarantees_hash"]) != str(descriptor["guarantees_hash"])
        or str(test["environment_id"]) != _environment_id(descriptor)
    ):
        raise PermissionError("sandbox backend or interpreter changed; retesting is required")
    return inspection, test, approval, descriptor


def _activate(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) != "approved":
        raise PermissionError("script must be approved before activation")
    inspection, test, approval, descriptor = _activation_inputs(house, row)
    limits = json_field(row, "limits_json")
    max_wall = min(int(payload.get("max_wall_seconds") or limits["wall_seconds"]), int(limits["wall_seconds"]))
    active_seconds = int(payload.get("active_seconds") or 0)
    active_seconds = max(0, min(active_seconds, 2592000))
    expires_at = (
        (datetime.now(UTC) + timedelta(seconds=active_seconds)).isoformat()
        if active_seconds
        else None
    )
    activation_id = new_id("script_activation")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO workshop_script_activations
            (id, resident_id, script_id, version, source_hash, inspection_id,
             inspection_ruleset_hash, test_id, approval_id, grant_hash, backend_id,
             backend_version, guarantees_hash, environment_id, max_wall_seconds,
             direct_call, status, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?)
            """,
            (
                activation_id,
                house.resident_id,
                row["script_id"],
                row["version"],
                row["source_hash"],
                inspection["id"],
                inspection["ruleset_hash"],
                test["id"],
                approval["id"],
                approval["grant_hash"],
                descriptor["backend_id"],
                descriptor["version"],
                descriptor["guarantees_hash"],
                _environment_id(descriptor),
                max_wall,
                now,
                now,
                expires_at,
            ),
        )
    set_state(house, row, "active", event_type="script_activated", evidence_id=activation_id)
    return {
        "mode": "activate",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "activation_id": activation_id,
        "source_hash": str(row["source_hash"]),
        "grant_hash": str(approval["grant_hash"]),
        "max_wall_seconds": max_wall,
        "expires_at": expires_at,
        "callable": True,
        "authority": "local sandbox computation only",
    }


def _active_activation(house: Any, row: Any) -> Any:
    activation = latest_evidence(house, "workshop_script_activations", str(row["script_id"]), int(row["version"]))
    if activation is None or str(activation["status"]) != "active":
        raise PermissionError("no active hash-bound activation exists for this version")
    if activation["expires_at"] and _now_dt(str(activation["expires_at"])) <= datetime.now(UTC):
        mark_activations(house, str(row["script_id"]), int(row["version"]), "expired")
        set_state(house, row, "approved", event_type="script_activation_expired", evidence_id=activation["id"])
        raise PermissionError("script activation expired")
    return activation


def _run(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    requested_version = payload.get("version")
    try:
        row = get_script(
            house,
            str(payload.get("script_id") or ""),
            int(requested_version) if requested_version is not None else None,
            active_only=requested_version is None,
        )
    except ValueError as exc:
        if requested_version is None:
            raise PermissionError("no active script version is callable for this script") from exc
        raise
    if str(row["state"]) != "active":
        raise PermissionError("script version is not active")
    activation = _active_activation(house, row)
    descriptor = backend_descriptor(house)
    stale = (
        str(activation["source_hash"]) != str(row["source_hash"])
        or str(activation["inspection_ruleset_hash"]) != RULESET_HASH
        or str(activation["backend_id"]) != str(descriptor["backend_id"])
        or str(activation["backend_version"]) != str(descriptor["version"])
        or str(activation["guarantees_hash"]) != str(descriptor["guarantees_hash"])
        or str(activation["environment_id"]) != _environment_id(descriptor)
    )
    if stale:
        mark_activations(house, str(row["script_id"]), int(row["version"]), "stale")
        set_state(house, row, "inspected", event_type="script_activation_stale", evidence_id=activation["id"])
        raise PermissionError("activation became stale; inspect/test/approve/activate again")
    source = read_source(house, row)
    arguments = payload.get("arguments") or {}
    validate_value(arguments, json_field(row, "input_schema_json"), path="$.arguments")
    wall = min(int(payload.get("wall_seconds") or activation["max_wall_seconds"]), int(activation["max_wall_seconds"]))
    result = execute_source(
        house,
        source=source,
        script_id=str(row["script_id"]),
        script_version=int(row["version"]),
        arguments=arguments,
        context=context,
        payload={"wall_seconds": wall},
    )
    shelf_status = str(result["status"])
    output_hash: str | None = None
    contract_error: str | None = None
    if result["status"] == "succeeded":
        try:
            validate_value(result.get("value"), json_field(row, "output_schema_json"), path="$.value")
            output_hash = sha256_text(stable_json(result.get("value")))
        except ValueError as exc:
            shelf_status = "failed"
            contract_error = str(exc)
            mark_activations(house, str(row["script_id"]), int(row["version"]), "quarantined")
            set_state(
                house,
                row,
                "quarantined",
                event_type="live_output_contract_quarantined",
                evidence_id=str(result["execution_id"]),
                reason=contract_error,
            )
    if str(((result.get("error") or {}).get("category") or "")) == "artifact_rejected":
        mark_activations(house, str(row["script_id"]), int(row["version"]), "quarantined")
        set_state(
            house,
            row,
            "quarantined",
            event_type="live_undeclared_behavior_quarantined",
            evidence_id=str(result["execution_id"]),
            reason="sandbox rejected undeclared or unsafe artifact behavior",
        )
    run_id = new_id("script_run")
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO workshop_script_runs
            (id, resident_id, script_id, version, source_hash, activation_id,
             sandbox_execution_id, status, input_hash, output_hash, authorship, privacy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'script_generated', 'private', ?)
            """,
            (
                run_id,
                house.resident_id,
                row["script_id"],
                row["version"],
                row["source_hash"],
                activation["id"],
                result["execution_id"],
                shelf_status,
                sha256_text(stable_json(arguments)),
                output_hash,
                utc_now_iso(),
            ),
        )
    return {
        "mode": "run",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "source_hash": str(row["source_hash"]),
        "activation_id": str(activation["id"]),
        "run_id": run_id,
        "status": shelf_status,
        "value": result.get("value") if contract_error is None else None,
        "contract_error": contract_error,
        "artifacts": result.get("artifacts", []),
        "output_authorship": "script_generated",
        "output_privacy": "private",
        "sandbox_execution_id": result["execution_id"],
        "workshop_receipt_id": result["workshop_receipt_id"],
        "requested_follow_up": result.get("requested_follow_up", []),
        "follow_up_executed": False,
        "outward_effect": "none",
        "authority_changed": False,
        "memory_adopted": False,
        "published": False,
    }


def _disable(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) != "active":
        raise ValueError("only an active script version can be disabled")
    mark_activations(house, str(row["script_id"]), int(row["version"]), "disabled")
    set_state(house, row, "disabled", event_type="script_disabled", reason=str(payload.get("reason") or "")[:1000])
    return {"mode": "disable", "script_id": row["script_id"], "version": row["version"], "state": "disabled", "callable": False}


def _supersede(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = str(payload.get("script_id") or "")
    old = get_script(house, script_id, int(payload.get("version") or 0))
    replacement_version = int(payload.get("replacement_version") or 0)
    replacement = get_script(house, script_id, replacement_version)
    if int(old["version"]) == int(replacement["version"]):
        raise ValueError("replacement version must differ from the version being superseded")
    if str(replacement["state"]) != "active":
        raise PermissionError("replacement version must already be active")
    if str(old["state"]) == "active":
        mark_activations(house, script_id, int(old["version"]), "superseded")
    set_state(
        house,
        old,
        "superseded",
        event_type="script_superseded",
        evidence_id=f"{script_id}@{replacement_version}",
        reason=f"explicitly superseded by version {replacement_version}",
    )
    return {"mode": "supersede", "script_id": script_id, "version": int(old["version"]), "state": "superseded", "replacement_version": replacement_version, "callable": False}


def _quarantine(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    mark_activations(house, str(row["script_id"]), int(row["version"]), "quarantined")
    set_state(
        house,
        row,
        "quarantined",
        event_type="script_quarantined",
        reason=str(payload.get("reason") or "resident safety decision")[:1000],
    )
    return {"mode": "quarantine", "script_id": row["script_id"], "version": row["version"], "state": "quarantined", "callable": False}


def _archive(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
    if str(row["state"]) == "active":
        raise PermissionError("disable an active script before archiving it")
    set_state(house, row, "archived", event_type="script_archived", reason=str(payload.get("reason") or "")[:1000])
    return {"mode": "archive", "script_id": row["script_id"], "version": row["version"], "state": "archived", "callable": False}


def _handle(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(house)
    mode = str(payload.get("mode") or "list").strip().lower()
    if mode == "draft":
        return _draft(house, payload)
    if mode == "receive":
        return _receive(house, payload)
    if mode == "list":
        limit = max(1, min(int(payload.get("limit") or 30), 100))
        return {"mode": mode, "scripts": list_scripts(house, limit), "source_included": False}
    if mode == "show":
        row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
        return {"mode": mode, "script": _script_card(house, row)}
    if mode == "read_source":
        row = get_script(house, str(payload.get("script_id") or ""), payload.get("version"))
        return {
            "mode": mode,
            "script_id": str(row["script_id"]),
            "version": int(row["version"]),
            "source_hash": str(row["source_hash"]),
            "source": read_source(house, row),
            "private": True,
            "source_executed": False,
        }
    if mode == "inspect":
        return _inspect(house, payload)
    if mode == "test":
        return _test(house, payload, context)
    if mode == "approve":
        return _approve(house, payload)
    if mode == "activate":
        return _activate(house, payload)
    if mode == "run":
        return _run(house, payload, context)
    if mode == "disable":
        return _disable(house, payload)
    if mode == "supersede":
        return _supersede(house, payload)
    if mode == "quarantine":
        return _quarantine(house, payload)
    if mode == "archive":
        return _archive(house, payload)
    raise ValueError("unsupported script.shelf mode")


def _register(house: Any) -> None:
    ensure_schema(house)
    house.registry.register(
        CapabilitySpec(
            name="script.shelf",
            description=(
                "Draft, receive, inspect, test, approve, activate, run, disable, and review "
                "immutable resident scripts. Source existence never implies callability."
            ),
            effects=(
                "filesystem:private_script_source",
                "database:script_lifecycle",
                "local_process:conditional_test_or_run",
            ),
            cost_class="local_low",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="workshop.enabled",
            group="workshop",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "script.shelf"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "draft", "receive", "list", "show", "read_source", "inspect",
                            "test", "approve", "activate", "run", "disable", "supersede",
                            "quarantine", "archive",
                        ],
                    },
                    "script_id": {"type": "string", "maxLength": 160},
                    "version": {"type": "integer", "minimum": 1},
                    "replacement_version": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 120},
                    "description": {"type": "string", "maxLength": 2000},
                    "source": {"type": "string", "maxLength": 1048576},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "arguments": {"type": "object"},
                    "determinism": {"type": "string", "enum": ["deterministic", "nondeterministic", "unknown"]},
                    "authored_lane": {"type": "string"},
                    "authored_actor_id": {"type": "string", "maxLength": 160},
                    "authored_name": {"type": "string", "maxLength": 160},
                    "supplied_actor_id": {"type": "string", "maxLength": 160},
                    "provenance_note": {"type": "string", "maxLength": 1000},
                    "source_object_ids": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 200}},
                    "derived_from": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 200}},
                    "wall_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                    "max_wall_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                    "active_seconds": {"type": "integer", "minimum": 0, "maximum": 2592000},
                    "reason": {"type": "string", "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "after": {"type": "string", "enum": ["continue", "finish"]},
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "script.shelf",
                    "mode": "draft",
                    "name": "Tiny greeter",
                    "source": "import json, sys\np=json.load(sys.stdin)\njson.dump({'schema_version':'vestigia.script-output.v0.1','value':{'text':'hi'},'artifacts':[],'warnings':[]}, sys.stdout)",
                    "after": "continue",
                },
                {"action": "script.shelf", "mode": "inspect", "script_id": "resident.tiny-greeter", "version": 1, "after": "continue"},
                {"action": "script.shelf", "mode": "run", "script_id": "resident.tiny-greeter", "arguments": {}, "after": "continue"},
            ),
            next_step=(
                "A new source version remains inert. Inspect, test, approve, and activate the exact "
                "hash before run. Imported or hardened-only source remains non-callable."
            ),
        ),
        lambda payload, context: _handle(house, payload, context),
    )


def install_core() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .house_tools import HousePort

    previous_install = HousePort._install_capabilities

    def install_with_script_shelf(self: Any) -> None:
        previous_install(self)
        _register(self)

    HousePort._install_capabilities = install_with_script_shelf

    try:
        from . import sensory_apparatus

        previous_observatory = sensory_apparatus._observatory

        def observatory_with_scripts(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
            result = previous_observatory(house, payload)
            panels = result.get("observatory")
            if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":
                panels["script_shelf"] = observatory_summary(house)
            return result

        sensory_apparatus._observatory = observatory_with_scripts
    except (ImportError, AttributeError):
        pass

    _INSTALLED = True
