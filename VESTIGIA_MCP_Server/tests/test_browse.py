from datetime import datetime
from pathlib import Path

import pytest

import vestigia_mcp.browse as browse
from vestigia_mcp.browse import BrowseCursorError, BrowseSessionStore


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _create(store: BrowseSessionStore):
    return store.create(
        kind="archive.read_text",
        source="live",
        path="logs/long.md",
        policy_scope="archive.read_text:test-policy",
        page_bytes=1024,
        snapshot_sha256="a" * 64,
        size=4096,
        now=_at("2026-09-19T12:00:00+00:00"),
    )


def _tamper(token: str) -> str:
    replacement = "x" if token[-1] != "x" else "y"
    return token[:-1] + replacement


def test_cursor_is_signed_expiring_and_operation_bound(tmp_path: Path) -> None:
    store = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)
    session = _create(store)

    claims = store.decode(
        session.cursor,
        "archive.read_text",
        now=_at("2026-09-19T12:00:30+00:00"),
    )
    assert claims["browse_id"] == session.id

    with pytest.raises(BrowseCursorError, match="signature"):
        store.decode(
            _tamper(session.cursor),
            "archive.read_text",
            now=_at("2026-09-19T12:00:30+00:00"),
        )
    with pytest.raises(BrowseCursorError, match="expired"):
        store.decode(
            session.cursor,
            "archive.read_text",
            now=_at("2026-09-19T12:01:01+00:00"),
        )
    with pytest.raises(BrowseCursorError, match="operation"):
        store.decode(
            session.cursor,
            "archive.read_bytes",
            now=_at("2026-09-19T12:00:30+00:00"),
        )


def test_continuation_rejects_scope_or_page_size_mismatch(tmp_path: Path) -> None:
    store = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)
    session = _create(store)
    claims = store.decode(
        session.cursor,
        "archive.read_text",
        now=_at("2026-09-19T12:00:30+00:00"),
    )

    with pytest.raises(BrowseCursorError, match="scope"):
        store.validate_continuation(
            claims,
            policy_scope="archive.read_text:other-policy",
            page_bytes=1024,
        )
    with pytest.raises(BrowseCursorError, match="page size"):
        store.validate_continuation(
            claims,
            policy_scope="archive.read_text:test-policy",
            page_bytes=2048,
        )


def test_session_load_survives_a_second_store(tmp_path: Path) -> None:
    first = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)
    session = _create(first)

    second = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)
    loaded = second.load(session.id)

    assert loaded.id == session.id
    assert loaded.snapshot_sha256 == "a" * 64
    assert loaded.size == 4096


def test_session_store_persists_without_posix_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(browse.os, "fchmod")
    store = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)

    session = _create(store)

    assert store.load(session.id).id == session.id


def test_cursor_input_has_a_size_ceiling(tmp_path: Path) -> None:
    store = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)

    with pytest.raises(BrowseCursorError, match="size ceiling"):
        store.decode("a" * 8_193 + ".b", "archive.read_text")
