from __future__ import annotations

from typing import Any

from .navigation_hardening import (
    NavigationStateError,
    _cursor_provenance,
    _cursor_row,
    _record_cursor_provenance,
    _resolve_heading_anchor,
)


def _install(house: Any) -> None:
    prior_read = house.registry.handler("read")
    prior_continue = house.registry.handler("continue")

    # Navigation hardening adds new falsifiability failures, but callers already
    # rely on the established cursor lifecycle exception families. Preserve those
    # public contracts for ordinary unknown/closed and expired cursors while
    # leaving genuinely new navigation failures as NavigationStateError.
    from .house_tools import HouseCursorError, HouseCursorExpiredError

    def continue_with_cursor_error_compat(
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return prior_continue(payload, context)
        except NavigationStateError as exc:
            code = str(getattr(exc, "house_error_code", "") or "")
            suggested_retry = getattr(exc, "house_suggested_retry", None)
            message = str(exc.args[0]) if exc.args else str(exc)
            if code == "cursor_unknown_or_closed":
                raise HouseCursorError(
                    message,
                    code=code,
                    suggested_retry=suggested_retry,
                ) from exc
            if code == "cursor_expired":
                raise HouseCursorExpiredError(
                    message,
                    code=code,
                    suggested_retry=suggested_retry,
                ) from exc
            raise

    def read_with_bookmark_compat(
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        bookmark_id = str(payload.get("bookmark_id") or "").strip()
        if not bookmark_id:
            return prior_read(payload, context)

        item = house.legible.bookmark(bookmark_id)
        location = dict(item.get("location") or {})
        house.refresh_index()
        obj = house.legible.object_by_reference(str(item["object_id"]))
        if not obj or str(obj.get("object_type") or "") != "document":
            raise NavigationStateError(
                "read(bookmark_id=...) requires a document bookmark; use bookmark.open for other objects",
                code="bookmark_read_target_not_document",
                suggested_retry={
                    "action": "bookmark.open",
                    "bookmark_id": bookmark_id,
                },
            )

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

        heading = str(location.get("heading") or "").strip()
        has_chunk = location.get("chunk") is not None
        cursor_id = str(location.get("cursor") or "").strip()

        if cursor_id:
            cursor = _cursor_row(house, cursor_id)
            if cursor and str(cursor.get("path") or "") != path:
                raise NavigationStateError(
                    "bookmark cursor points at a different document",
                    code="bookmark_cursor_path_mismatch",
                )

        if has_chunk:
            normalized: dict[str, Any] = {
                "action": "read",
                "path": path,
                "chunk": max(0, int(location["chunk"])),
                "max_tokens": payload.get("max_tokens", 3000),
                "after": "continue",
            }
            if heading:
                normalized["heading"] = heading
            result = prior_read(normalized, context)
            route = "chunk"
        elif heading:
            anchor = _resolve_heading_anchor(house, path, heading)
            result = prior_read(
                {
                    "action": "read",
                    "path": path,
                    "heading": heading,
                    "chunk": int(anchor["chunk"]),
                    "max_tokens": payload.get("max_tokens", 3000),
                    "after": "continue",
                },
                context,
            )
            route = f"heading:{anchor['match_mode']}"
        elif cursor_id:
            result, _spec, _after = house.registry.dispatch(
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
            result = prior_read(
                {
                    "action": "read",
                    "path": path,
                    "chunk": 0,
                    "max_tokens": payload.get("max_tokens", 3000),
                    "after": "continue",
                },
                context,
            )
            route = "document_start"

        navigation = dict(result.get("navigation") or {})
        navigation["action"] = "read"
        navigation["requested"] = {
            "bookmark_id": bookmark_id,
            "location": location,
        }
        navigation["resolution_route"] = route
        navigation["bookmark"] = {
            "bookmark_id": bookmark_id,
            "updated": False,
            "created": False,
            "source_hash_binding": "verified" if bound_hash else "legacy_unbound",
        }
        warnings = list(navigation.get("warnings") or [])
        if not bound_hash:
            warnings.append(
                "This bookmark predates source-hash binding; the current source is verified, "
                "but the Runtime cannot prove byte identity to bookmark creation time."
            )
        if cursor_id and route != "cursor":
            warnings.append(
                "A stored cursor was not used because the bookmark also contains a durable heading/chunk locator."
            )
        if warnings:
            navigation["warnings"] = warnings

        new_cursor_id = str(result.get("cursor") or "").strip() or None
        if new_cursor_id:
            existing = _cursor_provenance(house, new_cursor_id) or {}
            returned = navigation.get("returned", {})
            resolved = navigation.get("resolved", {})
            _record_cursor_provenance(
                house,
                cursor_id=new_cursor_id,
                object_id=str(resolved.get("object_id") or obj.get("id") or "") or None,
                path=str(resolved.get("path") or path),
                file_hash=str(resolved.get("file_hash") or current_hash),
                origin_action="read.bookmark",
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
                source_cursor_id=(cursor_id or None) if route == "cursor" else (
                    str(existing.get("source_cursor_id") or "") or None
                ),
                bookmark_id=bookmark_id,
            )
            navigation["new_cursor"] = {
                **dict(navigation.get("new_cursor") or {}),
                "provenance": {
                    **dict((navigation.get("new_cursor") or {}).get("provenance") or {}),
                    "bookmark_id": bookmark_id,
                    "origin_action": "read.bookmark",
                },
            }

        return {
            "navigation": navigation,
            **{key: value for key, value in result.items() if key != "navigation"},
        }

    house.registry.replace_handler("continue", continue_with_cursor_error_compat)
    house.registry.replace_handler("read", read_with_bookmark_compat)


def register_composition() -> None:
    """Keep established bookmark and cursor lifecycle contracts on hardened navigation."""

    from .composition import register_capability_installer

    register_capability_installer("navigation.bookmark_compat", _install, order=76)
