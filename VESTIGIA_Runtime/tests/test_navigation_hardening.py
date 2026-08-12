from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.navigation_hardening import NavigationStateError


def _house(tmp_path: Path) -> tuple[HousePort, Path]:
    home = initialize_home(tmp_path / "home", name="Navigation Canary", glyph="🏮")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    return HousePort(config, db), home


def _long_document(house: HousePort, home: Path) -> tuple[str, list[int], int]:
    relative = "imports/navigation-fixture.md"
    path = home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Sketch\n\n"
        + ("sketch-alpha " * 5000)
        + "\n\n# Other\n\n"
        + ("other-beta " * 1200),
        encoding="utf-8",
    )
    house.refresh_index()
    with house.db.connect() as connection:
        sketch_rows = connection.execute(
            """
            SELECT chunk_index FROM house_chunks
            WHERE path=? AND heading='Sketch'
            ORDER BY chunk_index
            """,
            (relative,),
        ).fetchall()
        other = connection.execute(
            """
            SELECT MIN(chunk_index) AS first_chunk FROM house_chunks
            WHERE path=? AND heading='Other'
            """,
            (relative,),
        ).fetchone()
    sketch_chunks = [int(row["chunk_index"]) for row in sketch_rows]
    assert len(sketch_chunks) >= 3
    return relative, sketch_chunks, int(other["first_chunk"])


def _bookmark(
    house: HousePort,
    *,
    relative: str,
    heading: str | None = None,
    chunk: int | None = None,
    cursor: str | None = None,
) -> str:
    stat = house.dispatch({"action": "stat", "path": relative})
    payload: dict[str, object] = {
        "action": "bookmark.add",
        "reference": stat["object_id"],
        "label": "navigation fixture",
    }
    if heading is not None:
        payload["heading"] = heading
    if chunk is not None:
        payload["chunk"] = chunk
    if cursor is not None:
        payload["cursor"] = cursor
    saved = house.dispatch(payload)
    return str(saved["bookmark_id"])


def test_bookmark_chunk_is_authoritative_when_heading_is_also_present(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, sketch_chunks, _other = _long_document(house, home)
    target = sketch_chunks[-2]
    bookmark_id = _bookmark(
        house,
        relative=relative,
        heading="Sketch",
        chunk=target,
    )

    opened = house.dispatch(
        {"action": "bookmark.open", "bookmark_id": bookmark_id, "max_tokens": 140}
    )

    nav = opened["navigation"]
    assert nav["resolution_route"] == "chunk"
    assert nav["requested"]["location"]["chunk"] == target
    assert nav["resolved"]["start_chunk"] == target
    assert nav["returned"]["first_chunk"] == target
    assert nav["proof"]["requested_chunk_honored"] is True
    assert nav["proof"]["requested_heading_honored"] is True
    assert f"chunk {target}]" in opened["opened"]["text"]
    assert "chunk 0]" not in opened["opened"]["text"]


def test_heading_chunk_mismatch_fails_closed_instead_of_falling_back(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, sketch_chunks, _other = _long_document(house, home)
    bookmark_id = _bookmark(
        house,
        relative=relative,
        heading="Other",
        chunk=sketch_chunks[-2],
    )

    with pytest.raises(NavigationStateError) as caught:
        house.dispatch(
            {"action": "bookmark.open", "bookmark_id": bookmark_id, "max_tokens": 140}
        )
    assert caught.value.house_error_code == "navigation_heading_chunk_mismatch"
    assert getattr(caught.value, "house_receipt_id", None)


def test_heading_only_bookmark_reports_concrete_resolved_chunk(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, other_chunk = _long_document(house, home)
    bookmark_id = _bookmark(house, relative=relative, heading="Other")

    opened = house.dispatch(
        {"action": "bookmark.open", "bookmark_id": bookmark_id, "max_tokens": 140}
    )

    nav = opened["navigation"]
    assert nav["resolution_route"] == "heading:exact"
    assert nav["resolved"]["start_chunk"] == other_chunk
    assert nav["returned"]["first_chunk"] == other_chunk
    assert nav["resolved"]["heading"] == "Other"
    assert nav["proof"]["requested_heading_honored"] is True


def test_cursor_only_bookmark_resumes_cursor_instead_of_document_start(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, _other = _long_document(house, home)
    first = house.dispatch(
        {"action": "read", "path": relative, "chunk": 0, "max_tokens": 140}
    )
    cursor_id = str(first["cursor"])
    assert cursor_id
    with house.db.connect() as connection:
        source = connection.execute(
            "SELECT next_chunk FROM house_cursors WHERE id=?", (cursor_id,)
        ).fetchone()
    expected = int(source["next_chunk"])
    bookmark_id = _bookmark(house, relative=relative, cursor=cursor_id)

    opened = house.dispatch(
        {"action": "bookmark.open", "bookmark_id": bookmark_id, "max_tokens": 140}
    )

    nav = opened["navigation"]
    assert nav["resolution_route"] == "cursor"
    assert nav["source_cursor"]["id"] == cursor_id
    assert nav["returned"]["first_chunk"] == expected
    assert nav["proof"]["source_cursor_next_chunk_honored"] is True
    assert nav["bookmark"]["updated"] is False


def test_legacy_read_bookmark_shortcut_uses_same_exact_chunk_semantics(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, sketch_chunks, _other = _long_document(house, home)
    target = sketch_chunks[-2]
    bookmark_id = _bookmark(
        house,
        relative=relative,
        heading="Sketch",
        chunk=target,
    )

    reopened = house.dispatch(
        {"action": "read", "bookmark_id": bookmark_id, "max_tokens": 140}
    )

    nav = reopened["navigation"]
    assert nav["resolution_route"] == "chunk"
    assert nav["requested"]["bookmark_id"] == bookmark_id
    assert nav["returned"]["first_chunk"] == target
    assert nav["proof"]["requested_chunk_honored"] is True
    assert f"chunk {target}]" in reopened["text"]


def test_legacy_read_cursor_bookmark_resumes_cursor_instead_of_chunk_zero(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, _other = _long_document(house, home)
    first = house.dispatch(
        {"action": "read", "path": relative, "chunk": 0, "max_tokens": 140}
    )
    cursor_id = str(first["cursor"])
    expected = int(first["navigation"]["new_cursor"]["next_chunk"])
    bookmark_id = _bookmark(house, relative=relative, cursor=cursor_id)

    reopened = house.dispatch(
        {"action": "read", "bookmark_id": bookmark_id, "max_tokens": 140}
    )

    nav = reopened["navigation"]
    assert nav["resolution_route"] == "cursor"
    assert nav["source_cursor"]["id"] == cursor_id
    assert nav["returned"]["first_chunk"] == expected
    assert nav["new_cursor"] is None or nav["new_cursor"]["provenance"]["bookmark_id"] == bookmark_id


def test_continue_rejects_unbound_legacy_cursor(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, _other = _long_document(house, home)
    legacy_cursor = "house_cursor_legacy_fixture"
    now = datetime.now(UTC)
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO house_cursors
            (id, resident_id, path, next_chunk, status, created_at, expires_at)
            VALUES (?, ?, ?, 1, 'active', ?, ?)
            """,
            (
                legacy_cursor,
                house.resident_id,
                relative,
                now.isoformat(),
                (now + timedelta(days=1)).isoformat(),
            ),
        )

    with pytest.raises(NavigationStateError) as caught:
        house.dispatch({"action": "continue", "cursor": legacy_cursor})
    assert caught.value.house_error_code == "cursor_provenance_missing"


def test_continue_reports_source_range_new_cursor_and_no_bookmark_advance(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, _other = _long_document(house, home)
    first = house.dispatch(
        {"action": "read", "path": relative, "chunk": 0, "max_tokens": 140}
    )
    source_cursor = str(first["cursor"])
    expected = int(first["navigation"]["new_cursor"]["next_chunk"])

    continued = house.dispatch(
        {"action": "continue", "cursor": source_cursor, "max_tokens": 140}
    )

    nav = continued["navigation"]
    assert nav["source_cursor"]["id"] == source_cursor
    assert nav["returned"]["first_chunk"] == expected
    assert nav["proof"]["source_cursor_next_chunk_honored"] is True
    assert nav["bookmark"]["updated"] is False
    assert nav["bookmark"]["created"] is False
    assert nav["new_cursor"] is None or nav["new_cursor"]["provenance"]["source_cursor_id"] == source_cursor


def test_continue_fails_if_source_hash_changed_after_cursor_creation(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, _sketch_chunks, _other = _long_document(house, home)
    first = house.dispatch(
        {"action": "read", "path": relative, "chunk": 0, "max_tokens": 140}
    )
    cursor_id = str(first["cursor"])
    path = home / relative
    path.write_text(path.read_text(encoding="utf-8") + "\nchanged source\n", encoding="utf-8")

    with pytest.raises(NavigationStateError) as caught:
        house.dispatch({"action": "continue", "cursor": cursor_id})
    assert caught.value.house_error_code == "cursor_source_hash_mismatch"
    with house.db.connect() as connection:
        status = connection.execute(
            "SELECT status FROM house_cursors WHERE id=?", (cursor_id,)
        ).fetchone()
    assert status["status"] == "active"


def test_new_bookmark_is_hash_bound_and_receipt_preserves_navigation_proof(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, sketch_chunks, _other = _long_document(house, home)
    target = sketch_chunks[-2]
    bookmark_id = _bookmark(house, relative=relative, heading="Sketch", chunk=target)
    stored = house.legible.bookmark(bookmark_id)
    assert stored["location"]["file_hash"]

    opened = house.dispatch(
        {"action": "bookmark.open", "bookmark_id": bookmark_id, "max_tokens": 140}
    )
    receipt = house.legible.inspect_receipt(str(opened["receipt_id"]))
    result = receipt["result"]
    assert result["navigation"]["returned"]["first_chunk"] == target
    assert result["navigation"]["proof"]["requested_chunk_honored"] is True
    assert result["navigation"]["bookmark"]["source_hash_binding"] == "verified"


def test_workbench_continue_uses_corrected_bookmark_resolution(tmp_path: Path) -> None:
    house, home = _house(tmp_path)
    relative, sketch_chunks, _other = _long_document(house, home)
    target = sketch_chunks[-2]
    bookmark_id = _bookmark(house, relative=relative, heading="Sketch", chunk=target)

    desk = house.dispatch({"action": "workbench.view", "lane": "continue", "limit": 20})
    card = next(
        item for item in desk["cards"]
        if str(item.get("source", {}).get("bookmark_id") or "") == bookmark_id
    )
    acted = house.dispatch(
        {
            "action": "workbench.act",
            "card_id": card["card_id"],
            "action_id": "continue",
            "max_tokens": 140,
        }
    )

    assert acted["underlying_action"] == "bookmark.open"
    assert acted["outcome"]["navigation"]["returned"]["first_chunk"] == target
    assert acted["outcome"]["navigation"]["proof"]["requested_chunk_honored"] is True
