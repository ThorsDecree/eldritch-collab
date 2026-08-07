from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "VESTIGIA_Runtime" / "src" / "vestigia"
TESTS = ROOT / "VESTIGIA_Runtime" / "tests"
DOCS = ROOT / "VESTIGIA_Runtime" / "docs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def replace(path: Path, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old!r}")
    write(path, text.replace(old, new, 1))


# Add the shelf to the explicit composition plan delivered by PR #27.
bootstrap = SRC / "bootstrap.py"
replace(
    bootstrap,
    '    ("workshop_sandbox", "register_composition"),\n)',
    '    ("workshop_sandbox", "register_composition"),\n'
    '    ("workshop_script_shelf", "register_composition"),\n)',
)

# Fix connection ownership and compare-and-set state transitions in the store.
store = SRC / "workshop_script_store.py"
store_text = read(store)
start = store_text.index("def record_event(\n")
end = store_text.index("\n\ndef list_scripts(", start)
replacement = '''def record_event(
    house: Any,
    *,
    script_id: str,
    version: int,
    event_type: str,
    from_state: str | None,
    to_state: str | None,
    evidence_id: str | None = None,
    note: str | None = None,
    connection: Any | None = None,
) -> str:
    if connection is None:
        with house.db.connect() as owned:
            return record_event(
                house,
                script_id=script_id,
                version=version,
                event_type=event_type,
                from_state=from_state,
                to_state=to_state,
                evidence_id=evidence_id,
                note=note,
                connection=owned,
            )

    event_id = new_id("script_event")
    connection.execute(
        """
        INSERT INTO workshop_script_events
        (id, resident_id, script_id, version, event_type, from_state, to_state,
         evidence_id, note_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            house.resident_id,
            script_id,
            int(version),
            event_type,
            from_state,
            to_state,
            evidence_id,
            sha256_text(note) if note else None,
            utc_now_iso(),
        ),
    )
    return event_id


def set_state(
    house: Any,
    row: Any,
    state: str,
    *,
    event_type: str,
    evidence_id: str | None = None,
    reason: str | None = None,
    expected_state: str | None = None,
    connection: Any | None = None,
) -> str:
    if connection is None:
        with house.db.connect() as owned:
            owned.execute("BEGIN IMMEDIATE")
            return set_state(
                house,
                row,
                state,
                event_type=event_type,
                evidence_id=evidence_id,
                reason=reason,
                expected_state=expected_state,
                connection=owned,
            )

    previous = expected_state or str(row["state"])
    now = utc_now_iso()
    cursor = connection.execute(
        """
        UPDATE workshop_scripts
        SET state=?, quarantine_reason=?, updated_at=?
        WHERE resident_id=? AND script_id=? AND version=? AND state=?
        """,
        (
            state,
            reason if state == "quarantined" else None,
            now,
            house.resident_id,
            row["script_id"],
            int(row["version"]),
            previous,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("script state changed concurrently; retry from fresh evidence")
    return record_event(
        house,
        script_id=str(row["script_id"]),
        version=int(row["version"]),
        event_type=event_type,
        from_state=previous,
        to_state=state,
        evidence_id=evidence_id,
        note=reason,
        connection=connection,
    )
'''
write(store, store_text[:start] + replacement + store_text[end:])

# Replace the executable shelf with an inert provenance, storage, and inspection surface.
shelf = '''from __future__ import annotations

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
'''
write(SRC / "workshop_script_shelf.py", shelf)

# Replace lifecycle tests with inertness, atomicity, and concurrency coverage.
tests = '''from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime
from vestigia.workshop_script_store import record_event


SAFE_SOURCE = """import json
import sys
payload = json.load(sys.stdin)
json.dump({'schema_version':'vestigia.script-output.v0.1','value':payload,'artifacts':[],'warnings':[]}, sys.stdout)
"""
INPUT_SCHEMA = {"type": "object", "additionalProperties": True}
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": True}


class ScriptShelfCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Shelf Resident", glyph="S"
        )
        self.config = load_config(self.home)
        self.runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, mode: str, **payload):
        return self.runtime.house.dispatch(
            {"action": "script.shelf", "mode": mode, **payload},
            context={"interface": "test"},
        )

    def draft(
        self,
        *,
        script_id: str = "resident.greeter",
        source: str = SAFE_SOURCE,
    ):
        return self.call(
            "draft",
            script_id=script_id,
            name="Greeter",
            source=source,
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )


class ScriptShelfTests(ScriptShelfCase):
    def test_capability_exposes_no_execution_lifecycle_modes(self) -> None:
        spec = self.runtime.house.registry.spec("script.shelf")
        modes = set(spec.input_schema["properties"]["mode"]["enum"])
        self.assertEqual(
            {
                "draft",
                "receive",
                "list",
                "show",
                "read_source",
                "inspect",
                "quarantine",
                "archive",
            },
            modes,
        )
        self.assertTrue(
            modes.isdisjoint({"test", "approve", "activate", "run", "disable"})
        )
        for mode in ("test", "approve", "activate", "run"):
            with self.assertRaises(ValueError):
                self.call(mode, script_id="resident.greeter", version=1)

    def test_draft_and_inspection_never_execute_source(self) -> None:
        marker = self.root / "must-not-exist.txt"
        source = f"open(r'{marker.as_posix()}', 'w').write('executed')\n"
        drafted = self.draft(source=source)
        self.assertEqual("draft", drafted["state"])
        self.assertFalse(drafted["source_executed"])
        inspected = self.call("inspect", script_id="resident.greeter", version=1)
        self.assertTrue(inspected["inspection"]["parse_ok"])
        self.assertEqual("await_hardened_execution_path", inspected["next_required"])
        self.assertFalse(inspected["execution_available"])
        self.assertFalse(marker.exists())

    def test_script_cards_and_observatory_are_explicitly_non_callable(self) -> None:
        drafted = self.draft()
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual(drafted["source"]["sha256"], card["source"]["sha256"])
        self.assertFalse(card["source_included"])
        self.assertFalse(card["callable"])
        self.assertFalse(card["sandbox"]["available"])
        self.assertEqual([], card["sandbox"]["allowed_backends"])
        exact = self.call("read_source", script_id="resident.greeter", version=1)
        self.assertEqual(SAFE_SOURCE, exact["source"])
        panel = self.runtime.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )["observatory"]["script_shelf"]
        self.assertFalse(panel["execution_available"])
        self.assertFalse(panel["provider_reachable_execution"])

    def test_received_and_risky_source_remain_hardened_only(self) -> None:
        self.call(
            "receive",
            script_id="imported.greeter",
            version=1,
            name="Imported",
            source=SAFE_SOURCE,
            authored_lane="model",
            authored_actor_id="model",
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
        )
        imported = self.call("inspect", script_id="imported.greeter", version=1)
        self.assertEqual("hardened_only", imported["inspection"]["classification"])
        self.assertEqual("await_hardened_execution_path", imported["next_required"])

        self.draft(
            script_id="resident.socket-test",
            source="import socket\n" + SAFE_SOURCE,
        )
        risky = self.call("inspect", script_id="resident.socket-test", version=1)
        self.assertEqual("hardened_only", risky["inspection"]["classification"])
        self.assertIn(
            "sensitive_import_requires_hardened",
            risky["inspection"]["violations"],
        )

    def test_concurrent_drafts_allocate_unique_versions_atomically(self) -> None:
        def create(index: int) -> int:
            result = self.draft(
                script_id="resident.concurrent",
                source=SAFE_SOURCE + f"\n# version candidate {index}\n",
            )
            return int(result["version"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            versions = sorted(pool.map(create, range(4)))
        self.assertEqual([1, 2, 3, 4], versions)

    def test_interrupted_inspection_rolls_back_evidence_and_state(self) -> None:
        self.draft()
        with patch(
            "vestigia.workshop_script_store.record_event",
            side_effect=RuntimeError("simulated interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                self.call("inspect", script_id="resident.greeter", version=1)
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT state FROM workshop_scripts WHERE resident_id=? "
                "AND script_id='resident.greeter' AND version=1",
                (self.runtime.house.resident_id,),
            ).fetchone()
            count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM workshop_script_inspections "
                    "WHERE resident_id=? AND script_id='resident.greeter' AND version=1",
                    (self.runtime.house.resident_id,),
                ).fetchone()["count"]
            )
        self.assertEqual("draft", row["state"])
        self.assertEqual(0, count)

    def test_record_event_can_own_its_connection(self) -> None:
        self.draft()
        event_id = record_event(
            self.runtime.house,
            script_id="resident.greeter",
            version=1,
            event_type="fixture_event",
            from_state="draft",
            to_state="draft",
        )
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM workshop_script_events WHERE id=?", (event_id,)
            ).fetchone()
        self.assertIsNotNone(row)

    def test_digest_conflict_quarantines_without_overwriting(self) -> None:
        first = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE,
            authored_lane="participant",
        )
        conflict = self.call(
            "receive",
            script_id="shared.tool",
            version=1,
            name="Shared",
            source=SAFE_SOURCE + "\n# different\n",
            authored_lane="participant",
        )
        self.assertEqual("quarantined_conflict", conflict["status"])
        card = self.call("show", script_id="shared.tool", version=1)["script"]
        self.assertEqual("quarantined", card["state"])
        self.assertEqual(first["source"]["sha256"], card["source"]["sha256"])

    def test_source_tamper_fails_closed(self) -> None:
        self.draft()
        with self.runtime.house.db.connect() as connection:
            row = connection.execute(
                "SELECT source_path FROM workshop_scripts WHERE resident_id=? "
                "AND script_id='resident.greeter' AND version=1",
                (self.runtime.house.resident_id,),
            ).fetchone()
        (self.home / row["source_path"]).write_text("# tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "hash no longer matches"):
            self.call("inspect", script_id="resident.greeter", version=1)
        card = self.call("show", script_id="resident.greeter", version=1)["script"]
        self.assertEqual("quarantined", card["state"])


if __name__ == "__main__":
    unittest.main()
'''
write(TESTS / "test_workshop_script_shelf.py", tests)

# Rewrite the runtime document to match the shipped authority boundary.
doc = '''# Resident Script Shelf Runtime Boundary

The first runtime slice of the resident script shelf is deliberately **inert**.
It stores immutable source, provenance, typed input/output contracts, static
inspection evidence, quarantine decisions, and archive state. It does not run
Python.

## Available resident operations

- `draft`
- `receive`
- `list`
- `show`
- `read_source`
- `inspect`
- `quarantine`
- `archive`

The provider-facing capability does not expose `test`, `approve`, `activate`,
`run`, `disable`, or `supersede`.

## Why execution is absent

The existing `local.process` Workshop backend is honest ordinary-bug
containment. It does not enforce network denial, host-filesystem denial,
process-tree containment, CPU limits, or memory limits. Static AST inspection is
review evidence, not isolation.

A stored script therefore requests a future `hardened` execution profile and
carries no granted backend. Inspection never changes that. Source remains
non-callable until a separate design supplies:

1. enforceable hardened isolation;
2. a separately authenticated operator approval path;
3. hash-, schema-, backend-, limit-, and expiry-bound grants;
4. atomic execution lifecycle and revocation receipts.

## Atomicity

Draft version allocation and insertion occur under one `BEGIN IMMEDIATE`
transaction. Inspection evidence, state transition, and lifecycle event commit
or roll back together. State changes use an expected-state predicate so stale
writers fail rather than silently overwriting newer evidence.

## Privacy and authority

Source is content-addressed and private. Listing and Observatory views expose
metadata and hashes, not source bytes. Inspection performs no imports and no
execution. No provider call, outward action, memory adoption, publication, or
resident identity change follows from storage or inspection.
'''
write(DOCS / "WORKSHOP_SCRIPT_SHELF_RUNTIME.md", doc)

# Remove the package-import installer left by the earlier branch. PR #27 owns bootstrap.
write(SRC / "__init__.py", '"""VESTIGIA portable continuity runtime."""\n\n__version__ = "0.8.0.dev0"\n')

Path(__file__).unlink()
