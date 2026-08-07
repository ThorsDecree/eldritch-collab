from __future__ import annotations

import json
import re
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .utils import new_id, sha256_text, stable_json, utc_now_iso
from .workshop_script_contracts import (
    CONTRACT_SUBSET_VERSION,
    default_input_schema,
    default_output_schema,
    validate_schema,
)
from .workshop_script_inspector import INSPECTOR_VERSION, RULESET_HASH, inspect_source
from .workshop_script_store import (
    ensure_schema,
    get_script,
    json_field,
    latest_evidence,
    list_scripts,
    observatory_summary,
    read_source,
    record_event,
    script_identity,
    set_state,
    store_source,
    validate_script_id,
)


_MODES = (
    "draft",
    "receive",
    "list",
    "show",
    "read_source",
    "inspect",
    "quarantine",
    "archive",
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _hash_id(value: str) -> str:
    return sha256_text(str(value))[:32]


def _default_script_id(name: str) -> str:
    slug = _SLUG_RE.sub("-", str(name).casefold()).strip("-")[:100] or "script"
    return validate_script_id(f"resident.{slug}")


def _limits(house: Any) -> dict[str, Any]:
    def bounded(key: str, default: int, maximum: int) -> int:
        try:
            value = int(house.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(0, min(value, maximum))

    return {
        "wall_seconds": bounded("workshop.max_wall_seconds", 5, 30),
        "memory_mb": 0,
        "processes": 0,
        "files_created": 0,
        "input_bytes": bounded("workshop.max_input_bytes", 65536, 1048576),
        "output_bytes": 0,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "trace_events": 0,
    }


def _pending_grant(
    house: Any,
    *,
    script_id: str,
    version: int,
    source_hash: str,
) -> dict[str, Any]:
    subject = {
        "kind": "script",
        "id": script_id,
        "version": int(version),
        "content_hash": source_hash,
    }
    return {
        "schema_version": "vestigia.workshop-grant.v0.1",
        "subject": subject,
        "requested": [
            {
                "capability": "sandbox.hardened_compute",
                "scope": {
                    "profile": "hardened",
                    "network": "deny_enforced",
                    "filesystem": "declared_mounts_only",
                    "process_tree": "contained",
                },
                "effects": ["compute", "private_artifact_write"],
                "outward_effect": "none",
                "cost_class": "local_bounded",
                "confirmation": "resident_and_operator",
                "expires_at": None,
            }
        ],
        "granted": [],
        "denied": [],
        "limits": {
            "tool_calls": 0,
            "provider_calls": 0,
            "outward_actions": 0,
            "image_generations": 0,
            "artifact_bytes": 0,
            "nested_depth": 0,
        },
        "approval": {
            "resident_required": True,
            "operator_required": True,
            "status": "unavailable",
            "reason": "No separately authenticated hardened execution path is installed.",
            "expires_at": None,
        },
        "scope_context": {
            "resident_id_hash": _hash_id(house.resident_id),
            "room_id_hash": _hash_id(house.room_id),
            "interface": "workshop",
        },
    }


def _insert_row(
    house: Any,
    connection: Any,
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
    event_type: str,
) -> dict[str, Any]:
    limits = _limits(house)
    requested_grant = _pending_grant(
        house,
        script_id=script_id,
        version=version,
        source_hash=str(source_ref["sha256"]),
    )
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO workshop_scripts
        (resident_id, script_id, version, state, name, description, language,
         environment_id, source_object_id, source_hash, source_size, source_path,
         provenance_json, input_schema_json, input_schema_hash, output_schema_json,
         output_schema_hash, requested_grant_json, requested_grant_hash, limits_json,
         allowed_backends_json, minimum_profile, determinism, privacy_json,
         quarantine_reason, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'python', 'hardened-unavailable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]',
                'hardened', 'unknown', ?, NULL, ?, ?)
        """,
        (
            house.resident_id,
            script_id,
            int(version),
            state,
            name,
            description,
            source_ref["object_id"],
            source_ref["sha256"],
            source_ref["size_bytes"],
            source_ref["storage_path"],
            stable_json(provenance),
            stable_json(input_schema),
            sha256_text(stable_json(input_schema)),
            stable_json(output_schema),
            sha256_text(stable_json(output_schema)),
            stable_json(requested_grant),
            sha256_text(stable_json(requested_grant)),
            stable_json(limits),
            stable_json(
                {"source": "private", "default_outputs": "private", "shareable": False}
            ),
            now,
            now,
        ),
    )
    event_id = record_event(
        house,
        script_id=script_id,
        version=version,
        event_type=event_type,
        from_state=None,
        to_state=state,
        evidence_id=str(source_ref["object_id"]),
        connection=connection,
    )
    return {
        "script_id": script_id,
        "version": int(version),
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
        "execution_available": False,
    }


def _schemas(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        validate_schema(
            payload.get("input_schema") or default_input_schema(),
            label="input_schema",
        ),
        validate_schema(
            payload.get("output_schema") or default_output_schema(),
            label="output_schema",
        ),
    )


def _draft(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(house)
    name = str(payload.get("name") or "Untitled script").strip()[:120]
    if not name:
        raise ValueError("script name cannot be empty")
    script_id = (
        validate_script_id(str(payload["script_id"]))
        if payload.get("script_id")
        else _default_script_id(name)
    )
    source_ref = store_source(house, str(payload.get("source") or ""))
    input_schema, output_schema = _schemas(payload)
    provenance = {
        "authored_by": {
            "lane": "resident",
            "actor_id": _hash_id(house.resident_id),
        },
        "supplied_by": {
            "lane": "resident",
            "actor_id": _hash_id(house.resident_id),
        },
        "derived_from": list(payload.get("derived_from") or [])[:64],
        "source_object_ids": [],
        "note": "Resident-authored source stored inertly inside the local Workshop.",
    }
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT MAX(version) AS value FROM workshop_scripts "
            "WHERE resident_id=? AND script_id=?",
            (house.resident_id, script_id),
        ).fetchone()
        version = int(row["value"] or 0) + 1
        inserted = _insert_row(
            house,
            connection,
            script_id=script_id,
            version=version,
            state="draft",
            name=name,
            description=str(payload.get("description") or "")[:2000],
            source_ref=source_ref,
            provenance=provenance,
            input_schema=input_schema,
            output_schema=output_schema,
            event_type="script_drafted",
        )
    return {
        "mode": "draft",
        **inserted,
        "next_required": "inspect",
        "execution_boundary": "hardened execution path not installed",
    }


def _receive(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(house)
    script_id = validate_script_id(str(payload.get("script_id") or ""))
    version = int(payload.get("version") or 1)
    if version < 1:
        raise ValueError("version must be at least 1")
    source_ref = store_source(house, str(payload.get("source") or ""))
    input_schema, output_schema = _schemas(payload)
    authored_lane = str(payload.get("authored_lane") or "unknown").strip().lower()
    if authored_lane not in {
        "resident",
        "operator",
        "participant",
        "model",
        "extension",
        "imported",
        "unknown",
    }:
        raise ValueError("unsupported authored_lane")
    provenance = {
        "authored_by": {
            "lane": authored_lane,
            "actor_id": str(payload.get("authored_actor_id") or "unknown")[:160],
            "claimed_name": str(payload.get("authored_name") or "")[:160],
        },
        "supplied_by": {
            "lane": "imported",
            "actor_id": str(payload.get("supplied_actor_id") or "interface")[:160],
        },
        "derived_from": list(payload.get("derived_from") or [])[:64],
        "source_object_ids": list(payload.get("source_object_ids") or [])[:64],
        "note": str(
            payload.get("provenance_note") or "Received source remains inert."
        )[:1000],
    }
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM workshop_scripts "
            "WHERE resident_id=? AND script_id=? AND version=?",
            (house.resident_id, script_id, version),
        ).fetchone()
        if existing is not None:
            if str(existing["source_hash"]) == str(source_ref["sha256"]):
                return {
                    "mode": "receive",
                    "status": "duplicate",
                    "script_id": script_id,
                    "version": version,
                    "source_hash": source_ref["sha256"],
                    "state": str(existing["state"]),
                    "callable": False,
                    "source_executed": False,
                }
            set_state(
                house,
                existing,
                "quarantined",
                event_type="version_digest_conflict",
                reason=f"conflicting candidate digest {source_ref['sha256']}",
                expected_state=str(existing["state"]),
                connection=connection,
            )
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
        inserted = _insert_row(
            house,
            connection,
            script_id=script_id,
            version=version,
            state="received",
            name=str(payload.get("name") or "Received script").strip()[:120],
            description=str(payload.get("description") or "")[:2000],
            source_ref=source_ref,
            provenance=provenance,
            input_schema=input_schema,
            output_schema=output_schema,
            event_type="script_received",
        )
    return {
        "mode": "receive",
        **inserted,
        "next_required": "inspect",
        "execution_boundary": "hardened execution path not installed",
    }


def _evidence_summary(house: Any, row: Any) -> dict[str, Any]:
    inspection = latest_evidence(
        house,
        "workshop_script_inspections",
        str(row["script_id"]),
        int(row["version"]),
    )
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
        "test": None,
        "approval": None,
        "activation": None,
    }


def _script_card(house: Any, row: Any) -> dict[str, Any]:
    return {
        "schema_version": "vestigia.resident-script.v0.1",
        "id": str(row["script_id"]),
        "name": str(row["name"]),
        "version": int(row["version"]),
        "state": str(row["state"]),
        "description": str(row["description"]),
        "language": {
            "name": "python",
            "version_range": "unexecuted-source",
            "environment_id": str(row["environment_id"]),
        },
        "source": {
            "object_id": str(row["source_object_id"]),
            "sha256": str(row["source_hash"]),
            "size_bytes": int(row["source_size"]),
            "media_type": "text/x-python",
        },
        "provenance": json_field(row, "provenance_json"),
        "input_schema": json_field(row, "input_schema_json"),
        "output_schema": json_field(row, "output_schema_json"),
        "requested_grant": json_field(row, "requested_grant_json"),
        "sandbox": {
            "minimum_profile": "hardened",
            "allowed_backends": [],
            "available": False,
            "hostile_code_assumed": True,
        },
        "limits": json_field(row, "limits_json"),
        "privacy": json_field(row, "privacy_json"),
        "quarantine_reason_present": bool(row["quarantine_reason"]),
        "identity": script_identity(row),
        "evidence": _evidence_summary(house, row),
        "callable": False,
        "source_included": False,
        "contract_subset": CONTRACT_SUBSET_VERSION,
    }


def _inspect(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(
        house,
        str(payload.get("script_id") or ""),
        payload.get("version"),
    )
    current_state = str(row["state"])
    if current_state in {"archived", "quarantined"}:
        raise ValueError("this lifecycle state is not reopened by static inspection")
    try:
        source = read_source(house, row)
    except Exception as exc:
        set_state(
            house,
            row,
            "quarantined",
            event_type="source_integrity_failed",
            reason=str(exc),
            expected_state=current_state,
        )
        raise
    provenance = json_field(row, "provenance_json")
    authored_lane = str(
        (provenance.get("authored_by") or {}).get("lane") or "unknown"
    )
    report = inspect_source(source, authored_lane=authored_lane)
    inspection_id = new_id("script_inspection")
    target_state = "inspected" if report["parse_ok"] else "quarantined"
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        fresh = connection.execute(
            "SELECT * FROM workshop_scripts "
            "WHERE resident_id=? AND script_id=? AND version=?",
            (house.resident_id, row["script_id"], int(row["version"])),
        ).fetchone()
        if fresh is None:
            raise RuntimeError("script disappeared during inspection")
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
                int(row["version"]),
                row["source_hash"],
                INSPECTOR_VERSION,
                RULESET_HASH,
                report["classification"],
                int(bool(report["parse_ok"])),
                stable_json(report),
                utc_now_iso(),
            ),
        )
        set_state(
            house,
            fresh,
            target_state,
            event_type=(
                "script_inspected" if report["parse_ok"] else "inspection_quarantined"
            ),
            evidence_id=inspection_id,
            reason=None if report["parse_ok"] else report["safe_message"],
            expected_state=str(fresh["state"]),
            connection=connection,
        )
    return {
        "mode": "inspect",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "inspection_id": inspection_id,
        "inspection": report,
        "source_executed": False,
        "callable": False,
        "next_required": "await_hardened_execution_path",
        "execution_available": False,
    }


def _quarantine(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(
        house,
        str(payload.get("script_id") or ""),
        payload.get("version"),
    )
    set_state(
        house,
        row,
        "quarantined",
        event_type="script_quarantined",
        reason=str(payload.get("reason") or "resident safety decision")[:1000],
        expected_state=str(row["state"]),
    )
    return {
        "mode": "quarantine",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "state": "quarantined",
        "callable": False,
    }


def _archive(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_script(
        house,
        str(payload.get("script_id") or ""),
        payload.get("version"),
    )
    if str(row["state"]) == "archived":
        return {
            "mode": "archive",
            "script_id": str(row["script_id"]),
            "version": int(row["version"]),
            "state": "archived",
            "callable": False,
            "idempotent": True,
        }
    set_state(
        house,
        row,
        "archived",
        event_type="script_archived",
        reason=str(payload.get("reason") or "")[:1000],
        expected_state=str(row["state"]),
    )
    return {
        "mode": "archive",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "state": "archived",
        "callable": False,
    }


def _handle(
    house: Any,
    payload: dict[str, Any],
    _context: dict[str, Any],
) -> dict[str, Any]:
    ensure_schema(house)
    mode = str(payload.get("mode") or "list").strip().lower()
    if mode == "draft":
        return _draft(house, payload)
    if mode == "receive":
        return _receive(house, payload)
    if mode == "list":
        limit = max(1, min(int(payload.get("limit") or 30), 100))
        return {
            "mode": mode,
            "scripts": list_scripts(house, limit),
            "source_included": False,
            "execution_available": False,
        }
    if mode == "show":
        row = get_script(
            house,
            str(payload.get("script_id") or ""),
            payload.get("version"),
        )
        return {"mode": mode, "script": _script_card(house, row)}
    if mode == "read_source":
        row = get_script(
            house,
            str(payload.get("script_id") or ""),
            payload.get("version"),
        )
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
    if mode == "quarantine":
        return _quarantine(house, payload)
    if mode == "archive":
        return _archive(house, payload)
    raise ValueError("unsupported inert script.shelf mode")


def _register(house: Any) -> None:
    ensure_schema(house)
    house.registry.register(
        CapabilitySpec(
            name="script.shelf",
            description=(
                "Store, receive, inspect, quarantine, archive, and privately read "
                "immutable script source. This surface does not execute source."
            ),
            effects=(
                "filesystem:private_script_source",
                "database:script_lifecycle",
                "execution:none",
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
                    "mode": {"type": "string", "enum": list(_MODES)},
                    "script_id": {"type": "string", "maxLength": 160},
                    "version": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 120},
                    "description": {"type": "string", "maxLength": 2000},
                    "source": {"type": "string", "maxLength": 1048576},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "authored_lane": {"type": "string"},
                    "authored_actor_id": {"type": "string", "maxLength": 160},
                    "authored_name": {"type": "string", "maxLength": 160},
                    "supplied_actor_id": {"type": "string", "maxLength": 160},
                    "provenance_note": {"type": "string", "maxLength": 1000},
                    "source_object_ids": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {"type": "string", "maxLength": 200},
                    },
                    "derived_from": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {"type": "string", "maxLength": 200},
                    },
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
                    "source": "print('stored, not executed')",
                    "after": "continue",
                },
                {
                    "action": "script.shelf",
                    "mode": "inspect",
                    "script_id": "resident.tiny-greeter",
                    "version": 1,
                    "after": "continue",
                },
            ),
            next_step=(
                "Source remains inert after inspection. Execution requires a separately "
                "authenticated hardened backend and approval path not present in this release."
            ),
        ),
        lambda payload, context: _handle(house, payload, context),
    )


def _observatory_panel(
    house: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    panels = result.get("observatory")
    if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":
        summary = observatory_summary(house)
        summary.update(
            {
                "execution_available": False,
                "provider_reachable_execution": False,
                "operator_approval_path_installed": False,
            }
        )
        panels["script_shelf"] = summary
    return result


def register_composition() -> None:
    from .composition import register_capability_installer, register_observatory_panel

    register_capability_installer("workshop.script_shelf", _register, order=60)
    register_observatory_panel(
        "workshop.script_shelf", _observatory_panel, order=60
    )
