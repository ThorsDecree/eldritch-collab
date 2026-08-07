from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .utils import new_id, sha256_text, stable_json, utc_now_iso


_DRAWER_MODES = (
    "browse",
    "search",
    "continue",
    "bookmark",
    "open_bookmark",
    "list_bookmarks",
    "remove_bookmark",
    "get",
    "update",
    "summarize",
    "pocket",
    "timeline",
)

_DRAWER_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_drawer_sessions (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    query_text TEXT NOT NULL DEFAULT '',
    query_hash TEXT NOT NULL,
    pocket TEXT NOT NULL DEFAULT '',
    include_private INTEGER NOT NULL DEFAULT 1,
    sort_version TEXT NOT NULL,
    filter_hash TEXT NOT NULL,
    snapshot_fingerprint TEXT NOT NULL,
    page_size INTEGER NOT NULL,
    total_items INTEGER NOT NULL,
    snapshot_truncated INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_drawer_sessions_resident
ON image_drawer_sessions(resident_id, status, updated_at);

CREATE TABLE IF NOT EXISTS image_drawer_session_items (
    session_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    image_id TEXT NOT NULL,
    sort_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, ordinal),
    UNIQUE(session_id, image_id),
    FOREIGN KEY (session_id) REFERENCES image_drawer_sessions(id),
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE INDEX IF NOT EXISTS idx_image_drawer_items_image
ON image_drawer_session_items(image_id, session_id);

CREATE TABLE IF NOT EXISTS image_drawer_cursors (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_opened_at TEXT NOT NULL,
    UNIQUE(resident_id, session_id, ordinal, page_size),
    FOREIGN KEY (session_id) REFERENCES image_drawer_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_image_drawer_cursors_resident
ON image_drawer_cursors(resident_id, status, expires_at);

CREATE TABLE IF NOT EXISTS image_drawer_bookmarks (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES image_drawer_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_image_drawer_bookmarks_resident
ON image_drawer_bookmarks(resident_id, status, updated_at);
"""


def _aware(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("drawer timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def ensure_schema(images: Any) -> None:
    with images.db.connect() as connection:
        connection.executescript(_DRAWER_SCHEMA)


def _page_size(images: Any, value: Any) -> int:
    operator_max = max(
        1,
        min(int(images.config.get("images.drawer_max_page_size", 100)), 100),
    )
    return max(1, min(int(value or 20), operator_max))


def _snapshot_limit(images: Any) -> int:
    return max(
        100,
        min(int(images.config.get("images.drawer_snapshot_max_items", 50000)), 100000),
    )


def _cursor_expiry(images: Any) -> str:
    seconds = max(
        300,
        min(int(images.config.get("images.drawer_cursor_seconds", 86400)), 604800),
    )
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _normalize_pocket(images: Any, pocket: str) -> str:
    clean = str(pocket or "").strip()
    if not clean:
        return ""
    normalize = getattr(images, "_normalize_pocket", None)
    return str(normalize(clean) if normalize else clean.casefold())


def _fingerprint(image_ids: list[str]) -> str:
    digest = hashlib.sha256()
    for image_id in image_ids:
        digest.update(image_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _snapshot_browse(
    images: Any,
    *,
    include_private: bool,
    pocket: str,
    maximum: int,
) -> tuple[list[tuple[str, str]], bool, str]:
    parameters: list[Any] = [images.resident_id]
    privacy_sql = "" if include_private else " AND a.privacy!='private'"
    pocket_sql = ""
    if pocket:
        pocket_sql = (
            " AND EXISTS (SELECT 1 FROM image_pockets p "
            "WHERE p.resident_id=a.resident_id AND p.image_id=a.id AND p.pocket=?)"
        )
        parameters.append(pocket)
    parameters.append(maximum + 1)
    with images.db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT a.id, a.created_at
            FROM image_assets a
            WHERE a.resident_id=? {privacy_sql} {pocket_sql}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
    truncated = len(rows) > maximum
    rows = rows[:maximum]
    return (
        [(str(row["id"]), f"{row['created_at']}|{row['id']}") for row in rows],
        truncated,
        "created_at_desc_id_desc_v1",
    )


def _snapshot_search(
    images: Any,
    *,
    query: str,
    include_private: bool,
    pocket: str,
    maximum: int,
) -> tuple[list[tuple[str, str]], bool, str]:
    clean = " ".join(str(query).split()).strip()
    words = list(dict.fromkeys(re.findall(r"[\w#-]{2,}", clean.casefold())))[:32]
    fts = " OR ".join(f'"{word}"' for word in words)
    privacy_sql = "" if include_private else " AND a.privacy!='private'"
    pocket_sql = ""
    pocket_parameters: list[Any] = []
    if pocket:
        pocket_sql = (
            " AND EXISTS (SELECT 1 FROM image_pockets p "
            "WHERE p.resident_id=a.resident_id AND p.image_id=a.id AND p.pocket=?)"
        )
        pocket_parameters.append(pocket)
    with images.db.connect() as connection:
        if fts:
            rows = connection.execute(
                f"""
                SELECT c.image_id, bm25(image_cards_fts) AS rank,
                       c.updated_at
                FROM image_cards_fts f
                JOIN image_cards c ON c.image_id=f.image_id
                JOIN image_assets a ON a.id=c.image_id
                WHERE c.resident_id=? AND image_cards_fts MATCH ?
                {privacy_sql} {pocket_sql}
                ORDER BY rank ASC, c.updated_at DESC, c.image_id ASC
                LIMIT ?
                """,
                (
                    images.resident_id,
                    fts,
                    *pocket_parameters,
                    maximum + 1,
                ),
            ).fetchall()
            items = [
                (
                    str(row["image_id"]),
                    f"{float(row['rank']):.12f}|{row['updated_at']}|{row['image_id']}",
                )
                for row in rows
            ]
            sort_version = "fts_rank_asc_updated_desc_id_asc_v1"
        else:
            rows = connection.execute(
                f"""
                SELECT c.image_id, c.updated_at
                FROM image_cards c
                JOIN image_assets a ON a.id=c.image_id
                WHERE c.resident_id=? {privacy_sql} {pocket_sql}
                ORDER BY c.updated_at DESC, c.image_id ASC
                LIMIT ?
                """,
                (images.resident_id, *pocket_parameters, maximum + 1),
            ).fetchall()
            items = [
                (
                    str(row["image_id"]),
                    f"{row['updated_at']}|{row['image_id']}",
                )
                for row in rows
            ]
            sort_version = "card_updated_desc_id_asc_v1"
    truncated = len(items) > maximum
    return items[:maximum], truncated, sort_version


def _new_session(
    images: Any,
    *,
    mode: str,
    query: str,
    include_private: bool,
    pocket: str,
    page_size: int,
) -> dict[str, Any]:
    ensure_schema(images)
    maximum = _snapshot_limit(images)
    if mode == "browse":
        items, truncated, sort_version = _snapshot_browse(
            images,
            include_private=include_private,
            pocket=pocket,
            maximum=maximum,
        )
    else:
        items, truncated, sort_version = _snapshot_search(
            images,
            query=query,
            include_private=include_private,
            pocket=pocket,
            maximum=maximum,
        )
    image_ids = [image_id for image_id, _sort_key in items]
    now = utc_now_iso()
    expires_at = _cursor_expiry(images)
    session_id = new_id("drawer_session")
    query_hash = sha256_text(query) if query else sha256_text("")
    filter_payload = {
        "mode": mode,
        "query_hash": query_hash,
        "include_private": bool(include_private),
        "pocket": pocket,
        "sort_version": sort_version,
    }
    with images.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO image_drawer_sessions
            (id, resident_id, mode, query_text, query_hash, pocket,
             include_private, sort_version, filter_hash, snapshot_fingerprint,
             page_size, total_items, snapshot_truncated, status, created_at,
             expires_at, last_opened_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                session_id,
                images.resident_id,
                mode,
                query,
                query_hash,
                pocket,
                int(include_private),
                sort_version,
                sha256_text(stable_json(filter_payload)),
                _fingerprint(image_ids),
                page_size,
                len(items),
                int(truncated),
                now,
                expires_at,
                now,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO image_drawer_session_items
            (session_id, ordinal, image_id, sort_key)
            VALUES (?, ?, ?, ?)
            """,
            [
                (session_id, ordinal, image_id, sort_key)
                for ordinal, (image_id, sort_key) in enumerate(items)
            ],
        )
    return _get_session(images, session_id, allow_expired=False)


def _session_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["include_private"] = bool(result["include_private"])
    result["snapshot_truncated"] = bool(result["snapshot_truncated"])
    return result


def _get_session(
    images: Any,
    session_id: str,
    *,
    allow_expired: bool,
) -> dict[str, Any]:
    ensure_schema(images)
    with images.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM image_drawer_sessions
            WHERE id=? AND resident_id=?
            """,
            (session_id, images.resident_id),
        ).fetchone()
    if not row:
        from .house_tools import HouseCursorError

        raise HouseCursorError(
            "unknown image drawer session",
            code="image_drawer_session_unknown",
            suggested_retry={"action": "image.drawer", "mode": "browse"},
        )
    session = _session_row(row)
    if session["status"] == "closed":
        from .house_tools import HouseCursorError

        raise HouseCursorError(
            "image drawer session is closed",
            code="image_drawer_session_closed",
            suggested_retry=_retry_payload(session),
        )
    expiry = str(session.get("expires_at") or "").strip()
    expired = bool(expiry and _aware(expiry) <= datetime.now(UTC))
    if expired and session["status"] != "bookmarked" and not allow_expired:
        with images.db.connect() as connection:
            connection.execute(
                "UPDATE image_drawer_sessions SET status='expired', updated_at=? WHERE id=?",
                (utc_now_iso(), session_id),
            )
        from .house_tools import HouseCursorExpiredError

        raise HouseCursorExpiredError(
            "image drawer cursor expired",
            code="image_drawer_cursor_expired",
            suggested_retry=_retry_payload(session),
        )
    return session


def _retry_payload(session: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "image.drawer",
        "mode": str(session["mode"]),
        "limit": int(session["page_size"]),
        "include_private": bool(session["include_private"]),
    }
    if session.get("query_text"):
        payload["query"] = str(session["query_text"])
    if session.get("pocket"):
        payload["pocket"] = str(session["pocket"])
    return payload


def _cursor_for(
    images: Any,
    session: dict[str, Any],
    *,
    ordinal: int,
    page_size: int,
) -> str:
    ensure_schema(images)
    bounded = max(0, min(int(ordinal), max(0, int(session["total_items"]))))
    now = utc_now_iso()
    expires_at = (
        None
        if session["status"] == "bookmarked"
        else str(session.get("expires_at") or _cursor_expiry(images))
    )
    with images.db.connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM image_drawer_cursors
            WHERE resident_id=? AND session_id=? AND ordinal=? AND page_size=?
            """,
            (images.resident_id, session["id"], bounded, page_size),
        ).fetchone()
        if row:
            cursor_id = str(row["id"])
            connection.execute(
                """
                UPDATE image_drawer_cursors
                SET status='active', expires_at=?, last_opened_at=?
                WHERE id=?
                """,
                (expires_at, now, cursor_id),
            )
            return cursor_id
        cursor_id = new_id("drawer_cursor")
        connection.execute(
            """
            INSERT INTO image_drawer_cursors
            (id, resident_id, session_id, ordinal, page_size, status,
             created_at, expires_at, last_opened_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                cursor_id,
                images.resident_id,
                session["id"],
                bounded,
                page_size,
                now,
                expires_at,
                now,
            ),
        )
    return cursor_id


def _cursor(images: Any, cursor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ensure_schema(images)
    with images.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM image_drawer_cursors
            WHERE id=? AND resident_id=?
            """,
            (cursor_id, images.resident_id),
        ).fetchone()
    if not row:
        from .house_tools import HouseCursorError

        raise HouseCursorError(
            "unknown image drawer cursor",
            code="image_drawer_cursor_unknown",
            suggested_retry={"action": "image.drawer", "mode": "browse"},
        )
    cursor = dict(row)
    session = _get_session(images, str(cursor["session_id"]), allow_expired=False)
    cursor_expiry = str(cursor.get("expires_at") or "").strip()
    if cursor_expiry and _aware(cursor_expiry) <= datetime.now(UTC) and session["status"] != "bookmarked":
        from .house_tools import HouseCursorExpiredError

        raise HouseCursorExpiredError(
            "image drawer cursor expired",
            code="image_drawer_cursor_expired",
            suggested_retry=_retry_payload(session),
        )
    with images.db.connect() as connection:
        connection.execute(
            "UPDATE image_drawer_cursors SET last_opened_at=? WHERE id=?",
            (utc_now_iso(), cursor_id),
        )
    return session, cursor


def _page(
    images: Any,
    session: dict[str, Any],
    *,
    ordinal: int,
    page_size: int,
    current_cursor: str | None = None,
) -> dict[str, Any]:
    total = int(session["total_items"])
    start = max(0, min(int(ordinal), total))
    size = _page_size(images, page_size)
    with images.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT ordinal, image_id FROM image_drawer_session_items
            WHERE session_id=? AND ordinal>=? AND ordinal<?
            ORDER BY ordinal
            """,
            (session["id"], start, start + size),
        ).fetchall()
        connection.execute(
            """
            UPDATE image_drawer_sessions
            SET last_opened_at=?, updated_at=? WHERE id=?
            """,
            (utc_now_iso(), utc_now_iso(), session["id"]),
        )
    cards: list[dict[str, Any]] = []
    missing_image_ids: list[str] = []
    for row in rows:
        image_id = str(row["image_id"])
        try:
            cards.append(images.card(image_id))
        except (KeyError, FileNotFoundError):
            missing_image_ids.append(image_id)
    end = min(total, start + len(rows))
    current = current_cursor or _cursor_for(
        images, session, ordinal=start, page_size=size
    )
    previous_cursor = (
        _cursor_for(
            images,
            session,
            ordinal=max(0, start - size),
            page_size=size,
        )
        if start > 0
        else None
    )
    next_cursor = (
        _cursor_for(images, session, ordinal=end, page_size=size)
        if end < total
        else None
    )
    page_number = (start // size) + 1 if size else 1
    page_count = (total + size - 1) // size if total else 0
    return {
        "mode": str(session["mode"]),
        "query": str(session["query_text"]) if session["mode"] == "search" else None,
        "cards": cards,
        "pockets": images.pockets() if session["mode"] == "browse" else None,
        "pagination": {
            "session_id": str(session["id"]),
            "current_cursor": current,
            "previous_cursor": previous_cursor,
            "next_cursor": next_cursor,
            "page_size": size,
            "page_number": page_number,
            "page_count": page_count,
            "start_ordinal": start,
            "end_ordinal_exclusive": end,
            "total_items": total,
            "snapshot_truncated": bool(session["snapshot_truncated"]),
            "total_is_lower_bound": bool(session["snapshot_truncated"]),
            "snapshot_fingerprint": str(session["snapshot_fingerprint"]),
            "filter_hash": str(session["filter_hash"]),
            "sort_version": str(session["sort_version"]),
            "snapshot_created_at": str(session["created_at"]),
            "expires_at": session.get("expires_at"),
            "bookmarkable": True,
            "stable_snapshot": True,
        },
        "missing_image_ids": missing_image_ids,
        "ambiguous": len(cards) > 1,
        "next_action": (
            "continue_with_next_cursor_or_bookmark_position"
            if next_cursor
            else "bookmark_position_or_choose_image_id"
        ),
        "provider_call": False,
        "resident_model_call": False,
        "memory_adoption": False,
        "outward_action": False,
        "authority_changed": False,
    }


def start_page(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "browse").strip().lower()
    query = " ".join(str(payload.get("query") or "").split()).strip()
    if mode == "search" and not query:
        raise ValueError("image.drawer search requires a query")
    page_size = _page_size(images, payload.get("limit", 20 if mode == "browse" else 8))
    pocket = _normalize_pocket(images, str(payload.get("pocket") or ""))
    session = _new_session(
        images,
        mode=mode,
        query=query,
        include_private=bool(payload.get("include_private", True)),
        pocket=pocket,
        page_size=page_size,
    )
    return _page(images, session, ordinal=0, page_size=page_size)


def continue_page(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    cursor_id = str(payload.get("cursor") or "").strip()
    if not cursor_id:
        raise ValueError("image.drawer continue requires cursor")
    session, cursor = _cursor(images, cursor_id)
    return _page(
        images,
        session,
        ordinal=int(cursor["ordinal"]),
        page_size=int(cursor["page_size"]),
        current_cursor=cursor_id,
    )


def bookmark_position(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    cursor_id = str(payload.get("cursor") or "").strip()
    if not cursor_id:
        raise ValueError("image.drawer bookmark requires cursor")
    session, cursor = _cursor(images, cursor_id)
    label = " ".join(str(payload.get("label") or "").split()).strip()[:240]
    note = str(payload.get("note") or "")[:2000]
    now = utc_now_iso()
    bookmark_id = new_id("drawer_bookmark")
    with images.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO image_drawer_bookmarks
            (id, resident_id, session_id, ordinal, page_size, label, note,
             status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                bookmark_id,
                images.resident_id,
                session["id"],
                int(cursor["ordinal"]),
                int(cursor["page_size"]),
                label,
                note,
                now,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE image_drawer_sessions
            SET status='bookmarked', expires_at=NULL, updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (now, session["id"], images.resident_id),
        )
        connection.execute(
            "UPDATE image_drawer_cursors SET expires_at=NULL WHERE session_id=?",
            (session["id"],),
        )
    return {
        "mode": "bookmark",
        "bookmark": {
            "id": bookmark_id,
            "session_id": str(session["id"]),
            "cursor": cursor_id,
            "ordinal": int(cursor["ordinal"]),
            "page_size": int(cursor["page_size"]),
            "label": label,
            "note": note,
            "query_hash": str(session["query_hash"]),
            "filter_hash": str(session["filter_hash"]),
            "snapshot_fingerprint": str(session["snapshot_fingerprint"]),
            "created_at": now,
        },
        "stable_snapshot_preserved": True,
        "provider_call": False,
        "resident_model_call": False,
        "memory_adoption": False,
        "outward_action": False,
        "authority_changed": False,
    }


def _bookmark_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["page_size"] = int(result["page_size"])
    result["ordinal"] = int(result["ordinal"])
    return result


def list_bookmarks(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(images)
    limit = max(1, min(int(payload.get("limit") or 100), 200))
    with images.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT b.*, s.mode, s.query_hash, s.pocket, s.include_private,
                   s.sort_version, s.filter_hash, s.snapshot_fingerprint,
                   s.total_items, s.snapshot_truncated, s.created_at AS snapshot_created_at
            FROM image_drawer_bookmarks b
            JOIN image_drawer_sessions s ON s.id=b.session_id
            WHERE b.resident_id=? AND b.status='active'
            ORDER BY b.updated_at DESC LIMIT ?
            """,
            (images.resident_id, limit),
        ).fetchall()
    bookmarks = [_bookmark_row(row) for row in rows]
    for item in bookmarks:
        item["include_private"] = bool(item["include_private"])
        item["snapshot_truncated"] = bool(item["snapshot_truncated"])
    return {
        "mode": "list_bookmarks",
        "bookmarks": bookmarks,
        "count": len(bookmarks),
        "outward_action": False,
        "authority_changed": False,
    }


def open_bookmark(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(images)
    bookmark_id = str(payload.get("bookmark_id") or "").strip()
    if not bookmark_id:
        raise ValueError("image.drawer open_bookmark requires bookmark_id")
    with images.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM image_drawer_bookmarks
            WHERE id=? AND resident_id=? AND status='active'
            """,
            (bookmark_id, images.resident_id),
        ).fetchone()
    if not row:
        from .house_tools import HouseCursorError

        raise HouseCursorError(
            "unknown image drawer bookmark",
            code="image_drawer_bookmark_unknown",
            suggested_retry={
                "action": "image.drawer",
                "mode": "list_bookmarks",
            },
        )
    bookmark = _bookmark_row(row)
    session = _get_session(images, str(bookmark["session_id"]), allow_expired=True)
    cursor_id = _cursor_for(
        images,
        session,
        ordinal=int(bookmark["ordinal"]),
        page_size=int(bookmark["page_size"]),
    )
    page = _page(
        images,
        session,
        ordinal=int(bookmark["ordinal"]),
        page_size=int(bookmark["page_size"]),
        current_cursor=cursor_id,
    )
    page["mode"] = "open_bookmark"
    page["bookmark"] = {
        "id": bookmark_id,
        "label": str(bookmark["label"]),
        "note": str(bookmark["note"]),
        "created_at": str(bookmark["created_at"]),
    }
    return page


def remove_bookmark(images: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(images)
    bookmark_id = str(payload.get("bookmark_id") or "").strip()
    if not bookmark_id:
        raise ValueError("image.drawer remove_bookmark requires bookmark_id")
    now = utc_now_iso()
    with images.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM image_drawer_bookmarks
            WHERE id=? AND resident_id=? AND status='active'
            """,
            (bookmark_id, images.resident_id),
        ).fetchone()
        if not row:
            raise KeyError("unknown image drawer bookmark")
        bookmark = _bookmark_row(row)
        connection.execute(
            """
            UPDATE image_drawer_bookmarks
            SET status='removed', updated_at=?
            WHERE id=? AND resident_id=?
            """,
            (now, bookmark_id, images.resident_id),
        )
        remaining = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count FROM image_drawer_bookmarks
                WHERE resident_id=? AND session_id=? AND status='active'
                """,
                (images.resident_id, bookmark["session_id"]),
            ).fetchone()["count"]
        )
        if remaining == 0:
            expiry = _cursor_expiry(images)
            connection.execute(
                """
                UPDATE image_drawer_sessions
                SET status='active', expires_at=?, updated_at=?
                WHERE id=? AND resident_id=?
                """,
                (expiry, now, bookmark["session_id"], images.resident_id),
            )
            connection.execute(
                """
                UPDATE image_drawer_cursors SET expires_at=?
                WHERE session_id=? AND resident_id=?
                """,
                (expiry, bookmark["session_id"], images.resident_id),
            )
    return {
        "mode": "remove_bookmark",
        "bookmark_id": bookmark_id,
        "status": "removed",
        "stable_snapshot_retained_until_cursor_expiry": remaining == 0,
        "remaining_bookmarks_for_snapshot": remaining,
        "outward_action": False,
        "authority_changed": False,
    }


def _contract_contribution(
    fields: dict[str, Any],
    required: tuple[str, ...],
    examples: tuple[dict[str, Any], ...] | None,
    group: str,
    related: tuple[str, ...],
) -> tuple[
    dict[str, Any],
    tuple[str, ...],
    tuple[dict[str, Any], ...] | None,
    str,
    tuple[str, ...],
]:
    from . import capability_contracts as contracts

    updated = dict(fields)
    updated["mode"] = contracts.S(enum=list(_DRAWER_MODES))
    updated.update(
        {
            "cursor": contracts.ID,
            "bookmark_id": contracts.ID,
            "label": contracts.S(maxLength=240),
            "note": contracts.S(maxLength=2000),
        }
    )
    existing = tuple(examples or ())
    continuation_examples = (
        {
            "action": "image.drawer",
            "mode": "continue",
            "cursor": "drawer_cursor_...",
            "after": "continue",
        },
        {
            "action": "image.drawer",
            "mode": "bookmark",
            "cursor": "drawer_cursor_...",
            "label": "Mall reactions, page 4",
            "after": "continue",
        },
        {
            "action": "image.drawer",
            "mode": "open_bookmark",
            "bookmark_id": "drawer_bookmark_...",
            "after": "continue",
        },
    )
    return updated, required, existing + continuation_examples, group, related


def _drawer_mode_handler(
    house: Any, payload: dict[str, Any], _context: dict[str, Any]
) -> dict[str, Any]:
    images = house._require_images()
    mode = str(payload.get("mode") or "browse").strip().lower()
    if mode in {"browse", "search"}:
        return start_page(images, payload)
    if mode == "continue":
        return continue_page(images, payload)
    if mode == "bookmark":
        return bookmark_position(images, payload)
    if mode == "open_bookmark":
        return open_bookmark(images, payload)
    if mode == "list_bookmarks":
        return list_bookmarks(images, payload)
    if mode == "remove_bookmark":
        return remove_bookmark(images, payload)
    raise ValueError(f"unsupported registered image drawer mode: {mode}")


def _refresh_spec(house: Any) -> None:
    try:
        house.registry.replace_spec(
            "image.drawer",
            description=(
                "Browse, search, resume, bookmark, name, annotate, summarize, "
                "pocket, or inspect resident-owned image memory cards through "
                "stable private collection snapshots."
            ),
            next_step=(
                "Use pagination.next_cursor with mode:continue, or preserve the "
                "current cursor with mode:bookmark."
            ),
        )
    except ValueError:
        return


def register_composition() -> None:
    from .composition import (
        register_capability_installer,
        register_contract_contribution,
        register_drawer_modes,
    )

    register_drawer_modes(
        "image.drawer.continuation", _DRAWER_MODES, _drawer_mode_handler, order=40
    )
    register_contract_contribution(
        "image.drawer.continuation",
        "image.drawer",
        _contract_contribution,
        order=40,
    )
    register_capability_installer(
        "image.drawer.continuation", _refresh_spec, order=40
    )
