from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any

from .utils import stable_json, utc_now_iso


_NAVIGATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS house_cursor_provenance (
    cursor_id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    object_id TEXT,
    path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    origin_action TEXT NOT NULL,
    source_cursor_id TEXT,
    bookmark_id TEXT,
    requested_heading TEXT,
    requested_chunk INTEGER,
    first_returned_chunk INTEGER,
    last_returned_chunk INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_house_cursor_provenance_resident
ON house_cursor_provenance(resident_id, created_at);
"""

_CHUNK_LABEL = re.compile(r" · chunk (\d+)\]")


class NavigationStateError(ValueError):
    """Resident-visible failure for ambiguous or unverifiable navigation state."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        suggested_retry: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.house_error_code = code
        self.house_suggested_retry = suggested_retry


def _ensure_schema(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(_NAVIGATION_SCHEMA)


def _document_for_path(house: Any, path: str) -> dict[str, Any] | None:
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT path, content_hash, indexed_at FROM house_documents WHERE path=?",
            (path,),
        ).fetchone()
    return dict(row) if row else None


def _cursor_row(house: Any, cursor_id: str) -> dict[str, Any] | None:
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, resident_id, path, next_chunk, status, created_at, expires_at
            FROM house_cursors
            WHERE id=? AND resident_id=?
            """,
            (cursor_id, house.resident_id),
        ).fetchone()
    return dict(row) if row else None


def _cursor_provenance(house: Any, cursor_id: str) -> dict[str, Any] | None:
    _ensure_schema(house)
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM house_cursor_provenance
            WHERE cursor_id=? AND resident_id=?
            """,
            (cursor_id, house.resident_id),
        ).fetchone()
    return dict(row) if row else None


def _chunk_heading(house: Any, path: str, chunk: int) -> str | None:
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT heading FROM house_chunks
            WHERE path=? AND chunk_index=?
            """,
            (path, chunk),
        ).fetchone()
    if not row:
        raise NavigationStateError(
            f"saved chunk {chunk} is unavailable in {path}",
            code="navigation_chunk_unavailable",
            suggested_retry={
                "action": "stat",
                "path": path,
                "instruction": "Inspect the current document before choosing a new position.",
            },
        )
    return str(row["heading"]) if row["heading"] is not None else None


def _heading_matches(requested: str, actual: str | None) -> bool:
    wanted = requested.strip().casefold()
    present = (actual or "").strip().casefold()
    return bool(wanted and present and wanted in present)


def _document_locator(house: Any, payload: dict[str, Any]) -> str | None:
    if payload.get("bookmark_id"):
        try:
            bookmark = house.legible.bookmark(str(payload["bookmark_id"]))
            obj = house.legible.object_by_reference(str(bookmark["object_id"]))
        except (KeyError, ValueError):
            obj = None
        if obj and str(obj.get("object_type") or "") == "document":
            return str(obj["locator"])
    supplied = str(payload.get("path") or payload.get("reference") or "").strip()
    if not supplied:
        return None
    obj = house.legible.object_by_reference(supplied)
    if obj and str(obj.get("object_type") or "") == "document":
        return str(obj["locator"])
    return None


def _resolve_heading_anchor(house: Any, path: str, heading: str) -> dict[str, Any]:
    wanted = heading.strip()
    if not wanted:
        raise NavigationStateError(
            "saved heading is empty",
            code="navigation_heading_empty",
        )
    house.refresh_index()
    with house.db.connect() as connection:
        exact = connection.execute(
            """
            SELECT chunk_index, heading FROM house_chunks
            WHERE path=? AND lower(heading)=lower(?)
            ORDER BY chunk_index
            """,
            (path, wanted),
        ).fetchall()
        rows = list(exact)
        mode = "exact"
        if not rows:
            rows = list(
                connection.execute(
                    """
                    SELECT chunk_index, heading FROM house_chunks
                    WHERE path=? AND lower(heading) LIKE ?
                    ORDER BY chunk_index
                    """,
                    (path, f"%{wanted.casefold()}%"),
                ).fetchall()
            )
            mode = "partial"
    if not rows:
        raise NavigationStateError(
            f"saved heading {wanted!r} is unavailable in {path}",
            code="navigation_heading_unavailable",
            suggested_retry={
                "action": "stat",
                "path": path,
                "instruction": "Inspect current headings before choosing a new position.",
            },
        )

    normalized_labels = {
        str(row["heading"] or "").strip().casefold()
        for row in rows
    }
    starts: list[int] = []
    previous: int | None = None
    for row in rows:
        index = int(row["chunk_index"])
        if previous is None or index != previous + 1:
            starts.append(index)
        previous = index
    if len(starts) != 1 or (mode == "partial" and len(normalized_labels) != 1):
        raise NavigationStateError(
            f"saved heading {wanted!r} resolves to multiple places in {path}",
            code="navigation_heading_ambiguous",
            suggested_retry={
                "action": "search",
                "scope": path,
                "query": wanted,
                "instruction": "Choose a concrete chunk rather than guessing between heading matches.",
            },
        )
    return {
        "chunk": int(rows[0]["chunk_index"]),
        "heading": str(rows[0]["heading"] or ""),
        "match_mode": mode,
        "matching_chunks": len(rows),
    }


def _returned_chunks(result: dict[str, Any]) -> list[int]:
    return [int(value) for value in _CHUNK_LABEL.findall(str(result.get("text") or ""))]


def _cursor_public(
    house: Any,
    cursor_id: str | None,
    *,
    include_provenance: bool = True,
) -> dict[str, Any] | None:
    clean = str(cursor_id or "").strip()
    if not clean:
        return None
    row = _cursor_row(house, clean)
    if not row:
        return {"id": clean, "status": "unavailable"}
    value: dict[str, Any] = {
        "id": clean,
        "path": str(row["path"]),
        "next_chunk": int(row["next_chunk"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "expires_at": str(row["expires_at"]),
    }
    if include_provenance:
        provenance = _cursor_provenance(house, clean)
        if provenance:
            value["provenance"] = {
                "object_id": provenance.get("object_id"),
                "file_hash": provenance.get("file_hash"),
                "origin_action": provenance.get("origin_action"),
                "source_cursor_id": provenance.get("source_cursor_id"),
                "bookmark_id": provenance.get("bookmark_id"),
                "requested_heading": provenance.get("requested_heading"),
                "requested_chunk": provenance.get("requested_chunk"),
                "first_returned_chunk": provenance.get("first_returned_chunk"),
                "last_returned_chunk": provenance.get("last_returned_chunk"),
            }
        else:
            value["provenance"] = None
    return value


def _record_cursor_provenance(
    house: Any,
    *,
    cursor_id: str | None,
    object_id: str | None,
    path: str,
    file_hash: str,
    origin_action: str,
    requested_heading: str | None,
    requested_chunk: int | None,
    first_returned_chunk: int | None,
    last_returned_chunk: int | None,
    source_cursor_id: str | None = None,
    bookmark_id: str | None = None,
) -> None:
    clean = str(cursor_id or "").strip()
    if not clean:
        return
    _ensure_schema(house)
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO house_cursor_provenance
            (cursor_id, resident_id, object_id, path, file_hash, origin_action,
             source_cursor_id, bookmark_id, requested_heading, requested_chunk,
             first_returned_chunk, last_returned_chunk, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cursor_id) DO UPDATE SET
              object_id=excluded.object_id,
              path=excluded.path,
              file_hash=excluded.file_hash,
              origin_action=excluded.origin_action,
              source_cursor_id=COALESCE(excluded.source_cursor_id, house_cursor_provenance.source_cursor_id),
              bookmark_id=COALESCE(excluded.bookmark_id, house_cursor_provenance.bookmark_id),
              requested_heading=COALESCE(excluded.requested_heading, house_cursor_provenance.requested_heading),
              requested_chunk=COALESCE(excluded.requested_chunk, house_cursor_provenance.requested_chunk),
              first_returned_chunk=excluded.first_returned_chunk,
              last_returned_chunk=excluded.last_returned_chunk
            """,
            (
                clean,
                house.resident_id,
                object_id,
                path,
                file_hash,
                origin_action,
                source_cursor_id,
                bookmark_id,
                requested_heading,
                requested_chunk,
                first_returned_chunk,
                last_returned_chunk,
                utc_now_iso(),
            ),
        )


def _navigation_for_read(
    house: Any,
    *,
    requested_payload: dict[str, Any],
    result: dict[str, Any],
    action: str = "read",
    source_cursor: dict[str, Any] | None = None,
    bookmark_id: str | None = None,
) -> dict[str, Any]:
    chunks = _returned_chunks(result)
    first_chunk = chunks[0] if chunks else None
    last_chunk = chunks[-1] if chunks else None
    path = str(result.get("path") or "")
    requested_heading = str(requested_payload.get("heading") or "").strip() or None
    requested_chunk = (
        int(requested_payload["chunk"])
        if requested_payload.get("chunk") is not None
        else None
    )
    resolved_heading = (
        _chunk_heading(house, path, first_chunk)
        if path and first_chunk is not None
        else None
    )
    new_cursor_id = str(result.get("cursor") or "").strip() or None
    _record_cursor_provenance(
        house,
        cursor_id=new_cursor_id,
        object_id=str(result.get("object_id") or "") or None,
        path=path,
        file_hash=str(result.get("file_hash") or ""),
        origin_action=action,
        requested_heading=requested_heading,
        requested_chunk=requested_chunk,
        first_returned_chunk=first_chunk,
        last_returned_chunk=last_chunk,
        source_cursor_id=(str(source_cursor.get("id")) if source_cursor else None),
        bookmark_id=bookmark_id,
    )
    requested = {
        key: requested_payload[key]
        for key in ("path", "reference", "bookmark_id", "heading", "chunk")
        if requested_payload.get(key) is not None and requested_payload.get(key) != ""
    }
    next_step = (
        {
            "action": "continue",
            "cursor": new_cursor_id,
            "instruction": "Continue with this exact cursor.",
        }
        if new_cursor_id
        else {
            "action": None,
            "instruction": "No more source chunks are available from this read.",
        }
    )
    return {
        "schema_version": "vestigia.navigation.v0.1",
        "action": action,
        "requested": requested,
        "resolved": {
            "path": path,
            "object_id": result.get("object_id"),
            "file_hash": result.get("file_hash"),
            "start_chunk": first_chunk,
            "heading": resolved_heading,
        },
        "returned": {
            "first_chunk": first_chunk,
            "last_chunk": last_chunk,
            "chunk_count": len(chunks),
        },
        "source_cursor": source_cursor,
        "new_cursor": _cursor_public(house, new_cursor_id),
        "bookmark": {
            "bookmark_id": bookmark_id,
            "updated": False,
            "created": False,
        },
        "proof": {
            "requested_chunk_honored": (
                first_chunk == requested_chunk if requested_chunk is not None else None
            ),
            "requested_heading_honored": (
                _heading_matches(requested_heading, resolved_heading)
                if requested_heading
                else None
            ),
        },
        "next_step": next_step,
        "recovery": (
            "If visible text is truncated, this compact navigation block remains the position proof; "
            "inspect the action receipt for the full stored result."
        ),
    }


def _validate_heading_chunk(
    house: Any,
    *,
    path: str,
    heading: str,
    chunk: int,
) -> None:
    house.refresh_index()
    actual = _chunk_heading(house, path, chunk)
    if not _heading_matches(heading, actual):
        raise NavigationStateError(
            (
                f"saved heading/chunk mismatch for {path}: requested heading {heading!r} "
                f"does not describe chunk {chunk} (actual heading {actual!r})"
            ),
            code="navigation_heading_chunk_mismatch",
            suggested_retry={
                "action": "stat",
                "path": path,
                "instruction": "Inspect the current source and choose one concrete position; no fallback was used.",
            },
        )


def _wrap_read(house: Any, original_handler: Any):
    def handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        requested = dict(payload)
        normalized = dict(payload)
        heading = str(payload.get("heading") or "").strip()
        has_chunk = payload.get("chunk") is not None
        if heading and has_chunk:
            path = _document_locator(house, payload)
            if path:
                _validate_heading_chunk(
                    house,
                    path=path,
                    heading=heading,
                    chunk=max(0, int(payload["chunk"])),
                )
                # An exact chunk is the durable locator. The heading has now been
                # verified as an assertion about that chunk and must not override it.
                normalized.pop("heading", None)
        result = original_handler(normalized, context)
        navigation = _navigation_for_read(
            house,
            requested_payload=requested,
            result=result,
            action="read",
            bookmark_id=(str(payload.get("bookmark_id") or "") or None),
        )
        return {"navigation": navigation, **result}

    return handler


def _wrap_bookmark_add(house: Any, original_handler: Any):
    def handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        result = original_handler(payload, context)
        bookmark_id = str(result["bookmark_id"])
        item = house.legible.bookmark(bookmark_id)
        location = dict(item.get("location") or {})
        obj = result.get("object") if isinstance(result.get("object"), dict) else {}
        if str(obj.get("object_type") or "") == "document":
            house.refresh_index()
            current = house.legible.object_by_reference(str(obj.get("id") or "")) or obj
            location["file_hash"] = str(current.get("content_hash") or "")
            location["object_id"] = str(current.get("id") or "")
            with house.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE house_bookmarks
                    SET location_json=?, updated_at=?
                    WHERE id=? AND resident_id=? AND status='active'
                    """,
                    (
                        stable_json(location),
                        utc_now_iso(),
                        bookmark_id,
                        house.resident_id,
                    ),
                )
            cursor_id = str(location.get("cursor") or "").strip()
            if cursor_id and _cursor_provenance(house, cursor_id):
                provenance = _cursor_provenance(house, cursor_id) or {}
                _record_cursor_provenance(
                    house,
                    cursor_id=cursor_id,
                    object_id=str(current.get("id") or "") or None,
                    path=str(current.get("locator") or ""),
                    file_hash=str(current.get("content_hash") or ""),
                    origin_action=str(provenance.get("origin_action") or "read"),
                    requested_heading=(str(provenance.get("requested_heading") or "") or None),
                    requested_chunk=(
                        int(provenance["requested_chunk"])
                        if provenance.get("requested_chunk") is not None
                        else None
                    ),
                    first_returned_chunk=(
                        int(provenance["first_returned_chunk"])
                        if provenance.get("first_returned_chunk") is not None
                        else None
                    ),
                    last_returned_chunk=(
                        int(provenance["last_returned_chunk"])
                        if provenance.get("last_returned_chunk") is not None
                        else None
                    ),
                    source_cursor_id=(str(provenance.get("source_cursor_id") or "") or None),
                    bookmark_id=bookmark_id,
                )
            result = {
                **result,
                "bookmark": house.legible.bookmark(bookmark_id),
                "source_hash_bound": bool(location.get("file_hash")),
            }
        return result

    return handler


def _continue_handler(house: Any):
    def handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        cursor_id = str(payload.get("cursor") or "").strip()
        row = _cursor_row(house, cursor_id)
        if not row or str(row.get("status") or "") != "active":
            raise NavigationStateError(
                "unknown or closed house cursor",
                code="cursor_unknown_or_closed",
                suggested_retry={
                    "action": "read",
                    "instruction": "Open the source again to obtain a fresh verifiable cursor.",
                },
            )
        try:
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
        except ValueError as exc:
            raise NavigationStateError(
                "cursor has an invalid expiry record",
                code="cursor_provenance_invalid",
            ) from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            with house.db.connect() as connection:
                connection.execute(
                    "UPDATE house_cursors SET status='expired' WHERE id=?", (cursor_id,)
                )
            raise NavigationStateError(
                "house cursor expired",
                code="cursor_expired",
                suggested_retry={
                    "action": "read",
                    "path": str(row["path"]),
                    "chunk": int(row["next_chunk"]),
                },
            )

        provenance = _cursor_provenance(house, cursor_id)
        if provenance is None:
            raise NavigationStateError(
                "cursor predates verifiable navigation provenance; it will not be guessed forward",
                code="cursor_provenance_missing",
                suggested_retry={
                    "action": "read",
                    "path": str(row["path"]),
                    "chunk": int(row["next_chunk"]),
                    "instruction": "Reopen the source explicitly to mint a hash-bound cursor.",
                },
            )
        if str(provenance.get("path") or "") != str(row["path"]):
            raise NavigationStateError(
                "cursor path disagrees with its recorded provenance",
                code="cursor_path_mismatch",
            )
        house.refresh_index()
        document = _document_for_path(house, str(row["path"]))
        if document is None:
            raise NavigationStateError(
                "cursor source is no longer indexed",
                code="cursor_source_unavailable",
            )
        if str(document["content_hash"]) != str(provenance.get("file_hash") or ""):
            raise NavigationStateError(
                "cursor source changed after the cursor was created",
                code="cursor_source_hash_mismatch",
                suggested_retry={
                    "action": "read",
                    "path": str(row["path"]),
                    "instruction": "Inspect the changed source and choose a new position; the stale cursor was not consumed.",
                },
            )

        source_cursor = _cursor_public(house, cursor_id)
        read_payload = {
            "action": "read",
            "path": str(row["path"]),
            "chunk": int(row["next_chunk"]),
            "max_tokens": payload.get("max_tokens", 3000),
            "after": "continue",
        }
        result, _spec, _after = house.registry.dispatch(
            read_payload,
            turn_id=str(context.get("turn_id") or "") or None,
            context=context,
        )
        with house.db.connect() as connection:
            connection.execute(
                "UPDATE house_cursors SET status='consumed' WHERE id=? AND resident_id=?",
                (cursor_id, house.resident_id),
            )

        navigation = dict(result.get("navigation") or {})
        navigation["action"] = "continue"
        navigation["requested"] = {"cursor": cursor_id}
        navigation["source_cursor"] = source_cursor
        navigation["bookmark"] = {
            "bookmark_id": provenance.get("bookmark_id"),
            "updated": False,
            "created": False,
        }
        navigation["proof"] = {
            **dict(navigation.get("proof") or {}),
            "source_cursor_next_chunk_honored": (
                navigation.get("returned", {}).get("first_chunk")
                == int(row["next_chunk"])
            ),
            "source_hash_honored": True,
        }
        new_cursor_id = str(result.get("cursor") or "").strip() or None
        if new_cursor_id:
            returned = navigation.get("returned", {})
            resolved = navigation.get("resolved", {})
            _record_cursor_provenance(
                house,
                cursor_id=new_cursor_id,
                object_id=str(resolved.get("object_id") or "") or None,
                path=str(resolved.get("path") or row["path"]),
                file_hash=str(resolved.get("file_hash") or document["content_hash"]),
                origin_action="continue",
                requested_heading=None,
                requested_chunk=int(row["next_chunk"]),
                first_returned_chunk=(
                    int(returned["first_chunk"])
                    if returned.get("first_chunk") is not None
                    else None
                ),
                last_returned_chunk=(
                    int(returned["last_chunk"])
                    if returned.get("last_chunk") is not None
                    else None
                ),
                source_cursor_id=cursor_id,
                bookmark_id=(str(provenance.get("bookmark_id") or "") or None),
            )
            navigation["new_cursor"] = _cursor_public(house, new_cursor_id)
        return {
            "navigation": navigation,
            **{key: value for key, value in result.items() if key != "navigation"},
        }

    return handler


def _bookmark_open_handler(house: Any):
    def handler(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        bookmark_id = str(payload.get("bookmark_id") or "").strip()
        item = house.legible.bookmark(bookmark_id)
        location = dict(item.get("location") or {})
        house.refresh_index()
        obj = house.legible.object_by_reference(str(item["object_id"]))
        if not obj:
            raise NavigationStateError(
                "bookmark target is unavailable",
                code="bookmark_target_unavailable",
            )
        if str(obj.get("object_type") or "") != "document":
            opened, _spec, _after = house.registry.dispatch(
                {
                    "action": "object.inspect",
                    "reference": str(obj["id"]),
                    "after": "continue",
                },
                turn_id=str(context.get("turn_id") or "") or None,
                context=context,
            )
            return {
                "navigation": {
                    "schema_version": "vestigia.navigation.v0.1",
                    "action": "bookmark.open",
                    "requested": {"bookmark_id": bookmark_id, "location": location},
                    "resolved": {
                        "object_id": obj.get("id"),
                        "object_type": obj.get("object_type"),
                        "locator": obj.get("locator"),
                    },
                    "returned": {"first_chunk": None, "last_chunk": None, "chunk_count": 0},
                    "bookmark": {"bookmark_id": bookmark_id, "updated": False, "created": False},
                    "next_step": {"action": None, "instruction": "The bookmarked object was inspected."},
                },
                "bookmark": item,
                "opened": opened,
            }

        path = str(obj["locator"])
        current_hash = str(obj.get("content_hash") or "")
        bound_hash = str(location.get("file_hash") or "")
        if bound_hash and bound_hash != current_hash:
            raise NavigationStateError(
                "bookmark source changed after the bookmark was created",
                code="bookmark_source_hash_mismatch",
                suggested_retry={
                    "action": "stat",
                    "path": path,
                    "instruction": "Inspect the changed source before choosing a new saved position.",
                },
            )
        hash_binding = "verified" if bound_hash else "legacy_unbound"

        heading = str(location.get("heading") or "").strip()
        has_chunk = location.get("chunk") is not None
        cursor_id = str(location.get("cursor") or "").strip()
        cursor_row = _cursor_row(house, cursor_id) if cursor_id else None
        if cursor_row and str(cursor_row.get("path") or "") != path:
            raise NavigationStateError(
                "bookmark cursor points at a different document",
                code="bookmark_cursor_path_mismatch",
            )

        if has_chunk:
            read_payload: dict[str, Any] = {
                "action": "read",
                "path": path,
                "chunk": max(0, int(location["chunk"])),
                "max_tokens": payload.get("max_tokens", 3000),
                "after": "continue",
            }
            if heading:
                read_payload["heading"] = heading
            route = "chunk"
            opened, _spec, _after = house.registry.dispatch(
                read_payload,
                turn_id=str(context.get("turn_id") or "") or None,
                context=context,
            )
        elif heading:
            anchor = _resolve_heading_anchor(house, path, heading)
            opened, _spec, _after = house.registry.dispatch(
                {
                    "action": "read",
                    "path": path,
                    "heading": heading,
                    "chunk": int(anchor["chunk"]),
                    "max_tokens": payload.get("max_tokens", 3000),
                    "after": "continue",
                },
                turn_id=str(context.get("turn_id") or "") or None,
                context=context,
            )
            route = f"heading:{anchor['match_mode']}"
        elif cursor_id:
            opened, _spec, _after = house.registry.dispatch(
                {
                    "action": "continue",
                    "cursor": cursor_id,
                    "max_tokens": payload.get("max_tokens", 3000),
                    "after": "continue",
                },
                turn_id=str(context.get("turn_id") or "") or None,
                context=context,
            )
            route = "cursor"
        else:
            opened, _spec, _after = house.registry.dispatch(
                {
                    "action": "read",
                    "path": path,
                    "chunk": 0,
                    "max_tokens": payload.get("max_tokens", 3000),
                    "after": "continue",
                },
                turn_id=str(context.get("turn_id") or "") or None,
                context=context,
            )
            route = "document_start"

        navigation = dict(opened.get("navigation") or {})
        navigation["action"] = "bookmark.open"
        navigation["requested"] = {
            "bookmark_id": bookmark_id,
            "location": location,
        }
        navigation["resolution_route"] = route
        navigation["bookmark"] = {
            "bookmark_id": bookmark_id,
            "updated": False,
            "created": False,
            "source_hash_binding": hash_binding,
        }
        warnings = list(navigation.get("warnings") or [])
        if hash_binding == "legacy_unbound":
            warnings.append(
                "This bookmark predates source-hash binding; the current source is verified, "
                "but the Runtime cannot prove that its bytes are identical to bookmark creation time."
            )
        if cursor_id and route != "cursor":
            warnings.append(
                "A stored cursor was not used because the bookmark also contains a durable heading/chunk locator."
            )
        if warnings:
            navigation["warnings"] = warnings

        new_cursor_id = str(opened.get("cursor") or "").strip() or None
        if new_cursor_id:
            returned = navigation.get("returned", {})
            resolved = navigation.get("resolved", {})
            existing = _cursor_provenance(house, new_cursor_id) or {}
            _record_cursor_provenance(
                house,
                cursor_id=new_cursor_id,
                object_id=str(resolved.get("object_id") or obj.get("id") or "") or None,
                path=str(resolved.get("path") or path),
                file_hash=str(resolved.get("file_hash") or current_hash),
                origin_action="bookmark.open",
                requested_heading=(heading or None),
                requested_chunk=(
                    int(location["chunk"])
                    if location.get("chunk") is not None
                    else (
                        int(existing["requested_chunk"])
                        if existing.get("requested_chunk") is not None
                        else None
                    )
                ),
                first_returned_chunk=(
                    int(returned["first_chunk"])
                    if returned.get("first_chunk") is not None
                    else None
                ),
                last_returned_chunk=(
                    int(returned["last_chunk"])
                    if returned.get("last_chunk") is not None
                    else None
                ),
                source_cursor_id=(cursor_id or None) if route == "cursor" else None,
                bookmark_id=bookmark_id,
            )
            navigation["new_cursor"] = _cursor_public(house, new_cursor_id)

        return {
            "navigation": navigation,
            "bookmark": item,
            "opened": opened,
        }

    return handler


def _install(house: Any) -> None:
    _ensure_schema(house)
    original_read = house.registry.handler("read")
    original_bookmark_add = house.registry.handler("bookmark.add")
    house.registry.replace_handler("read", _wrap_read(house, original_read))
    house.registry.replace_handler("bookmark.add", _wrap_bookmark_add(house, original_bookmark_add))
    house.registry.replace_handler("continue", _continue_handler(house))
    house.registry.replace_handler("bookmark.open", _bookmark_open_handler(house))


def register_composition() -> None:
    """Install falsifiable bookmark/cursor navigation through explicit composition."""

    from .composition import register_capability_installer

    register_capability_installer("navigation.hardening", _install, order=75)
