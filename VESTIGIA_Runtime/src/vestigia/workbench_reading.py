from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .utils import sha256_text, stable_json


PROVIDER_NAME = "reading.continue"
_BOOKMARK_KIND = "reading.bookmark"
_CURSOR_KIND = "reading.cursor"
_EFFECT_READ_ONLY = "read_only"
_MAX_CARDS = 50


def _reading_summary(locator: str, location: dict[str, Any], note: str) -> str:
    if note.strip():
        return note.strip()
    heading = str(location.get("heading") or "").strip()
    chunk = location.get("chunk")
    if heading and chunk is not None:
        return f"Saved at {heading!r}, chunk {chunk}."
    if heading:
        return f"Saved at {heading!r}."
    if chunk is not None:
        return f"Saved at chunk {chunk}."
    if location.get("cursor"):
        return "Saved resumable reading position."
    return f"Saved reading bookmark for {Path(locator).name}."


def _current_document(house: Any, locator: str) -> dict[str, Any] | None:
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT path, content_hash, indexed_at FROM house_documents WHERE path=?",
            (locator,),
        ).fetchone()
    return dict(row) if row else None


def _current_cursor(
    house: Any,
    cursor_id: str,
    *,
    expected_path: str,
) -> dict[str, Any] | None:
    clean = str(cursor_id or "").strip()
    if not clean:
        return None
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, resident_id, path, next_chunk, status, created_at, expires_at
            FROM house_cursors
            WHERE id=? AND resident_id=?
            """,
            (clean, house.resident_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if str(item.get("status") or "") != "active":
        return None
    if str(item.get("path") or "") != expected_path:
        return None
    try:
        expires_at = datetime.fromisoformat(str(item.get("expires_at") or ""))
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    return item


def _active_cursors(house: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, resident_id, path, next_chunk, status, created_at, expires_at
            FROM house_cursors
            WHERE resident_id=? AND status='active' AND expires_at>?
            ORDER BY rowid DESC LIMIT ?
            """,
            (
                house.resident_id,
                datetime.now(UTC).isoformat(),
                min(200, max(1, int(limit))),
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def _source_for_document(
    house: Any,
    locator: str,
    *,
    bookmark_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    current_document = _current_document(house, locator)
    if current_document is None:
        return None
    obj = house.legible.object_by_reference(locator)
    if not obj or str(obj.get("object_type") or "") != "document":
        return None
    evidence_state = str(obj.get("evidence_state") or "")
    if not evidence_state.startswith("verified"):
        return None
    provenance = obj.get("provenance") if isinstance(obj.get("provenance"), dict) else {}
    source = {
        "bookmark_id": bookmark_id,
        "object_id": str(obj.get("id") or ""),
        "locator": locator,
        "evidence_state": evidence_state,
        "content_hash": str(current_document.get("content_hash") or ""),
        "provenance": provenance,
    }
    return source, current_document


def _actions(*, cursor_mode: bool) -> list[dict[str, Any]]:
    return [
        {
            "action_id": "continue",
            "label": "Continue",
            "effect_class": _EFFECT_READ_ONLY,
            "description": (
                "Resume the active reading cursor."
                if cursor_mode
                else "Open the saved reading position."
            ),
        },
        {
            "action_id": "start_over",
            "label": "Start over",
            "effect_class": _EFFECT_READ_ONLY,
            "description": "Open the same document from its beginning.",
        },
        {
            "action_id": "provenance",
            "label": "Inspect provenance",
            "effect_class": _EFFECT_READ_ONLY,
            "description": "Inspect where the source came from and how it is represented here.",
        },
    ]


def _reading_card(house: Any, bookmark: dict[str, Any]) -> dict[str, Any] | None:
    if str(bookmark.get("object_type") or "") != "document":
        return None
    location = bookmark.get("location") or {}
    if not isinstance(location, dict):
        location = {}
    locator = str(bookmark.get("locator") or "")
    bookmark_id = str(bookmark.get("id") or "")
    resolved = _source_for_document(house, locator, bookmark_id=bookmark_id)
    if resolved is None:
        return None
    source, _current = resolved

    cursor_state = _current_cursor(
        house,
        str(location.get("cursor") or ""),
        expected_path=locator,
    )
    has_direct_position = bool(str(location.get("heading") or "").strip()) or (
        location.get("chunk") is not None
    )
    if location.get("cursor") and cursor_state is None and not has_direct_position:
        return None
    resume_mode = "cursor" if cursor_state is not None else "bookmark"

    state = {
        "provider": PROVIDER_NAME,
        "projection_kind": _BOOKMARK_KIND,
        "resident_id": house.resident_id,
        "bookmark_id": bookmark_id,
        "bookmark_updated_at": str(bookmark.get("updated_at") or ""),
        "object_id": source["object_id"],
        "locator": locator,
        "content_hash": source["content_hash"],
        "evidence_state": source["evidence_state"],
        "location": location,
        "resume_mode": resume_mode,
        "cursor": (
            {
                "id": str(cursor_state.get("id") or ""),
                "next_chunk": int(cursor_state.get("next_chunk") or 0),
                "status": str(cursor_state.get("status") or ""),
                "expires_at": str(cursor_state.get("expires_at") or ""),
            }
            if cursor_state is not None
            else None
        ),
    }
    fingerprint = sha256_text(stable_json(state))
    label = str(bookmark.get("label") or "").strip()
    title = label or Path(locator).name or "Saved reading"
    position = dict(location)
    position["resume_mode"] = resume_mode
    if cursor_state is not None:
        position["next_chunk"] = int(cursor_state.get("next_chunk") or 0)
        position["cursor_expires_at"] = str(cursor_state.get("expires_at") or "")

    return {
        "card_id": f"wb_{fingerprint[:24]}",
        "state_fingerprint": fingerprint,
        "provider": PROVIDER_NAME,
        "projection_kind": _BOOKMARK_KIND,
        "lane": "continue",
        "kind": "reading",
        "title": title,
        "summary": _reading_summary(locator, location, str(bookmark.get("note") or "")),
        "why_now": "saved reading position",
        "last_touched": str(bookmark.get("updated_at") or bookmark.get("created_at") or ""),
        "effect_class": _EFFECT_READ_ONLY,
        "source": source,
        "position": position,
        "actions": _actions(cursor_mode=resume_mode == "cursor"),
    }


def _cursor_card(
    house: Any,
    cursor: dict[str, Any],
    *,
    bookmark: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    locator = str(cursor.get("path") or "")
    clean_cursor = _current_cursor(house, str(cursor.get("id") or ""), expected_path=locator)
    if clean_cursor is None:
        return None
    bookmark_id = str(bookmark.get("id") or "") if bookmark else None
    resolved = _source_for_document(house, locator, bookmark_id=bookmark_id)
    if resolved is None:
        return None
    source, _current = resolved
    state = {
        "provider": PROVIDER_NAME,
        "projection_kind": _CURSOR_KIND,
        "resident_id": house.resident_id,
        "cursor_id": str(clean_cursor["id"]),
        "locator": locator,
        "object_id": source["object_id"],
        "content_hash": source["content_hash"],
        "next_chunk": int(clean_cursor.get("next_chunk") or 0),
        "status": str(clean_cursor.get("status") or ""),
        "expires_at": str(clean_cursor.get("expires_at") or ""),
        "bookmark_id": bookmark_id,
        "bookmark_updated_at": str(bookmark.get("updated_at") or "") if bookmark else "",
    }
    fingerprint = sha256_text(stable_json(state))
    label = str(bookmark.get("label") or "").strip() if bookmark else ""
    note = str(bookmark.get("note") or "").strip() if bookmark else ""
    next_chunk = int(clean_cursor.get("next_chunk") or 0)
    return {
        "card_id": f"wb_{fingerprint[:24]}",
        "state_fingerprint": fingerprint,
        "provider": PROVIDER_NAME,
        "projection_kind": _CURSOR_KIND,
        "lane": "continue",
        "kind": "reading",
        "title": label or Path(locator).name or "Unfinished reading",
        "summary": note or f"Continue from chunk {next_chunk}.",
        "why_now": "unfinished bounded read",
        "last_touched": str(clean_cursor.get("created_at") or ""),
        "effect_class": _EFFECT_READ_ONLY,
        "source": source,
        "position": {
            "resume_mode": "cursor",
            "cursor": str(clean_cursor["id"]),
            "next_chunk": next_chunk,
            "cursor_expires_at": str(clean_cursor.get("expires_at") or ""),
        },
        "actions": _actions(cursor_mode=True),
    }


def reading_cards(house: Any, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    house.refresh_index()
    requested = min(_MAX_CARDS, max(1, int(limit)))
    bookmarks = house.legible.list_bookmarks(limit=200)
    bookmark_by_cursor: dict[str, dict[str, Any]] = {}
    for bookmark in bookmarks:
        location = bookmark.get("location") or {}
        if isinstance(location, dict) and location.get("cursor"):
            bookmark_by_cursor[str(location["cursor"])] = bookmark

    cards: list[dict[str, Any]] = []
    used_bookmark_ids: set[str] = set()
    for cursor in _active_cursors(house):
        bookmark = bookmark_by_cursor.get(str(cursor.get("id") or ""))
        card = _cursor_card(house, cursor, bookmark=bookmark)
        if card is None:
            continue
        cards.append(card)
        if bookmark:
            used_bookmark_ids.add(str(bookmark.get("id") or ""))
        if len(cards) >= requested:
            return cards

    for bookmark in bookmarks:
        if str(bookmark.get("id") or "") in used_bookmark_ids:
            continue
        card = _reading_card(house, bookmark)
        if card is None:
            continue
        cards.append(card)
        if len(cards) >= requested:
            break
    return cards


def _dispatch_semantic_action(
    house: Any,
    card: dict[str, Any],
    action_id: str,
    *,
    max_tokens: int,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    source = card["source"]
    position = card["position"]
    inner_context = dict(context)
    inner_context["source_envelope"] = "WORKBENCH"
    turn_id = str(context.get("turn_id") or "") or None

    if action_id == "continue":
        if position.get("resume_mode") == "cursor":
            underlying_action = "continue"
            result = house.dispatch(
                {
                    "action": underlying_action,
                    "cursor": position["cursor"],
                    "max_tokens": max_tokens,
                },
                turn_id=turn_id,
                context=inner_context,
            )
        else:
            underlying_action = "bookmark.open"
            result = house.dispatch(
                {
                    "action": underlying_action,
                    "bookmark_id": source["bookmark_id"],
                    "max_tokens": max_tokens,
                },
                turn_id=turn_id,
                context=inner_context,
            )
        return underlying_action, result
    if action_id == "start_over":
        underlying_action = "read"
        result = house.dispatch(
            {
                "action": underlying_action,
                "path": source["locator"],
                "chunk": 0,
                "max_tokens": max_tokens,
            },
            turn_id=turn_id,
            context=inner_context,
        )
        return underlying_action, result
    if action_id == "provenance":
        underlying_action = "object.provenance"
        result = house.dispatch(
            {
                "action": underlying_action,
                "reference": source["object_id"],
            },
            turn_id=turn_id,
            context=inner_context,
        )
        return underlying_action, result
    raise ValueError(f"unsupported semantic reading action: {action_id}")


def _refreshed_card_for_source(house: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    cards = reading_cards(house, limit=_MAX_CARDS)
    bookmark_id = str(source.get("bookmark_id") or "")
    if bookmark_id:
        for candidate in cards:
            if str(candidate.get("source", {}).get("bookmark_id") or "") == bookmark_id:
                return candidate
    locator = str(source.get("locator") or "")
    for candidate in cards:
        if (
            str(candidate.get("source", {}).get("locator") or "") == locator
            and candidate.get("position", {}).get("resume_mode") == "cursor"
        ):
            return candidate
    return None


def provider(house: Any, request: dict[str, Any]) -> dict[str, Any]:
    mode = str(request.get("mode") or "view").strip().lower()
    if mode == "view":
        lane = str(request.get("lane") or "all").strip().lower()
        limit = int(request.get("limit") or 0)
        cards = reading_cards(house, limit=limit) if lane in {"all", "continue"} else []
        return {
            "implemented_lanes": ["continue"],
            "cards": cards,
        }
    if mode != "act":
        raise ValueError(f"unsupported Workbench provider mode: {mode}")

    card = request.get("card")
    if not isinstance(card, dict) or card.get("provider") != PROVIDER_NAME:
        raise ValueError("reading provider received a foreign Workbench card")
    action_id = str(request.get("action_id") or "").strip().lower()
    available = {str(item.get("action_id") or "") for item in card.get("actions", [])}
    if action_id not in available:
        raise ValueError(f"action {action_id!r} is not offered by this reading card")
    max_tokens = min(4000, max(100, int(request.get("max_tokens") or 3000)))
    context = request.get("context") if isinstance(request.get("context"), dict) else {}

    underlying_action, result = _dispatch_semantic_action(
        house,
        card,
        action_id,
        max_tokens=max_tokens,
        context=context,
    )
    outcome = result.get("opened") if underlying_action == "bookmark.open" else result
    refreshed = _refreshed_card_for_source(house, card["source"])
    return {
        "underlying_action": underlying_action,
        "underlying_receipt_id": result.get("receipt_id"),
        "outcome": outcome,
        "underlying_result": result,
        "refreshed_card": refreshed,
        "outward_effect": "none",
        "memory_promotion": False,
        "identity_effect": False,
    }
