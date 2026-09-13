from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


CURSOR_SCHEMA_VERSION = "vestigia.cursor.v0.1"
PAGE_SCHEMA_VERSION = "vestigia.page.v0.1"
_MAX_CURSOR_CHARS = 8192


class CursorError(ValueError):
    pass


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def encode_cursor(kind: str, state: dict[str, Any]) -> str:
    body = {
        "schema_version": CURSOR_SCHEMA_VERSION,
        "kind": kind,
        "state": state,
    }
    encoded_body = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope = {
        "body": body,
        "checksum": hashlib.sha256(encoded_body).hexdigest(),
    }
    raw = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, expected_kind: str) -> dict[str, Any]:
    if not token or len(token) > _MAX_CURSOR_CHARS:
        raise CursorError("Cursor is empty or exceeds the encoded size ceiling")
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(token + padding, altchars=b"-_", validate=True)
        envelope = json.loads(raw.decode("utf-8"))
        body = envelope["body"]
        checksum = envelope["checksum"]
        encoded_body = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorError("Cursor is malformed") from exc
    if not isinstance(checksum, str) or not hmac.compare_digest(
        checksum, hashlib.sha256(encoded_body).hexdigest()
    ):
        raise CursorError("Cursor checksum is invalid")
    if body.get("schema_version") != CURSOR_SCHEMA_VERSION:
        raise CursorError("Cursor schema version is unsupported")
    if body.get("kind") != expected_kind:
        raise CursorError("Cursor belongs to a different operation")
    state = body.get("state")
    if not isinstance(state, dict):
        raise CursorError("Cursor state is invalid")
    return state


def page_metadata(
    *,
    limit: int,
    returned: int,
    offset: int,
    total: int,
    next_cursor: str | None,
    view_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": PAGE_SCHEMA_VERSION,
        "limit": limit,
        "returned": returned,
        "offset": offset,
        "total": total,
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
        "view_sha256": view_sha256,
    }
