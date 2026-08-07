from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .bells import BellService
from .config import ResolvedConfig
from .db import ContinuityDB
from .home import validate_home
from .packing import EXCLUDED_NAMES, EXCLUDED_SUFFIXES, inspect_home_pack
from .utils import sha256_file, utc_now_iso


SENSITIVE_KEY_FRAGMENTS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
}
PATH_KEYS = {"path", "home", "executable"}
IDENTITY_KEYS = {
    "name",
    "id",
    "active_resident_ids",
    "participant_ids",
    "allowed_user_ids",
    "allowed_channel_ids",
}
SUPPORT_BUNDLE_SCHEMA = "vestigia.support-bundle.v0.1"


def _hash_label(value: Any) -> str:
    raw = str(value or "")
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _redact(value: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).casefold()
            child_path = (*path, str(key))
            if any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS):
                result[str(key)] = "<redacted>"
            elif normalized in PATH_KEYS:
                result[str(key)] = "<redacted-path>"
            elif normalized in IDENTITY_KEYS:
                if isinstance(child, list):
                    result[str(key)] = [_hash_label(item) for item in child]
                else:
                    result[str(key)] = _hash_label(child)
            else:
                result[str(key)] = _redact(child, path=child_path)
        return result
    if isinstance(value, list):
        return [_redact(item, path=path) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, path=path) for item in value]
    return value


def _distribution_status(name: str, *, import_name: str | None = None) -> dict[str, Any]:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return {"installed": False, "version": None, "importable": False}
    module = import_name or name.replace("-", "_")
    importable = True
    try:
        __import__(module)
    except Exception:
        importable = False
    return {"installed": True, "version": version, "importable": importable}


def dependency_status(config: ResolvedConfig) -> dict[str, Any]:
    optional = {
        "discord.py": _distribution_status("discord.py", import_name="discord"),
        "tiktoken": _distribution_status("tiktoken"),
        "pytest": _distribution_status("pytest"),
    }
    required = {
        "PyYAML": _distribution_status("PyYAML", import_name="yaml"),
        "openai": _distribution_status("openai"),
        "Pillow": _distribution_status("Pillow", import_name="PIL"),
        "tzdata": _distribution_status("tzdata"),
    }
    ocr_binary = str(config.get("images.ocr_binary", "tesseract")).strip()
    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": str(Path(os.sys.executable).resolve()),
        },
        "required": required,
        "optional": optional,
        "external": {
            "ocr_binary": ocr_binary,
            "ocr_binary_found": bool(shutil.which(ocr_binary)),
        },
    }


def _schema_version(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row["value"]) if row else None


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row["name"])
        escaped = name.replace('"', '""')
        try:
            count = connection.execute(f'SELECT COUNT(*) AS n FROM "{escaped}"').fetchone()
            counts[name] = int(count["n"] if count else 0)
        except sqlite3.DatabaseError:
            counts[name] = -1
    return counts


def database_health(db: ContinuityDB) -> dict[str, Any]:
    with db.connect() as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity = [str(row[0]) for row in integrity_rows]
        foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        journal = connection.execute("PRAGMA journal_mode").fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()
        page_count = connection.execute("PRAGMA page_count").fetchone()
        page_size = connection.execute("PRAGMA page_size").fetchone()
        counts = _table_counts(connection)
        schema_version = _schema_version(connection)
    return {
        "path": str(db.path),
        "exists": db.path.is_file(),
        "size_bytes": db.path.stat().st_size if db.path.is_file() else 0,
        "sqlite_version": sqlite3.sqlite_version,
        "schema_version": schema_version,
        "user_version": int(user_version[0] if user_version else 0),
        "journal_mode": str(journal[0] if journal else "unknown"),
        "integrity": "ok" if integrity == ["ok"] else "failed",
        "integrity_details": integrity[:20],
        "foreign_key_violations": len(foreign_rows),
        "foreign_key_samples": [list(row) for row in foreign_rows[:20]],
        "page_count": int(page_count[0] if page_count else 0),
        "page_size": int(page_size[0] if page_size else 0),
        "table_counts": counts,
    }


def operation_health(
    config: ResolvedConfig,
    db: ContinuityDB,
) -> dict[str, Any]:
    resident_id = str(config.get("resident.id"))
    room_id = str(config.get("room.id"))
    stale_seconds = max(30, int(config.get("images.job_stale_seconds", 900)))
    stale_before = (datetime.now(UTC) - timedelta(seconds=stale_seconds)).isoformat()
    now = datetime.now(UTC).isoformat()
    with db.connect() as connection:
        image_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS n FROM image_jobs
            WHERE resident_id=? GROUP BY status
            """,
            (resident_id,),
        ).fetchall()
        stale_jobs = connection.execute(
            """
            SELECT id, operation, updated_at FROM image_jobs
            WHERE resident_id=? AND status='running' AND updated_at<?
            ORDER BY updated_at LIMIT 50
            """,
            (resident_id, stale_before),
        ).fetchall()
        unnotified = connection.execute(
            """
            SELECT COUNT(*) AS n FROM image_jobs
            WHERE resident_id=? AND status IN ('completed','failed')
              AND notified_at IS NULL
            """,
            (resident_id,),
        ).fetchone()
        interface_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='interface_events'
            """
        ).fetchone()
        interface_rows = (
            connection.execute(
                """
                SELECT status, COUNT(*) AS n FROM interface_events
                WHERE resident_id=? AND room_id=? GROUP BY status
                """,
                (resident_id, room_id),
            ).fetchall()
            if interface_table
            else []
        )
        failed_receipts = connection.execute(
            """
            SELECT COUNT(*) AS n FROM house_receipts
            WHERE resident_id=? AND status='failed'
            """,
            (resident_id,),
        ).fetchone()
        failed_bell_deliveries = connection.execute(
            """
            SELECT COUNT(*) AS n FROM bell_events e
            JOIN bells b ON b.id=e.bell_id
            WHERE b.resident_id=? AND e.event_type='delivery_failed'
            """,
            (resident_id,),
        ).fetchone()
        due_bells = connection.execute(
            """
            SELECT COUNT(*) AS n FROM bells
            WHERE resident_id=? AND status='active'
              AND next_fire_at IS NOT NULL AND next_fire_at<=?
              AND (expires_at IS NULL OR expires_at>?)
            """,
            (resident_id, now, now),
        ).fetchone()
    return {
        "image_jobs": {
            "by_status": {str(row["status"]): int(row["n"]) for row in image_rows},
            "stale_after_seconds": stale_seconds,
            "stale_running": [dict(row) for row in stale_jobs],
            "unnotified_terminal": int(unnotified["n"] if unnotified else 0),
            "recovery_contract": "stale running jobs are re-queued when ImageService starts",
            "execution_semantics": "at-least-once after stale-job recovery",
        },
        "interface_events": {
            "available": bool(interface_table),
            "by_status": {str(row["status"]): int(row["n"]) for row in interface_rows},
        },
        "bells": {
            "due_now": int(due_bells["n"] if due_bells else 0),
            "failed_delivery_events": int(
                failed_bell_deliveries["n"] if failed_bell_deliveries else 0
            ),
            "firing_semantics": (
                "schedule is advanced before delivery; failures are durably recorded and "
                "are not retried automatically"
            ),
        },
        "failed_action_receipts": int(failed_receipts["n"] if failed_receipts else 0),
        "checked_at": now,
    }


def backup_health(home: Path, db: ContinuityDB) -> dict[str, Any]:
    inspection = inspect_home_pack(home)
    parent = home.parent
    candidates = sorted(
        parent.glob(f"{home.name}*.vestigia.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    newest = candidates[0] if candidates else None
    disk = shutil.disk_usage(home)
    temp_fragments = sorted(
        str(path.name)
        for path in parent.glob(f".{home.name}*.tmp-*")
        if path.is_file()
    )
    return {
        "packable": inspection["packable"],
        "issues": inspection["issues"],
        "file_count": inspection["manifest"]["file_count"],
        "estimated_bytes": inspection["manifest"]["total_size_bytes"],
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
        "free_bytes": int(disk.free),
        "newest_local_pack": (
            {
                "path": str(newest.resolve()),
                "size_bytes": newest.stat().st_size,
                "modified_at": datetime.fromtimestamp(
                    newest.stat().st_mtime, tz=UTC
                ).isoformat(),
            }
            if newest
            else None
        ),
        "orphaned_temp_fragments": temp_fragments,
        "database_checkpoint_available": hasattr(db, "checkpoint"),
    }


def build_doctor_report(
    config: ResolvedConfig,
    db: ContinuityDB,
    *,
    bells: BellService,
    house: Any,
    images: Any,
    refresh_index: bool = True,
) -> dict[str, Any]:
    home = validate_home(config.home_path)
    if refresh_index:
        index = house.refresh_index()
    else:
        index = {"refreshed": False}
    db.check_fts5()
    database = database_health(db)
    dependencies = dependency_status(config)
    operations = operation_health(config, db)
    backup = backup_health(home, db)
    required_missing = [
        name
        for name, status in dependencies["required"].items()
        if not status["installed"] or not status["importable"]
    ]
    overall = "ok"
    if database["integrity"] != "ok" or database["foreign_key_violations"]:
        overall = "error"
    elif required_missing or operations["image_jobs"]["stale_running"]:
        overall = "warning"
    return {
        "schema_version": "vestigia.doctor.v0.2",
        "generated_at": utc_now_iso(),
        "overall": overall,
        "runtime": {
            "version": __version__,
            "home": str(home),
            "resident_id_hash": _hash_label(config.get("resident.id")),
            "room_id_hash": _hash_label(config.get("room.id")),
            "active_resident_count": len(config.get("room.active_resident_ids", [])),
        },
        "composition": __import__(
            "vestigia.composition", fromlist=["composition_plan"]
        ).composition_plan(),
        "database": database,
        "dependencies": dependencies,
        "backup": backup,
        "operations": operations,
        "credentials": {
            "openai_key": "present" if config.secret("OPENAI_API_KEY") else "absent",
            "discord_token": "present" if config.secret("DISCORD_BOT_TOKEN") else "absent",
            "values_exported": False,
        },
        "discord": {
            "enabled": bool(config.get("interface.discord.enabled")),
            "allowed_users": len(config.get("discord.allowed_user_ids", [])),
            "allowed_guild_channels": len(config.get("discord.allowed_channel_ids", [])),
            "dms_allowed": bool(config.get("discord.allow_dms", True)),
            "rejection_logging": bool(config.get("discord.log_rejections", False)),
            "activity_window": bool(config.get("discord.activity_window", False)),
        },
        "bells": {
            "enabled": bool(config.get("bells.enabled", True)),
            "timezone": config.get("bells.timezone"),
            "quiet_hours": [
                config.get("bells.quiet_start"),
                config.get("bells.quiet_end"),
            ],
            "visible": len(bells.list()),
            "active": sum(item.status == "active" for item in bells.list()),
        },
        "house": {
            "enabled": bool(config.get("house.enabled", True)),
            "index": index,
            **house.private_turn_budget(),
            "writable_roots": config.get("house.writable_roots", ["workspace"]),
            "bookmarks": len(house.legible.list_bookmarks(limit=200)),
            "pinned_receipts": len(
                house.legible.list_receipts(limit=200, pinned_only=True)
            ),
            "capability_count": len(house.registry.describe()),
        },
        "images": images.diagnostics(),
        "required_dependency_failures": required_missing,
    }


def format_doctor_text(report: dict[str, Any]) -> str:
    database = report["database"]
    backup = report["backup"]
    operations = report["operations"]
    lines = [
        f"VESTIGIA doctor: {report['overall'].upper()}",
        f"Runtime: {report['runtime']['version']}",
        f"Schema: {database.get('schema_version') or 'unknown'}",
        f"SQLite: {database['sqlite_version']} · integrity={database['integrity']} · "
        f"foreign_keys={database['foreign_key_violations']}",
        f"Backup: packable={backup['packable']} · files={backup['file_count']} · "
        f"estimated_bytes={backup['estimated_bytes']}",
        f"Image jobs: stale_running={len(operations['image_jobs']['stale_running'])} · "
        f"unnotified={operations['image_jobs']['unnotified_terminal']}",
        f"Interface events: {json.dumps(operations['interface_events']['by_status'], sort_keys=True)}",
        f"Bells: due_now={operations['bells']['due_now']} · "
        f"failed_deliveries={operations['bells']['failed_delivery_events']}",
    ]
    missing = report.get("required_dependency_failures") or []
    if missing:
        lines.append("Required dependency failures: " + ", ".join(missing))
    if backup["issues"]:
        lines.append("Pack issues: " + "; ".join(backup["issues"]))
    return "\n".join(lines)


def _recent_failed_receipts(db: ContinuityDB, resident_id: str) -> list[dict[str, Any]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, action, status, turn_id, source_envelope, outward_effect,
                   started_at, completed_at, target_json
            FROM house_receipts
            WHERE resident_id=? AND status='failed'
            ORDER BY rowid DESC LIMIT 50
            """,
            (resident_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            target = json.loads(item.pop("target_json") or "{}")
        except json.JSONDecodeError:
            target = {}
        item["target_keys"] = sorted(target)
        result.append(item)
    return result


def write_support_bundle(
    config: ResolvedConfig,
    db: ContinuityDB,
    report: dict[str, Any],
    output: str | Path,
) -> Path:
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    resident_id = str(config.get("resident.id"))
    sanitized_config = _redact(copy.deepcopy(config.data))
    database_inventory = {
        "schema_version": report["database"].get("schema_version"),
        "table_counts": report["database"].get("table_counts", {}),
        "integrity": report["database"].get("integrity"),
        "foreign_key_violations": report["database"].get("foreign_key_violations"),
    }
    manifest = {
        "schema_version": SUPPORT_BUNDLE_SCHEMA,
        "created_at": utc_now_iso(),
        "privacy": "redacted-diagnostics-only",
        "included": [
            "doctor-report.json",
            "effective-config.redacted.yaml",
            "database-inventory.json",
            "recent-failed-receipts.json",
            "MANIFEST.json",
        ],
        "excluded": [
            "environment secret values",
            "raw SQLite database",
            "transcripts and message content",
            "memory content",
            "identity prose",
            "image bytes and prompts",
            "full action results",
        ],
    }
    payloads = {
        "MANIFEST.json": json.dumps(manifest, indent=2, sort_keys=True),
        "doctor-report.json": json.dumps(
            _redact(report), ensure_ascii=False, indent=2, sort_keys=True, default=str
        ),
        "effective-config.redacted.yaml": yaml.safe_dump(
            sanitized_config, sort_keys=False, allow_unicode=True, width=100
        ),
        "database-inventory.json": json.dumps(
            database_inventory, indent=2, sort_keys=True
        ),
        "recent-failed-receipts.json": json.dumps(
            _recent_failed_receipts(db, resident_id),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ),
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.tmp-",
            suffix=".zip",
            dir=target.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in payloads.items():
                archive.writestr(name, content)
        with zipfile.ZipFile(temp_path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("Support bundle integrity verification failed")
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return target


def support_bundle_receipt(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": SUPPORT_BUNDLE_SCHEMA,
        "privacy": "redacted-diagnostics-only",
    }
