from __future__ import annotations

from pathlib import Path

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.library_window_hardening import store_source_guarded
from vestigia.library_window_transport import FetchResult, extract_readable
from vestigia.research_maintenance import inspect_research_cas


def _house(tmp_path: Path) -> HousePort:
    home = initialize_home(tmp_path / "home", name="CAS Shared Ref Test", glyph="🧹")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    return HousePort(config, db)


def _fetch(body: bytes) -> FetchResult:
    return FetchResult(
        original_url="https://example.com/source",
        final_url="https://example.com/source",
        status=200,
        media_type="text/html",
        charset="utf-8",
        body=body,
        redirect_chain=(),
        response_headers={"content-type": "text/html"},
        elapsed_ms=5,
    )


def test_shared_cas_observation_is_cached_but_every_reference_is_validated(tmp_path: Path) -> None:
    house = _house(tmp_path)
    fetched = _fetch(b"<html><body>Shared custody</body></html>")
    extraction = extract_readable(fetched)
    first = store_source_guarded(
        house, fetched=fetched, extraction=extraction, search_provenance=None
    )
    second = store_source_guarded(
        house, fetched=fetched, extraction=extraction, search_provenance=None
    )
    assert first["source_id"] != second["source_id"]

    # Corrupt only the later DB reference. The physical CAS bytes remain valid and
    # shared; a path-dedupe implementation that skips later custody rows would miss it.
    with house.db.connect() as connection:
        connection.execute(
            "UPDATE library_sources SET raw_size_bytes=raw_size_bytes+1 WHERE id=?",
            (second["source_id"],),
        )

    inventory = inspect_research_cas(house.home, house.db, min_age_hours=24)
    assert inventory["status"] == "error"
    assert any(
        item["source_id"] == second["source_id"] and item["kind"] == "raw"
        for item in inventory["hash_mismatches"]
    )

    # Physical bytes are still hashed/deduplicated once per path; custody rows are not.
    assert inventory["unique_referenced_paths"] == len(inventory["verified_referenced"])
