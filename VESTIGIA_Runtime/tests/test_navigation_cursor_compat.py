from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HouseCursorError, HouseCursorExpiredError, HousePort


def _house(tmp_path: Path) -> HousePort:
    home = initialize_home(tmp_path / "home", name="Cursor Compat Canary", glyph="🏮")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    return HousePort(config, db)


def test_unknown_or_closed_cursor_preserves_keyerror_family_and_receipt(tmp_path: Path) -> None:
    house = _house(tmp_path)

    with pytest.raises(HouseCursorError) as caught:
        house.dispatch({"action": "continue", "cursor": "house_cursor_missing"})

    assert isinstance(caught.value, KeyError)
    assert caught.value.house_error_code == "cursor_unknown_or_closed"
    assert caught.value.house_suggested_retry["action"] == "read"
    receipt = house.legible.inspect_receipt(str(caught.value.house_receipt_id))
    assert receipt["result"]["error_code"] == "cursor_unknown_or_closed"
    assert receipt["result"]["suggested_retry"]["action"] == "read"


def test_expired_cursor_preserves_expiry_family_and_reopen_position(tmp_path: Path) -> None:
    house = _house(tmp_path)
    cursor_id = "house_cursor_expired_fixture"
    now = datetime.now(UTC)
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO house_cursors
            (id, resident_id, path, next_chunk, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                cursor_id,
                house.resident_id,
                "imports/expired-fixture.md",
                17,
                (now - timedelta(days=2)).isoformat(),
                (now - timedelta(days=1)).isoformat(),
            ),
        )

    with pytest.raises(HouseCursorExpiredError) as caught:
        house.dispatch({"action": "continue", "cursor": cursor_id})

    assert isinstance(caught.value, ValueError)
    assert caught.value.house_error_code == "cursor_expired"
    assert caught.value.house_suggested_retry == {
        "action": "read",
        "path": "imports/expired-fixture.md",
        "chunk": 17,
    }
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM house_cursors WHERE id=?", (cursor_id,)
        ).fetchone()
    assert row["status"] == "expired"
