from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from vestigia.bells import BellService
from vestigia.cli import build_parser
from vestigia.curation import Curator
from vestigia.diagnostics import build_doctor_report
from vestigia.images import ImageService
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.library_window_hardening import store_source_guarded
from vestigia.library_window_transport import FetchResult, extract_readable
from vestigia.research_maintenance import (
    apply_research_gc_plan,
    build_research_gc_plan,
    inspect_research_cas,
)


def _house(tmp_path: Path) -> HousePort:
    home = initialize_home(tmp_path / "home", name="CAS Maintenance Test", glyph="🧹")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    return HousePort(config, db)


def _fetch(body: bytes, *, url: str = "https://example.com/source") -> FetchResult:
    return FetchResult(
        original_url=url,
        final_url=url,
        status=200,
        media_type="text/html",
        charset="utf-8",
        body=body,
        redirect_chain=(),
        response_headers={"content-type": "text/html"},
        elapsed_ms=5,
    )


def _orphan(home: Path, body: bytes, *, suffix: str = ".raw", age_hours: float = 48.0) -> Path:
    digest = hashlib.sha256(body).hexdigest()
    path = home / "research" / "sources" / f"{digest}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    then = time.time() - age_hours * 3600.0
    os.utime(path, (then, then))
    return path


def test_referenced_source_is_never_a_gc_candidate(tmp_path: Path) -> None:
    house = _house(tmp_path)
    fetched = _fetch(b"<html><body>Referenced evidence</body></html>")
    source = store_source_guarded(
        house,
        fetched=fetched,
        extraction=extract_readable(fetched),
        search_provenance=None,
    )
    inventory = inspect_research_cas(house.home, house.db, min_age_hours=0)
    candidate_paths = {item["path"] for item in inventory["gc_candidates"]}
    assert source["content_hash"]
    assert not candidate_paths
    assert inventory["status"] == "ok"
    assert inventory["missing_references"] == []
    assert inventory["hash_mismatches"] == []


def test_young_unreferenced_blob_is_grace_protected(tmp_path: Path) -> None:
    house = _house(tmp_path)
    path = _orphan(house.home, b"young orphan", age_hours=0)
    inventory = inspect_research_cas(house.home, house.db, min_age_hours=24)
    assert [item["path"] for item in inventory["young_unreferenced"]] == [
        path.relative_to(house.home).as_posix()
    ]
    assert inventory["gc_candidates"] == []


def test_malformed_and_corrupt_unreferenced_files_are_not_auto_deleted(tmp_path: Path) -> None:
    house = _house(tmp_path)
    root = house.home / "research" / "sources"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("not CAS", encoding="utf-8")
    fake_digest = "0" * 64
    corrupt = root / f"{fake_digest}.raw"
    corrupt.write_bytes(b"does not hash to zeros")
    then = time.time() - 72 * 3600
    os.utime(corrupt, (then, then))

    plan = build_research_gc_plan(house.home, house.db, min_age_hours=24)
    inventory = inspect_research_cas(house.home, house.db, min_age_hours=24)
    assert plan["candidate_count"] == 0
    assert inventory["unexpected_entries"]
    assert inventory["corrupt_unreferenced"]
    assert inventory["status"] == "warning"


def test_aged_valid_orphan_produces_deterministic_hash_bound_plan(tmp_path: Path) -> None:
    house = _house(tmp_path)
    path = _orphan(house.home, b"old valid orphan", age_hours=48)
    first = build_research_gc_plan(house.home, house.db, min_age_hours=24)
    second = build_research_gc_plan(house.home, house.db, min_age_hours=24)
    assert first["plan_hash"] == second["plan_hash"]
    assert first["candidate_count"] == 1
    assert first["candidates"][0]["path"] == path.relative_to(house.home).as_posix()
    assert first["candidate_bytes"] == len(b"old valid orphan")


def test_apply_requires_runtime_stopped_and_exact_plan_hash(tmp_path: Path) -> None:
    house = _house(tmp_path)
    _orphan(house.home, b"old valid orphan", age_hours=48)
    plan = build_research_gc_plan(house.home, house.db, min_age_hours=24)

    with pytest.raises(PermissionError, match="Runtime is stopped"):
        apply_research_gc_plan(
            house.home,
            house.db,
            expected_plan_hash=plan["plan_hash"],
            runtime_stopped=False,
            min_age_hours=24,
        )
    with pytest.raises(RuntimeError, match="plan changed"):
        apply_research_gc_plan(
            house.home,
            house.db,
            expected_plan_hash="sha256:" + "f" * 64,
            runtime_stopped=True,
            min_age_hours=24,
        )


def test_candidate_becoming_referenced_invalidates_old_plan(tmp_path: Path) -> None:
    house = _house(tmp_path)
    body = b"<html><body>Reused orphan bytes</body></html>"
    _orphan(house.home, body, age_hours=48)
    old_plan = build_research_gc_plan(house.home, house.db, min_age_hours=24)
    assert old_plan["candidate_count"] == 1

    fetched = _fetch(body)
    store_source_guarded(
        house,
        fetched=fetched,
        extraction=extract_readable(fetched),
        search_provenance=None,
    )

    with pytest.raises(RuntimeError, match="plan changed"):
        apply_research_gc_plan(
            house.home,
            house.db,
            expected_plan_hash=old_plan["plan_hash"],
            runtime_stopped=True,
            min_age_hours=24,
        )


def test_successful_apply_deletes_only_planned_orphan_and_writes_receipt(tmp_path: Path) -> None:
    house = _house(tmp_path)
    orphan = _orphan(house.home, b"collect me", age_hours=48)
    young = _orphan(house.home, b"not yet", age_hours=1)
    plan = build_research_gc_plan(house.home, house.db, min_age_hours=24)
    assert plan["candidate_count"] == 1

    receipt = apply_research_gc_plan(
        house.home,
        house.db,
        expected_plan_hash=plan["plan_hash"],
        runtime_stopped=True,
        min_age_hours=24,
    )

    assert receipt["status"] == "completed"
    assert receipt["deleted_count"] == 1
    assert receipt["bytes_reclaimed"] == len(b"collect me")
    assert not orphan.exists()
    assert young.exists()
    receipt_path = Path(receipt["receipt_path"])
    assert receipt_path.is_file()
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["content_included"] is False
    assert payload["plan_hash"] == plan["plan_hash"]
    assert "collect me" not in receipt_path.read_text(encoding="utf-8")


def test_doctor_surfaces_research_cas_without_mutating_bytes(tmp_path: Path) -> None:
    house = _house(tmp_path)
    orphan = _orphan(house.home, b"doctor sees me", age_hours=48)
    bells = BellService(house.db, house.resident_id, house.room_id)
    images = ImageService(house.config, house.db, fake=True)
    report = build_doctor_report(
        house.config,
        house.db,
        bells=bells,
        house=house,
        images=images,
        refresh_index=False,
    )
    assert report["research_cas"]["status"] == "warning"
    assert report["research_cas"]["gc_candidates"]
    assert orphan.exists()


def test_research_gc_cli_is_two_step_and_explicit() -> None:
    parser = build_parser()
    plan = parser.parse_args(["research-gc", "C:/home"])
    assert plan.apply is False
    assert plan.min_age_hours == 24.0
    apply = parser.parse_args([
        "research-gc",
        "C:/home",
        "--apply",
        "--plan-hash",
        "sha256:" + "a" * 64,
        "--runtime-stopped",
    ])
    assert apply.apply is True
    assert apply.runtime_stopped is True



def test_missing_custody_table_with_cas_bytes_is_doctor_error_and_not_collectible(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "bare-home", name="Bare CAS Test", glyph="🧯")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    orphan = _orphan(home, b"bytes without custody table", age_hours=72)

    inventory = inspect_research_cas(home, db, min_age_hours=24)
    plan = build_research_gc_plan(home, db, min_age_hours=24)

    assert orphan.exists()
    assert inventory["status"] == "error"
    assert inventory["custody_table_available"] is False
    assert inventory["custody_table_issue"]
    assert inventory["gc_allowed"] is False
    assert plan["candidate_count"] == 0
    assert plan["integrity_errors_present"] is True
    with pytest.raises(RuntimeError, match="custody table is unavailable"):
        apply_research_gc_plan(
            home,
            db,
            expected_plan_hash=plan["plan_hash"],
            runtime_stopped=True,
            min_age_hours=24,
        )


def test_shared_cas_paths_are_verified_and_counted_once(tmp_path: Path) -> None:
    house = _house(tmp_path)
    fetched = _fetch(b"<html><body>Same evidence</body></html>")
    extraction = extract_readable(fetched)
    first = store_source_guarded(
        house, fetched=fetched, extraction=extraction, search_provenance=None
    )
    second = store_source_guarded(
        house, fetched=fetched, extraction=extraction, search_provenance=None
    )
    assert first["source_id"] != second["source_id"]

    inventory = inspect_research_cas(house.home, house.db, min_age_hours=24)
    expected_unique = 1 + (1 if extraction.text else 0)
    assert inventory["reference_count"] == expected_unique * 2
    assert inventory["unique_referenced_paths"] == expected_unique
    assert len(inventory["verified_referenced"]) == expected_unique
    assert inventory["status"] == "ok"
