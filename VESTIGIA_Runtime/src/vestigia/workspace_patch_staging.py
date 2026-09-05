from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .composition import register_capability_installer
from .utils import new_id, sha256_text, stable_json, utc_now_iso


PATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_patch_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    path TEXT NOT NULL,
    destination TEXT,
    candidate_content TEXT,
    base_hash TEXT,
    candidate_hash TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workspace_patch_drafts_resident
ON workspace_patch_drafts(resident_id, status, created_at);
"""

PATCH_OPERATIONS = {"create", "edit", "delete", "move"}


def _workspace_locator(raw: object, *, field: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        raise ValueError(f"{field} is required")
    path = PurePosixPath(value)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field} must be a normalized relative workspace path")
    if ":" in parts[0]:
        raise ValueError(f"{field} may not be drive-qualified")
    normalized = "/".join(parts)
    if normalized == "workspace" or not normalized.startswith("workspace/"):
        raise ValueError(f"{field} must stay under the Runtime workspace shelf")
    return normalized


def _file_handlers(house: Any):
    return (
        house.registry.handler("file.diff"),
        house.registry.handler("stat"),
    )


def _stat_optional(house: Any, path: str) -> dict[str, Any] | None:
    _, stat_handler = _file_handlers(house)
    try:
        return stat_handler({"action": "stat", "path": path}, {})
    except (FileNotFoundError, LookupError):
        return None


def _diff(
    house: Any,
    *,
    path: str,
    content: str,
    expected_hash: str = "",
) -> dict[str, Any]:
    diff_handler, _ = _file_handlers(house)
    return diff_handler(
        {
            "action": "file.diff",
            "path": path,
            "content": content,
            "expected_hash": expected_hash,
        },
        {},
    )


def _row_view(row: Any) -> dict[str, Any]:
    return {
        "patch_id": str(row["id"]),
        "operation": str(row["operation"]),
        "path": str(row["path"]),
        "destination": row["destination"],
        "base_hash": row["base_hash"],
        "candidate_hash": row["candidate_hash"],
        "reason": str(row["reason"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "candidate_content_stored": row["candidate_content"] is not None,
        "applied": False,
    }


def _patch_row(house: Any, patch_id: str) -> Any:
    wanted = str(patch_id or "").strip()
    if not wanted:
        raise ValueError("patch_id is required")
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM workspace_patch_drafts
            WHERE id=? AND resident_id=?
            """,
            (wanted, house.resident_id),
        ).fetchone()
    if not row:
        raise KeyError("unknown workspace patch draft")
    return row


def _prepare_stage(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    operation = str(payload.get("operation") or "").strip().lower()
    if operation not in PATCH_OPERATIONS:
        raise ValueError("operation must be create, edit, delete, or move")
    path = _workspace_locator(payload.get("path"), field="path")
    destination = None
    expected = str(payload.get("expected_hash") or "").strip()
    content_present = "content" in payload
    content = str(payload.get("content") or "") if content_present else None
    current = _stat_optional(house, path)

    if operation == "create":
        if not content_present:
            raise ValueError("create staging requires content")
        if current is not None:
            raise ValueError("create staging requires a path that does not currently exist")
        preview = _diff(house, path=path, content=content or "")
        base_hash = None
        candidate_hash = str(preview["proposed_hash"])
    elif operation == "edit":
        if not content_present:
            raise ValueError("edit staging requires content")
        if current is None:
            raise FileNotFoundError(path)
        base_hash = str(current["file_hash"])
        if expected and expected != base_hash:
            raise RuntimeError("workspace file changed after it was read; inspect and retry")
        preview = _diff(
            house,
            path=path,
            content=content or "",
            expected_hash=base_hash,
        )
        candidate_hash = str(preview["proposed_hash"])
    elif operation == "delete":
        if content_present:
            raise ValueError("delete staging does not accept content")
        if current is None:
            raise FileNotFoundError(path)
        base_hash = str(current["file_hash"])
        if expected and expected != base_hash:
            raise RuntimeError("workspace file changed after it was read; inspect and retry")
        preview = _diff(
            house,
            path=path,
            content="",
            expected_hash=base_hash,
        )
        candidate_hash = None
    else:
        if content_present:
            raise ValueError("move staging does not accept content")
        destination = _workspace_locator(payload.get("destination"), field="destination")
        if destination == path:
            raise ValueError("move destination must differ from source path")
        if current is None:
            raise FileNotFoundError(path)
        destination_current = _stat_optional(house, destination)
        if destination_current is not None:
            raise ValueError("move destination already exists")
        base_hash = str(current["file_hash"])
        if expected and expected != base_hash:
            raise RuntimeError("workspace file changed after it was read; inspect and retry")
        source_preview = _diff(
            house,
            path=path,
            content="",
            expected_hash=base_hash,
        )
        # This zero-content diff is only a safe writable-path/existence probe. A future
        # apply capability must re-read the source bytes and re-check both paths.
        destination_probe = _diff(house, path=destination, content="")
        preview = {
            "operation": "move",
            "source": source_preview,
            "destination": {
                "path": destination,
                "currently_exists": False,
                "writable_path_verified": True,
                "probe_hash": destination_probe.get("proposed_hash"),
                "full_destination_diff_available": False,
            },
        }
        candidate_hash = base_hash

    return {
        "operation": operation,
        "path": path,
        "destination": destination,
        "candidate_content": content if operation in {"create", "edit"} else None,
        "base_hash": base_hash,
        "candidate_hash": candidate_hash,
        "preview": preview,
    }


def _stage(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    prepared = _prepare_stage(house, payload)
    patch_id = new_id("patch")
    now = utc_now_iso()
    reason = str(payload.get("reason") or "").strip()[:1000]
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO workspace_patch_drafts
            (id, resident_id, operation, path, destination, candidate_content,
             base_hash, candidate_hash, reason, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?)
            """,
            (
                patch_id,
                house.resident_id,
                prepared["operation"],
                prepared["path"],
                prepared["destination"],
                prepared["candidate_content"],
                prepared["base_hash"],
                prepared["candidate_hash"],
                reason,
                now,
                now,
            ),
        )
    return {
        "patch_id": patch_id,
        "status": "staged",
        "operation": prepared["operation"],
        "path": prepared["path"],
        "destination": prepared["destination"],
        "base_hash": prepared["base_hash"],
        "candidate_hash": prepared["candidate_hash"],
        "preview": prepared["preview"],
        "proposal_only": True,
        "workspace_changed": False,
        "canonical_changed": False,
        "apply_capability_available": False,
        "next_step": "Inspect with fs.patch_preview / fs.patch_validate; applying is intentionally unavailable.",
    }


def _list(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "staged").strip().lower()
    if status not in {"staged", "discarded", "all"}:
        raise ValueError("status must be staged, discarded, or all")
    limit = min(200, max(1, int(payload.get("limit", 50))))
    sql = "SELECT * FROM workspace_patch_drafts WHERE resident_id=?"
    params: list[Any] = [house.resident_id]
    if status != "all":
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY rowid DESC LIMIT ?"
    params.append(limit)
    with house.db.connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return {
        "patches": [_row_view(row) for row in rows],
        "status_filter": status,
        "proposal_only": True,
        "apply_capability_available": False,
    }


def _evaluate(house: Any, row: Any) -> dict[str, Any]:
    operation = str(row["operation"])
    path = str(row["path"])
    destination = str(row["destination"] or "") or None
    base_hash = str(row["base_hash"] or "") or None
    content = row["candidate_content"]

    if str(row["status"]) != "staged":
        return {
            "valid": False,
            "reason": f"patch_status_{row['status']}",
            "current_workspace_unchanged": None,
        }

    try:
        current = _stat_optional(house, path)
        if operation == "create":
            if current is not None:
                return {
                    "valid": False,
                    "reason": "create_target_now_exists",
                    "current_workspace_unchanged": False,
                    "current_hash": current.get("file_hash"),
                }
            preview = _diff(house, path=path, content=str(content or ""))
            return {
                "valid": True,
                "reason": "create_target_still_absent",
                "current_workspace_unchanged": True,
                "preview": preview,
            }

        if current is None:
            return {
                "valid": False,
                "reason": "source_now_missing",
                "current_workspace_unchanged": False,
            }
        current_hash = str(current["file_hash"])
        if base_hash and current_hash != base_hash:
            return {
                "valid": False,
                "reason": "source_hash_changed",
                "current_workspace_unchanged": False,
                "expected_hash": base_hash,
                "current_hash": current_hash,
            }

        if operation == "edit":
            preview = _diff(
                house,
                path=path,
                content=str(content or ""),
                expected_hash=current_hash,
            )
        elif operation == "delete":
            preview = _diff(
                house,
                path=path,
                content="",
                expected_hash=current_hash,
            )
        elif operation == "move":
            if destination is None:
                return {
                    "valid": False,
                    "reason": "move_destination_missing_from_draft",
                    "current_workspace_unchanged": None,
                }
            destination_current = _stat_optional(house, destination)
            if destination_current is not None:
                return {
                    "valid": False,
                    "reason": "move_destination_now_exists",
                    "current_workspace_unchanged": False,
                    "destination_hash": destination_current.get("file_hash"),
                }
            source_preview = _diff(
                house,
                path=path,
                content="",
                expected_hash=current_hash,
            )
            destination_probe = _diff(house, path=destination, content="")
            preview = {
                "operation": "move",
                "source": source_preview,
                "destination": {
                    "path": destination,
                    "currently_exists": False,
                    "writable_path_verified": True,
                    "probe_hash": destination_probe.get("proposed_hash"),
                    "full_destination_diff_available": False,
                },
            }
        else:
            return {
                "valid": False,
                "reason": "unknown_staged_operation",
                "current_workspace_unchanged": None,
            }
        return {
            "valid": True,
            "reason": "base_preconditions_still_hold",
            "current_workspace_unchanged": True,
            "current_hash": current_hash,
            "preview": preview,
        }
    except (FileNotFoundError, LookupError, RuntimeError, ValueError, PermissionError) as exc:
        return {
            "valid": False,
            "reason": "validation_error",
            "current_workspace_unchanged": None,
            "error_type": type(exc).__name__,
            "error": str(exc)[:400],
        }


def _preview(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = _patch_row(house, str(payload.get("patch_id") or ""))
    return {
        "patch": _row_view(row),
        "validation": _evaluate(house, row),
        "proposal_only": True,
        "workspace_changed": False,
        "canonical_changed": False,
        "apply_capability_available": False,
    }


def _validate(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = _patch_row(house, str(payload.get("patch_id") or ""))
    return {
        "patch_id": str(row["id"]),
        "status": str(row["status"]),
        "validation": _evaluate(house, row),
        "validation_persisted": False,
        "proposal_only": True,
        "workspace_changed": False,
        "apply_capability_available": False,
    }


def _discard(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    row = _patch_row(house, str(payload.get("patch_id") or ""))
    if str(row["status"]) != "staged":
        raise ValueError("only staged patches may be discarded")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            """
            UPDATE workspace_patch_drafts
            SET status='discarded', updated_at=?
            WHERE id=? AND resident_id=? AND status='staged'
            """,
            (now, str(row["id"]), house.resident_id),
        )
    return {
        "patch_id": str(row["id"]),
        "status": "discarded",
        "proposal_preserved": True,
        "workspace_changed": False,
        "canonical_changed": False,
    }


def _register(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(PATCH_SCHEMA)

    after = {"type": "string", "enum": ["continue", "finish"]}
    patch_id = {"type": "string", "minLength": 3, "maxLength": 200}

    house.registry.register(
        CapabilitySpec(
            name="fs.stage_patch",
            description=(
                "Stage a create/edit/delete/move proposal against the resident workspace "
                "without applying it. The draft captures optimistic base hashes and a preview."
            ),
            effects=("database:write_pending_draft",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="editing",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "fs.stage_patch"},
                    "operation": {
                        "type": "string",
                        "enum": ["create", "edit", "delete", "move"],
                    },
                    "path": {"type": "string", "minLength": 1, "maxLength": 500},
                    "destination": {"type": "string", "maxLength": 500},
                    "content": {"type": "string"},
                    "expected_hash": {"type": "string", "maxLength": 128},
                    "reason": {"type": "string", "maxLength": 1000},
                    "after": after,
                },
                required=("action", "operation", "path"),
            ),
            example_envelopes=(
                {
                    "action": "fs.stage_patch",
                    "operation": "edit",
                    "path": "workspace/notes.md",
                    "content": "replacement text",
                    "after": "continue",
                },
            ),
        ),
        lambda payload, _context: _stage(house, payload),
    )

    house.registry.register(
        CapabilitySpec(
            name="fs.patch_list",
            description="List workspace patch proposals without revealing stored candidate text.",
            effects=("database:read",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="editing",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "fs.patch_list"},
                    "status": {
                        "type": "string",
                        "enum": ["staged", "discarded", "all"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "after": after,
                },
                required=("action",),
            ),
        ),
        lambda payload, _context: _list(house, payload),
    )

    house.registry.register(
        CapabilitySpec(
            name="fs.patch_preview",
            description=(
                "Recompute a staged workspace proposal preview against current files without "
                "writing or promoting anything."
            ),
            effects=("database:read", "filesystem:read"),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="editing",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "fs.patch_preview"},
                    "patch_id": patch_id,
                    "after": after,
                },
                required=("action", "patch_id"),
            ),
        ),
        lambda payload, _context: _preview(house, payload),
    )

    house.registry.register(
        CapabilitySpec(
            name="fs.patch_validate",
            description=(
                "Check whether a staged patch's base hashes/existence preconditions still hold. "
                "Validation is observational and is not persisted as authority."
            ),
            effects=("database:read", "filesystem:read"),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="editing",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "fs.patch_validate"},
                    "patch_id": patch_id,
                    "after": after,
                },
                required=("action", "patch_id"),
            ),
        ),
        lambda payload, _context: _validate(house, payload),
    )

    house.registry.register(
        CapabilitySpec(
            name="fs.patch_discard",
            description=(
                "Discard a staged patch proposal while preserving its draft record. No workspace "
                "or canonical file is changed."
            ),
            effects=("database:write_low_authority",),
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="house.enabled",
            group="editing",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "fs.patch_discard"},
                    "patch_id": patch_id,
                    "reason": {"type": "string", "maxLength": 1000},
                    "after": after,
                },
                required=("action", "patch_id"),
            ),
        ),
        lambda payload, _context: _discard(house, payload),
    )


def register_composition() -> None:
    register_capability_installer(
        "workspace_patch_staging",
        _register,
        order=330,
    )
