from __future__ import annotations

from difflib import unified_diff
import json
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .workshop_contract_tools import diagnose_contract
from .workshop_script_inspector import inspect_source
from .workshop_script_store import (
    ensure_schema,
    get_script,
    json_field,
    latest_evidence,
    list_scripts,
    read_source,
    script_identity,
)


MICROSCOPE_SCHEMA_VERSION = "vestigia.workshop-microscope.v0.2"
_MODES = (
    "explain",
    "lifecycle",
    "provenance",
    "contracts",
    "quarantine",
    "compare",
    "help",
)
_STATE_MEANINGS = {
    "received": "Imported or received immutable source stored inertly.",
    "draft": "Resident-authored immutable source stored inertly.",
    "inspected": "Static inspection evidence exists; source is still inert.",
    "quarantined": "Sticky safety hold; source is inert.",
    "archived": "Historical source retained inertly.",
}


def _inspection(house: Any, row: Any, *, ephemeral: bool = False) -> dict[str, Any] | None:
    evidence = latest_evidence(
        house,
        "workshop_script_inspections",
        str(row["script_id"]),
        int(row["version"]),
    )
    if evidence is not None:
        try:
            report = json.loads(str(evidence["report_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            report = {}
        report.update(
            {
                "evidence_id": str(evidence["id"]),
                "evidence_kind": "persisted_static_inspection",
            }
        )
        return report
    if not ephemeral:
        return None
    provenance = json_field(row, "provenance_json")
    lane = str((provenance.get("authored_by") or {}).get("lane") or "unknown")
    report = inspect_source(read_source(house, row), authored_lane=lane)
    report.update(
        {
            "evidence_id": None,
            "evidence_kind": "ephemeral_read_only_analysis",
        }
    )
    return report


def _events(house: Any, row: Any, limit: int = 50) -> list[dict[str, Any]]:
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, event_type, from_state, to_state, evidence_id, note_hash, created_at
            FROM workshop_script_events
            WHERE resident_id=? AND script_id=? AND version=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (
                house.resident_id,
                row["script_id"],
                int(row["version"]),
                max(1, min(int(limit), 200)),
            ),
        ).fetchall()
    return [dict(item) for item in rows]


def _lifecycle(house: Any, row: Any) -> dict[str, Any]:
    events = _events(house, row)
    state = str(row["state"])
    return {
        "current_state": state,
        "state_meaning": _STATE_MEANINGS.get(state, "Unknown inert shelf state."),
        "events": events,
        "event_count_returned": len(events),
        "callable": False,
        "execution_available": False,
        "transition_boundary": (
            "This runtime slice supports storage, inspection, quarantine, and archive only."
        ),
    }


def _provenance(row: Any) -> dict[str, Any]:
    provenance = json_field(row, "provenance_json")
    return {
        "script": script_identity(row),
        "authored_by": provenance.get("authored_by"),
        "supplied_by": provenance.get("supplied_by"),
        "derived_from": list(provenance.get("derived_from") or [])[:64],
        "source_object_ids": list(provenance.get("source_object_ids") or [])[:64],
        "source_hash": str(row["source_hash"]),
        "source_size": int(row["source_size"]),
        "source_included": False,
        "execution_evidence": None,
        "model_interpretation_inferred": False,
    }


def _contracts(row: Any) -> dict[str, Any]:
    return {
        "input": diagnose_contract(
            json_field(row, "input_schema_json"), label="input_schema"
        ),
        "output": diagnose_contract(
            json_field(row, "output_schema_json"), label="output_schema"
        ),
        "contracts_authorize_execution": False,
    }


def _manifest(house: Any, row: Any) -> dict[str, Any]:
    report = _inspection(house, row)
    requested = json_field(row, "requested_grant_json")
    return {
        "schema_version": "vestigia.script-capability-manifest.v0.2",
        "script": script_identity(row),
        "declared": {
            "requested_capabilities": [
                str(item.get("capability"))
                for item in requested.get("requested", [])
                if item.get("capability")
            ],
            "minimum_profile": str(row["minimum_profile"]),
            "allowed_backends": list(json_field(row, "allowed_backends_json")),
        },
        "observed": {
            "inspection_present": report is not None,
            "classification": report.get("classification") if report else None,
            "imports": list((report or {}).get("imports") or [])[:32],
            "dangerous_calls": list((report or {}).get("dangerous_calls") or [])[:32],
            "violations": list((report or {}).get("violations") or [])[:32],
            "warnings": list((report or {}).get("warnings") or [])[:32],
            "observation_is_security_proof": False,
        },
        "effective": {
            "callable": False,
            "reason_code": "hardened_execution_unavailable",
            "granted_capabilities": [],
            "provider_calls": 0,
            "outward_actions": 0,
            "execution_available": False,
        },
        "backend": {
            "required_profile": "hardened",
            "available": False,
            "operator_approval_path_installed": False,
        },
        "security_proof": False,
    }


def _quarantine(house: Any, row: Any) -> dict[str, Any] | None:
    if str(row["state"]) != "quarantined":
        return None
    event = next(
        (item for item in _events(house, row) if item.get("to_state") == "quarantined"),
        None,
    )
    return {
        "sticky": True,
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "source_hash": str(row["source_hash"]),
        "triggering_event": event.get("event_type") if event else None,
        "evidence_id": event.get("evidence_id") if event else None,
        "reason_note_hash": event.get("note_hash") if event else None,
        "created_at": event.get("created_at") if event else None,
        "recovery": "Draft a new immutable version; quarantine is not cleared in place.",
    }


def _row(house: Any, payload: dict[str, Any]) -> Any:
    return get_script(
        house,
        str(payload.get("script_id") or ""),
        payload.get("version"),
    )


def _explain(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = _row(house, payload)
    return {
        "schema_version": MICROSCOPE_SCHEMA_VERSION,
        "mode": "explain",
        "script_id": str(row["script_id"]),
        "version": int(row["version"]),
        "identity": script_identity(row),
        "lifecycle": _lifecycle(house, row),
        "provenance": _provenance(row),
        "contracts": _contracts(row),
        "manifest": _manifest(house, row),
        "quarantine": _quarantine(house, row),
        "read_only": True,
        "source_executed": False,
        "authority_changed": False,
    }


def _compare(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = str(payload.get("script_id") or "")
    left_version = int(payload.get("left_version") or 0)
    right_version = int(payload.get("right_version") or 0)
    if left_version < 1 or right_version < 1:
        raise ValueError("compare requires left_version and right_version")
    left = get_script(house, script_id, left_version)
    right = get_script(house, script_id, right_version)
    left_source = read_source(house, left).splitlines()
    right_source = read_source(house, right).splitlines()
    diff = list(
        unified_diff(
            left_source,
            right_source,
            fromfile=f"{script_id}@{left_version}",
            tofile=f"{script_id}@{right_version}",
            lineterm="",
        )
    )
    limit = max(1, min(int(payload.get("limit") or 200), 500))
    return {
        "schema_version": MICROSCOPE_SCHEMA_VERSION,
        "mode": "compare",
        "script_id": script_id,
        "left": script_identity(left),
        "right": script_identity(right),
        "diff": diff[:limit],
        "diff_line_count": len(diff),
        "diff_truncated": len(diff) > limit,
        "private": True,
        "read_only": True,
        "source_executed": False,
    }


def _help() -> dict[str, Any]:
    return {
        "schema_version": MICROSCOPE_SCHEMA_VERSION,
        "mode": "help",
        "modes": list(_MODES),
        "boundary": (
            "The Microscope explains inert script evidence. It does not test, approve, "
            "activate, execute, unquarantine, or grant authority."
        ),
        "read_only": True,
    }


def _handle(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(house)
    mode = str(payload.get("mode") or "help").strip().lower()
    if mode == "help":
        return _help()
    if mode == "compare":
        return _compare(house, payload)
    row = _row(house, payload)
    if mode == "explain":
        return _explain(house, payload)
    if mode == "lifecycle":
        return {
            "mode": mode,
            "script": script_identity(row),
            "lifecycle": _lifecycle(house, row),
            "read_only": True,
        }
    if mode == "provenance":
        return {
            "mode": mode,
            "script": script_identity(row),
            "provenance": _provenance(row),
            "read_only": True,
        }
    if mode == "contracts":
        return {
            "mode": mode,
            "script": script_identity(row),
            "contracts": _contracts(row),
            "read_only": True,
        }
    if mode == "quarantine":
        return {
            "mode": mode,
            "script": script_identity(row),
            "quarantine": _quarantine(house, row),
            "read_only": True,
        }
    raise ValueError("unsupported workshop.microscope mode")


def _register(house: Any) -> None:
    ensure_schema(house)
    house.registry.register(
        CapabilitySpec(
            name="workshop.microscope",
            description=(
                "Explain inert script lifecycle, provenance, contracts, static inspection, "
                "quarantine, and version differences without changing authority."
            ),
            effects=("database:read", "filesystem:private_script_read"),
            cost_class="local_low",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="workshop.enabled",
            group="workshop",
            input_schema=object_schema(
                {
                    "action": {
                        "type": "string",
                        "const": "workshop.microscope",
                    },
                    "mode": {"type": "string", "enum": list(_MODES)},
                    "script_id": {"type": "string", "maxLength": 160},
                    "version": {"type": "integer", "minimum": 1},
                    "left_version": {"type": "integer", "minimum": 1},
                    "right_version": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "after": {"type": "string", "enum": ["continue", "finish"]},
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "workshop.microscope",
                    "mode": "explain",
                    "script_id": "resident.greeter",
                    "version": 1,
                    "after": "continue",
                },
                {
                    "action": "workshop.microscope",
                    "mode": "compare",
                    "script_id": "resident.greeter",
                    "left_version": 1,
                    "right_version": 2,
                    "after": "continue",
                },
            ),
            next_step=(
                "Use script.shelf to inspect, quarantine, archive, or draft a new inert "
                "version. No execution path is exposed."
            ),
        ),
        lambda payload, _context: _handle(house, payload),
    )


def _observatory_panel(
    house: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    panels = result.get("observatory")
    if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":
        scripts = list_scripts(house, 100)
        panels["workshop_microscope"] = {
            "script_count": len(scripts),
            "state_counts": {
                state: sum(1 for item in scripts if str(item["state"]) == state)
                for state in sorted({str(item["state"]) for item in scripts})
            },
            "execution_available": False,
            "read_only": True,
        }
    return result


def register_composition() -> None:
    from .composition import register_capability_installer, register_observatory_panel

    register_capability_installer("workshop.microscope", _register, order=70)
    register_observatory_panel(
        "workshop.microscope", _observatory_panel, order=70
    )
