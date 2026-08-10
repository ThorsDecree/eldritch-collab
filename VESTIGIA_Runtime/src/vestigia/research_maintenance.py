from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .db import ContinuityDB
from .utils import new_id, sha256_file, sha256_text, stable_json, utc_now_iso


CAS_HEALTH_SCHEMA = "vestigia.research-cas-health.v0.1"
GC_PLAN_SCHEMA = "vestigia.research-cas-gc-plan.v0.1"
GC_RECEIPT_SCHEMA = "vestigia.research-cas-gc-receipt.v0.1"
DEFAULT_MIN_AGE_HOURS = 24.0
_CAS_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.(?P<kind>raw|txt)$")


def _root(home: str | Path) -> Path:
    return Path(home).resolve() / "research" / "sources"


def _home_fingerprint(home: Path) -> str:
    return "sha256:" + sha256_text(str(home.resolve()))


def _table_exists(connection: Any, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _source_references(connection: Any) -> list[dict[str, Any]]:
    if not _table_exists(connection, "library_sources"):
        return []
    rows = connection.execute(
        """
        SELECT id, resident_id, raw_path, raw_hash, raw_size_bytes,
               readable_path, readable_hash, readable_size_bytes
        FROM library_sources
        ORDER BY rowid
        """
    ).fetchall()
    references: list[dict[str, Any]] = []
    for row in rows:
        references.append(
            {
                "source_id": str(row["id"]),
                "resident_id_hash": "sha256:"
                + hashlib.sha256(str(row["resident_id"]).encode("utf-8")).hexdigest()[:16],
                "kind": "raw",
                "path": str(row["raw_path"]),
                "expected_hash": str(row["raw_hash"]),
                "expected_size": int(row["raw_size_bytes"]),
            }
        )
        if row["readable_path"]:
            references.append(
                {
                    "source_id": str(row["id"]),
                    "resident_id_hash": "sha256:"
                    + hashlib.sha256(str(row["resident_id"]).encode("utf-8")).hexdigest()[:16],
                    "kind": "readable",
                    "path": str(row["readable_path"]),
                    "expected_hash": str(row["readable_hash"] or ""),
                    "expected_size": int(row["readable_size_bytes"] or 0),
                }
            )
    return references


def _validated_reference_path(home: Path, root: Path, value: str) -> tuple[Path | None, str | None]:
    relative = Path(str(value or ""))
    if not str(value or "").strip():
        return None, "empty stored path"
    if relative.is_absolute() or ".." in relative.parts:
        return None, "stored path is not a safe relative path"
    if len(relative.parts) != 3 or tuple(relative.parts[:2]) != ("research", "sources"):
        return None, "stored path is outside the Research CAS namespace"
    candidate = home / relative
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None, "stored path could not be resolved"
    if resolved.parent != root:
        return None, "stored path resolves outside the Research CAS root"
    if candidate.is_symlink():
        return None, "stored path is a symbolic link"
    return candidate, None


def inspect_research_cas(
    home: str | Path,
    db: ContinuityDB,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
) -> dict[str, Any]:
    """Inspect Research CAS custody without mutating source bytes or database state."""

    home_path = Path(home).resolve()
    root = _root(home_path)
    min_age_seconds = max(0.0, float(min_age_hours) * 3600.0)
    now_ns = time.time_ns()

    with db.connect() as connection:
        table_available = _table_exists(connection, "library_sources")
        references = _source_references(connection)

    referenced_paths: set[str] = set()
    observed_reference_paths: dict[str, dict[str, Any]] = {}
    verified_reference_paths: set[str] = set()
    missing: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    invalid_references: list[dict[str, Any]] = []
    verified_referenced: list[dict[str, Any]] = []
    referenced_bytes = 0
    hash_verified_bytes = 0

    for reference in references:
        path, error = _validated_reference_path(home_path, root, reference["path"])
        if error or path is None:
            invalid_references.append({**reference, "error": error})
            continue
        relative = path.relative_to(home_path).as_posix()
        referenced_paths.add(relative)
        match = _CAS_NAME.fullmatch(path.name)
        if match is None or match.group("digest") != reference["expected_hash"]:
            invalid_references.append(
                {
                    **reference,
                    "error": "stored filename is not content-addressed by the expected hash",
                }
            )
            continue

        observation = observed_reference_paths.get(relative)
        if observation is None:
            if not path.is_file():
                observation = {"missing": True}
            else:
                stat = path.stat()
                actual_hash = sha256_file(path)
                observation = {
                    "missing": False,
                    "actual_hash": actual_hash,
                    "actual_size": int(stat.st_size),
                }
                hash_verified_bytes += int(stat.st_size)
            observed_reference_paths[relative] = observation

        if bool(observation["missing"]):
            missing.append(reference)
            continue

        actual_hash = str(observation["actual_hash"])
        actual_size = int(observation["actual_size"])
        if actual_hash != reference["expected_hash"] or actual_size != reference["expected_size"]:
            mismatches.append(
                {
                    **reference,
                    "actual_hash": actual_hash,
                    "actual_size": actual_size,
                }
            )
            continue

        if relative not in verified_reference_paths:
            verified_reference_paths.add(relative)
            referenced_bytes += actual_size
            verified_referenced.append(
                {
                    "path": relative,
                    "sha256": actual_hash,
                    "size_bytes": actual_size,
                }
            )

    aged_orphans: list[dict[str, Any]] = []
    young_unreferenced: list[dict[str, Any]] = []
    corrupt_unreferenced: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []

    if root.is_dir():
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(home_path).as_posix()
            if path.is_symlink() or not path.is_file():
                unexpected.append({"path": relative, "reason": "non-regular CAS entry"})
                continue
            match = _CAS_NAME.fullmatch(path.name)
            if match is None:
                unexpected.append({"path": relative, "reason": "unrecognized CAS filename"})
                continue
            if relative in referenced_paths:
                continue
            stat = path.stat()
            expected_hash = match.group("digest")
            actual_hash = sha256_file(path)
            hash_verified_bytes += int(stat.st_size)
            age_seconds = max(0.0, (now_ns - int(stat.st_mtime_ns)) / 1_000_000_000)
            item = {
                "path": relative,
                "sha256": actual_hash,
                "filename_hash": expected_hash,
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "age_seconds": round(age_seconds, 3),
            }
            if actual_hash != expected_hash:
                corrupt_unreferenced.append(item)
            elif age_seconds >= min_age_seconds:
                aged_orphans.append(item)
            else:
                young_unreferenced.append(item)

    cas_entries_present = bool(
        aged_orphans or young_unreferenced or corrupt_unreferenced or unexpected
    )
    custody_table_issue = None
    status = "ok"
    if not table_available and cas_entries_present:
        custody_table_issue = (
            "library_sources custody table is unavailable while Research CAS entries exist"
        )
        status = "error"
    elif invalid_references or missing or mismatches:
        status = "error"
    elif aged_orphans or corrupt_unreferenced or unexpected:
        status = "warning"

    return {
        "schema_version": CAS_HEALTH_SCHEMA,
        "available": table_available,
        "status": status,
        "path": str(root),
        "min_age_hours": float(min_age_hours),
        "reference_count": len(references),
        "unique_referenced_paths": len(referenced_paths),
        "verified_referenced": verified_referenced,
        "verified_referenced_bytes": referenced_bytes,
        "hash_verified_bytes": hash_verified_bytes,
        "missing_references": missing,
        "hash_mismatches": mismatches,
        "invalid_references": invalid_references,
        "gc_candidates": aged_orphans,
        "young_unreferenced": young_unreferenced,
        "corrupt_unreferenced": corrupt_unreferenced,
        "unexpected_entries": unexpected,
        "gc_candidate_bytes": sum(int(item["size_bytes"]) for item in aged_orphans),
        "custody_table_available": table_available,
        "custody_table_issue": custody_table_issue,
        "gc_allowed": table_available and custody_table_issue is None,
        "mutation_performed": False,
        "checked_at": utc_now_iso(),
    }


def build_research_gc_plan(
    home: str | Path,
    db: ContinuityDB,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
) -> dict[str, Any]:
    inventory = inspect_research_cas(home, db, min_age_hours=min_age_hours)
    home_path = Path(home).resolve()
    candidates = [
        {
            "path": str(item["path"]),
            "sha256": str(item["sha256"]),
            "size_bytes": int(item["size_bytes"]),
            "mtime_ns": int(item["mtime_ns"]),
        }
        for item in sorted(inventory["gc_candidates"], key=lambda value: str(value["path"]))
    ] if inventory["gc_allowed"] else []
    basis = {
        "schema_version": GC_PLAN_SCHEMA,
        "home_fingerprint": _home_fingerprint(home_path),
        "min_age_hours": float(min_age_hours),
        "candidates": candidates,
    }
    plan_hash = "sha256:" + sha256_text(stable_json(basis))
    return {
        **basis,
        "plan_hash": plan_hash,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item["size_bytes"]) for item in candidates),
        "candidates": candidates,
        "integrity_status": inventory["status"],
        "custody_table_available": bool(inventory["custody_table_available"]),
        "custody_table_issue": inventory["custody_table_issue"],
        "gc_allowed": bool(inventory["gc_allowed"]),
        "integrity_errors_present": bool(
            inventory["custody_table_issue"]
            or inventory["missing_references"]
            or inventory["hash_mismatches"]
            or inventory["invalid_references"]
        ),
        "runtime_stopped_assertion_required": True,
        "generated_at": utc_now_iso(),
    }


def _write_receipt(home: Path, payload: dict[str, Any]) -> Path:
    directory = home / "traces" / "maintenance"
    directory.mkdir(parents=True, exist_ok=True)
    receipt_id = new_id("research_gc")
    target = directory / f"{receipt_id}.json"
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    descriptor, raw = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    temp = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def apply_research_gc_plan(
    home: str | Path,
    db: ContinuityDB,
    *,
    expected_plan_hash: str,
    runtime_stopped: bool,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
) -> dict[str, Any]:
    """Delete only exact aged orphan CAS candidates from a verified offline plan."""

    if not runtime_stopped:
        raise PermissionError(
            "Research CAS GC requires an explicit assertion that the VESTIGIA Runtime is stopped"
        )
    expected = str(expected_plan_hash or "").strip()
    if not expected.startswith("sha256:"):
        raise ValueError("--plan-hash must be the sha256 plan hash from a fresh research-gc plan")

    home_path = Path(home).resolve()
    root = _root(home_path)
    plan = build_research_gc_plan(home_path, db, min_age_hours=min_age_hours)
    if not plan["gc_allowed"]:
        raise RuntimeError("Research CAS custody table is unavailable; refusing garbage collection")
    if plan["integrity_errors_present"]:
        raise RuntimeError(
            "Research CAS has referenced integrity errors; refusing garbage collection until custody is repaired"
        )
    if plan["plan_hash"] != expected:
        raise RuntimeError("Research CAS GC plan changed; generate a fresh plan and review it before apply")

    validated: list[tuple[Path, dict[str, Any]]] = []
    deleted: list[dict[str, Any]] = []
    failure: str | None = None

    try:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            live_references = {
                str(item["path"])
                for item in _source_references(connection)
                if str(item.get("path") or "").strip()
            }
            now_ns = time.time_ns()
            minimum_seconds = max(0.0, float(min_age_hours) * 3600.0)

            for candidate in plan["candidates"]:
                relative = Path(str(candidate["path"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("GC candidate path is no longer safe")
                path = home_path / relative
                resolved = path.resolve(strict=False)
                if resolved.parent != root or path.is_symlink():
                    raise RuntimeError("GC candidate escaped or changed type")
                if str(candidate["path"]) in live_references:
                    raise RuntimeError("GC candidate became referenced after planning")
                if not path.is_file():
                    raise RuntimeError("GC candidate disappeared after planning")
                stat = path.stat()
                if int(stat.st_size) != int(candidate["size_bytes"]) or int(stat.st_mtime_ns) != int(candidate["mtime_ns"]):
                    raise RuntimeError("GC candidate metadata changed after planning")
                age_seconds = max(0.0, (now_ns - int(stat.st_mtime_ns)) / 1_000_000_000)
                if age_seconds < minimum_seconds:
                    raise RuntimeError("GC candidate fell inside the configured age grace window")
                actual_hash = sha256_file(path)
                if actual_hash != str(candidate["sha256"]):
                    raise RuntimeError("GC candidate content changed after planning")
                match = _CAS_NAME.fullmatch(path.name)
                if match is None or match.group("digest") != actual_hash:
                    raise RuntimeError("GC candidate is not a valid content-addressed blob")
                validated.append((path, candidate))

            for path, candidate in validated:
                try:
                    path.unlink()
                except OSError as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    break
                deleted.append(dict(candidate))
    except Exception:
        raise
    finally:
        if deleted or failure:
            receipt_payload = {
                "schema_version": GC_RECEIPT_SCHEMA,
                "created_at": utc_now_iso(),
                "home_fingerprint": _home_fingerprint(home_path),
                "plan_hash": plan["plan_hash"],
                "min_age_hours": float(min_age_hours),
                "runtime_stopped_asserted": True,
                "status": "partial_failure" if failure else "completed",
                "deleted": deleted,
                "deleted_count": len(deleted),
                "bytes_reclaimed": sum(int(item["size_bytes"]) for item in deleted),
                "failure": failure,
                "content_included": False,
            }
            receipt_path = _write_receipt(home_path, receipt_payload)
            receipt_payload["receipt_path"] = str(receipt_path)
            receipt_payload["receipt_sha256"] = sha256_file(receipt_path)
            if failure:
                raise RuntimeError(
                    f"Research CAS GC stopped after a deletion failure; see maintenance receipt {receipt_path}"
                )

    if not deleted:
        receipt_payload = {
            "schema_version": GC_RECEIPT_SCHEMA,
            "created_at": utc_now_iso(),
            "home_fingerprint": _home_fingerprint(home_path),
            "plan_hash": plan["plan_hash"],
            "min_age_hours": float(min_age_hours),
            "runtime_stopped_asserted": True,
            "status": "completed",
            "deleted": [],
            "deleted_count": 0,
            "bytes_reclaimed": 0,
            "failure": None,
            "content_included": False,
        }
        receipt_path = _write_receipt(home_path, receipt_payload)
        receipt_payload["receipt_path"] = str(receipt_path)
        receipt_payload["receipt_sha256"] = sha256_file(receipt_path)

    return receipt_payload
