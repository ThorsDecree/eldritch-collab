from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.library_window_hardening import (
    authorize_network_read,
    discard_notebook_atomic,
    ensure_policy_schema,
    quota_summary,
    read_source_chunk_guarded,
    record_search_guarded,
    revoke_source,
    store_source_guarded,
)
from vestigia.library_window_store import create_notebook
from vestigia.library_window_transport import FetchResult, extract_readable


def _house(tmp_path: Path, *, web_enabled: bool = False) -> HousePort:
    home = initialize_home(tmp_path / "home", name="Hardening Test", glyph="🔭")
    if web_enabled:
        with patch.dict(os.environ, {"VESTIGIA_WEB_ENABLED": "true"}):
            config = load_config(home)
    else:
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


def _turn(house: HousePort, text: str) -> str:
    return house.db.add_turn(
        resident_id=house.resident_id,
        room_id=house.room_id,
        speaker_role="user",
        speaker_id="local-user",
        content=text,
        interface="cli",
    )


def test_web_is_disabled_by_default_but_local_research_stays_available(tmp_path: Path) -> None:
    house = _house(tmp_path)
    assert house.registry.describe("web.search")[0]["callable_now"] is False
    assert house.registry.describe("web.open")[0]["callable_now"] is False
    assert house.registry.describe("source.capsule")[0]["callable_now"] is True
    assert house.registry.describe("research.notebook")[0]["callable_now"] is True


def test_network_query_must_be_bounded_by_current_participant_turn(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    turn_id = _turn(house, "Search for SQLite WAL documentation.")
    context = {"turn_id": turn_id, "interface": "cli", "invocation": "conversation"}
    authorize_network_read(house, {"action": "web.search", "query": "SQLite WAL"}, context)
    with pytest.raises(PermissionError, match="not bounded"):
        authorize_network_read(
            house,
            {"action": "web.search", "query": "SQLite WAL private-secret-4471"},
            context,
        )


def test_direct_open_requires_exact_url_in_participant_turn(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    turn_id = _turn(house, "Open https://example.com/source for me.")
    context = {"turn_id": turn_id, "interface": "cli", "invocation": "conversation"}
    authorize_network_read(house, {"action": "web.open", "url": "https://example.com/source"}, context)
    with pytest.raises(PermissionError, match="exact URL"):
        authorize_network_read(
            house,
            {"action": "web.open", "url": "https://example.com/source?leak=private-secret"},
            context,
        )


def test_search_lineage_is_durable_on_source_capsule(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    ensure_policy_schema(house)
    turn_id = _turn(house, "Search for paper.")
    fetched_search = _fetch(b"search", url="https://html.duckduckgo.com/html/?q=paper")
    results = [{"rank": 1, "title": "Paper", "url": "https://example.com/source", "snippet": "x"}]
    search_id, _ = record_search_guarded(
        house,
        query="paper",
        results=results,
        fetched=fetched_search,
        requested_turn_id=turn_id,
    )
    source_fetch = _fetch(b"<html><body>Evidence</body></html>")
    source = store_source_guarded(
        house,
        fetched=source_fetch,
        extraction=extract_readable(source_fetch),
        search_provenance={"search_id": search_id, "rank": 1},
    )
    assert source["discovery_provenance"]["search_id"] == search_id
    assert source["discovery_provenance"]["rank"] == 1
    assert source["discovery_provenance"]["query_hash"]



def test_source_row_lineage_and_stored_event_are_one_transaction(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    ensure_policy_schema(house)
    turn_id = _turn(house, "Search for paper.")
    fetched_search = _fetch(b"search", url="https://html.duckduckgo.com/html/?q=paper")
    search_id, _ = record_search_guarded(
        house,
        query="paper",
        results=[{"rank": 1, "title": "Paper", "url": "https://example.com/source", "snippet": "x"}],
        fetched=fetched_search,
        requested_turn_id=turn_id,
    )

    # Force the custody-event insert to fail after the source INSERT has executed.
    # If source creation and lineage are not in the same transaction, a partial source
    # row would survive this uniqueness failure.
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO library_source_events
            (id, resident_id, source_id, event_type, payload_hash, created_at)
            VALUES ('source_event_collision', ?, 'sentinel', 'sentinel', 'sentinel', '2000-01-01T00:00:00Z')
            """,
            (house.resident_id,),
        )

    fetched = _fetch(b"<html><body>Atomic evidence</body></html>")
    with patch("vestigia.library_window_hardening.new_id", return_value="source_event_collision"):
        with pytest.raises(sqlite3.IntegrityError):
            store_source_guarded(
                house,
                fetched=fetched,
                extraction=extract_readable(fetched),
                search_provenance={"search_id": search_id, "rank": 1},
            )

    with house.db.connect() as connection:
        source_rows = connection.execute(
            "SELECT id FROM library_sources WHERE resident_id=?",
            (house.resident_id,),
        ).fetchall()
        stored_events = connection.execute(
            "SELECT source_id FROM library_source_events WHERE resident_id=? AND event_type='stored'",
            (house.resident_id,),
        ).fetchall()
    assert source_rows == []
    assert stored_events == []

def test_revoked_source_cannot_be_read_through_guarded_path(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    fetched = _fetch(b"<html><body>Evidence</body></html>")
    source = store_source_guarded(
        house,
        fetched=fetched,
        extraction=extract_readable(fetched),
        search_provenance=None,
    )
    revoked = revoke_source(house, source_id=source["source_id"], reason="resident request")
    assert revoked["retrieval_eligible"] is False
    with pytest.raises(PermissionError, match="revoked"):
        read_source_chunk_guarded(house, source_id=source["source_id"])


def test_source_quota_is_aggregate_not_only_per_request(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    house.config.data["web"]["max_sources"] = 1
    first = _fetch(b"<html><body>one</body></html>", url="https://example.com/one")
    store_source_guarded(house, fetched=first, extraction=extract_readable(first), search_provenance=None)
    assert quota_summary(house)["source_count"] == 1
    second = _fetch(b"<html><body>two</body></html>", url="https://example.com/two")
    with pytest.raises(PermissionError, match="source-count quota"):
        store_source_guarded(house, fetched=second, extraction=extract_readable(second), search_provenance=None)


def test_discard_state_and_event_commit_together(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook = create_notebook(house, title="Atomic discard")
    notebook_id = notebook["notebook_id"]
    result = discard_notebook_atomic(house, notebook_id=notebook_id)
    assert result["status"] == "discarded"
    with house.db.connect() as connection:
        row = connection.execute("SELECT id FROM research_notebooks WHERE id=?", (notebook_id,)).fetchone()
        event = connection.execute(
            "SELECT event_type FROM research_notebook_events WHERE notebook_id=? ORDER BY rowid DESC LIMIT 1",
            (notebook_id,),
        ).fetchone()
    assert row is None
    assert event is not None and event["event_type"] == "discarded"
