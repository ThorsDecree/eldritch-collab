from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .utils import sha256_text, stable_json


_LANES = ("all", "continue", "review", "tend", "make", "observe")
_EFFECT_READ_ONLY = "read_only"
_BOOKMARK_PROVIDER = "reading.bookmark"
_CURSOR_PROVIDER = "reading.cursor"
_MAX_CARDS = 50


def _bounded_limit(value: Any, *, default: int = 12) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(_MAX_CARDS, max(1, parsed))


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
        # Cursor-only bookmarks that can no longer resume should not masquerade as a
        # working Continue button. A later Review/Observe provider can surface them as
        # stale housekeeping instead.
        return None
    resume_mode = "cursor" if cursor_state is not None else "bookmark"

    state = {
        "provider": _BOOKMARK_PROVIDER,
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
        "provider": _BOOKMARK_PROVIDER,
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
        "provider": _CURSOR_PROVIDER,
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
        "provider": _CURSOR_PROVIDER,
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
    # Index refresh is the same evidence-refresh path used by list/search/read. It lets
    # changed or missing files alter the projection rather than allowing an old card ID
    # to stand in for current state.
    house.refresh_index()
    requested = _bounded_limit(limit)
    bookmarks = house.legible.list_bookmarks(limit=200)
    bookmark_by_cursor: dict[str, dict[str, Any]] = {}
    for bookmark in bookmarks:
        location = bookmark.get("location") or {}
        if isinstance(location, dict) and location.get("cursor"):
            bookmark_by_cursor[str(location["cursor"])] = bookmark

    cards: list[dict[str, Any]] = []
    used_bookmark_ids: set[str] = set()

    # Active cursors are the most direct evidence of "I was in the middle of reading
    # this." Surface them first. If a cursor also has an explicit bookmark, borrow the
    # resident's label/note without duplicating the same continuation state.
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

    # Explicit bookmarks remain valuable even when no active cursor exists. Cursor-only
    # bookmarks whose cursor has expired are filtered by _reading_card rather than being
    # reinterpreted as a position they never actually recorded.
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


def workbench_view(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
    lane = str(payload.get("lane") or "all").strip().lower()
    if lane not in _LANES:
        raise ValueError(f"unknown workbench lane: {lane}")
    limit = _bounded_limit(payload.get("limit"))
    cards = reading_cards(house, limit=limit) if lane in {"all", "continue"} else []
    return {
        "schema_version": "vestigia.workbench.v0.1",
        "lane": lane,
        "cards": cards,
        "card_count": len(cards),
        "implemented_lanes": ["continue"],
        "planned_lanes": ["review", "tend", "make", "observe"],
        "authority": "projection_only",
        "invariant": (
            "Workbench cards project current house state. Card IDs and action IDs do not grant authority; "
            "actions are re-resolved and dispatched through ordinary Runtime capabilities."
        ),
    }


def _current_card(house: Any, card_id: str) -> dict[str, Any]:
    clean = str(card_id or "").strip()
    if not clean:
        raise ValueError("card_id is required")
    # Search the bounded active state rather than accepting hidden locator or capability
    # parameters from the caller. If the underlying state changed, the fingerprint
    # changes and the old card deliberately stops resolving.
    for card in reading_cards(house, limit=_MAX_CARDS):
        if card["card_id"] == clean:
            return card
    raise KeyError(
        "workbench card is stale or unavailable; refresh workbench.view before acting"
    )


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
    raise ValueError(f"unsupported semantic workbench action: {action_id}")


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


def workbench_act(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    card = _current_card(house, str(payload.get("card_id") or ""))
    action_id = str(payload.get("action_id") or "").strip().lower()
    available = {str(item["action_id"]) for item in card["actions"]}
    if action_id not in available:
        raise ValueError(f"action {action_id!r} is not offered by this workbench card")
    try:
        max_tokens = int(payload.get("max_tokens", 3000))
    except (TypeError, ValueError):
        max_tokens = 3000
    max_tokens = min(4000, max(100, max_tokens))

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
        "schema_version": "vestigia.workbench.v0.1",
        "card_id": card["card_id"],
        "state_fingerprint": card["state_fingerprint"],
        "action_id": action_id,
        "effect_class": card["effect_class"],
        "underlying_action": underlying_action,
        "underlying_receipt_id": result.get("receipt_id"),
        "outcome": outcome,
        "underlying_result": result,
        "refreshed_card": refreshed,
        "outward_effect": "none",
        "memory_promotion": False,
        "identity_effect": False,
        "invariant": "The semantic action used the ordinary Runtime capability dispatcher.",
    }


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="workbench.view",
            description=(
                "Show a bounded resident-facing Workbench of current useful affordances. "
                "The initial slice projects unfinished reads and durable bookmarks into Continue cards."
            ),
            effects=("database:read", "filesystem:read_indexed_house"),
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="workbench",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "workbench.view"},
                    "lane": {"type": "string", "enum": list(_LANES)},
                    "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_CARDS},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {"action": "workbench.view", "lane": "continue", "limit": 12, "after": "continue"},
            ),
            next_step="Choose an offered card action with workbench.act; no raw cursor or bookmark syntax is required.",
        ),
        lambda payload, _context: workbench_view(house, payload),
    )
    house.registry.register(
        CapabilitySpec(
            name="workbench.act",
            description=(
                "Take one semantic action offered by a current Workbench card. The card is "
                "re-resolved first, then the underlying operation goes through the ordinary capability dispatcher."
            ),
            effects=("database:read", "filesystem:read_indexed_house"),
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="workbench",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "workbench.act"},
                    "card_id": {"type": "string", "minLength": 4, "maxLength": 80},
                    "action_id": {
                        "type": "string",
                        "enum": ["continue", "start_over", "provenance"],
                    },
                    "max_tokens": {"type": "integer", "minimum": 100, "maximum": 4000},
                    "after": after,
                },
                required=("action", "card_id", "action_id"),
            ),
            example_envelopes=(
                {
                    "action": "workbench.act",
                    "card_id": "wb_...",
                    "action_id": "continue",
                    "after": "continue",
                },
            ),
            related_actions=("workbench.view", "bookmark.open", "continue", "read", "object.provenance"),
            next_step="Use the semantic outcome and refreshed_card; refresh workbench.view whenever you want the current desk state.",
        ),
        lambda payload, context: workbench_act(house, payload, context),
    )


def register_composition() -> None:
    from .composition import register_capability_installer

    register_capability_installer("workbench.core", _register, order=80)
