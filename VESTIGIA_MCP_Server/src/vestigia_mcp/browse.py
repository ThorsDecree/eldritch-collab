"""Durable, signed cursors for bounded archive browsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class BrowseCursorError(ValueError):
    """A continuation cursor is malformed, invalid, or no longer usable."""


_MAX_BROWSE_CURSOR_CHARS = 8_192


@dataclass(frozen=True)
class BrowseSession:
    id: str
    kind: str
    source: str
    path: str
    policy_scope: str
    page_bytes: int
    snapshot_sha256: str
    size: int
    source_revision: str | None
    created_at: str
    expires_at: str
    cursor: str


class BrowseCursorCodec:
    """HMAC-protected cursor claims, independent from legacy list cursors."""

    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("Browse cursor secret must be at least 32 bytes")
        self._secret = secret

    def encode(self, kind: str, claims: dict[str, object]) -> str:
        payload_claims = dict(claims)
        payload_claims["kind"] = kind
        payload = json.dumps(
            payload_claims,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def decode(self, token: str, expected_kind: str) -> dict[str, object]:
        if not isinstance(token, str) or not token:
            raise BrowseCursorError("malformed browse cursor")
        if len(token) > _MAX_BROWSE_CURSOR_CHARS:
            raise BrowseCursorError("browse cursor exceeds the encoded size ceiling")
        if "." not in token:
            raise BrowseCursorError("unsupported legacy browse cursor")
        try:
            encoded_payload, encoded_signature = token.split(".")
            payload = _b64decode(encoded_payload)
            signature = _b64decode(encoded_signature)
        except (AttributeError, ValueError) as exc:
            raise BrowseCursorError("malformed browse cursor") from exc

        expected_signature = hmac.new(
            self._secret, payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise BrowseCursorError("invalid browse cursor signature")

        try:
            claims = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowseCursorError("malformed browse cursor") from exc
        if not isinstance(claims, dict):
            raise BrowseCursorError("malformed browse cursor")
        if claims.get("kind") != expected_kind:
            raise BrowseCursorError("browse cursor operation does not match")
        _validate_claim_types(claims)
        return claims


class BrowseSessionStore:
    """Small state-backed store for snapshot-bound archive browse sessions."""

    def __init__(
        self,
        state_dir: Path,
        *,
        ttl_seconds: int,
        secret: bytes | None = None,
    ):
        if ttl_seconds <= 0:
            raise ValueError("Browse session TTL must be positive")
        self._state_dir = state_dir.expanduser()
        self._sessions_dir = self._state_dir / "browse_sessions"
        self._ttl_seconds = ttl_seconds
        self._codec = BrowseCursorCodec(secret or self._load_or_create_secret())

    def create(
        self,
        *,
        kind: str,
        source: str,
        path: str,
        policy_scope: str,
        page_bytes: int,
        snapshot_sha256: str,
        size: int,
        source_revision: str | None = None,
        now: datetime | None = None,
    ) -> BrowseSession:
        if not kind or not source or not path or not policy_scope:
            raise ValueError("Browse session fields must not be blank")
        if page_bytes <= 0 or size < 0:
            raise ValueError("Browse session page size and file size are invalid")
        if len(snapshot_sha256) != 64:
            raise ValueError("Browse session snapshot digest must be SHA-256")

        created = _utc_now(now)
        expires = created + timedelta(seconds=self._ttl_seconds)
        browse_id = uuid.uuid4().hex
        claims: dict[str, object] = {
            "v": 2,
            "kind": kind,
            "browse_id": browse_id,
            "source": source,
            "path": path,
            "offset": 0,
            "page_bytes": page_bytes,
            "policy_scope": policy_scope,
            "expires_at": _timestamp(expires),
        }
        session = BrowseSession(
            id=browse_id,
            kind=kind,
            source=source,
            path=path,
            policy_scope=policy_scope,
            page_bytes=page_bytes,
            snapshot_sha256=snapshot_sha256,
            size=size,
            source_revision=source_revision,
            created_at=_timestamp(created),
            expires_at=_timestamp(expires),
            cursor=self._codec.encode(kind, claims),
        )
        self._write_session(session)
        return session

    def decode(
        self,
        cursor: str,
        expected_kind: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        claims = self._codec.decode(cursor, expected_kind)
        expires_at = _parse_timestamp(claims["expires_at"])
        if _utc_now(now) > expires_at:
            raise BrowseCursorError("browse cursor expired")
        return claims

    def load(self, browse_id: str) -> BrowseSession:
        if not browse_id or "/" in browse_id or "\\" in browse_id:
            raise BrowseCursorError("malformed browse cursor")
        path = self._sessions_dir / f"{browse_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BrowseCursorError("browse session not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowseCursorError("malformed browse session") from exc
        if not isinstance(raw, dict):
            raise BrowseCursorError("malformed browse session")
        try:
            session = BrowseSession(
                id=_string(raw, "id"),
                kind=_string(raw, "kind"),
                source=_string(raw, "source"),
                path=_string(raw, "path"),
                policy_scope=_string(raw, "policy_scope"),
                page_bytes=_positive_int(raw, "page_bytes"),
                snapshot_sha256=_string(raw, "snapshot_sha256"),
                size=_nonnegative_int(raw, "size"),
                source_revision=_optional_string(raw, "source_revision"),
                created_at=_string(raw, "created_at"),
                expires_at=_string(raw, "expires_at"),
                cursor="",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BrowseCursorError("malformed browse session") from exc
        if session.id != browse_id or len(session.snapshot_sha256) != 64:
            raise BrowseCursorError("malformed browse session")
        return session

    def validate_continuation(
        self,
        claims: dict[str, object],
        *,
        policy_scope: str,
        page_bytes: int,
    ) -> BrowseSession:
        if claims.get("policy_scope") != policy_scope:
            raise BrowseCursorError("browse cursor scope does not match")
        if claims.get("page_bytes") != page_bytes:
            raise BrowseCursorError("browse cursor page size does not match")
        session = self.load(_string(claims, "browse_id"))
        expected = {
            "kind": session.kind,
            "source": session.source,
            "path": session.path,
            "policy_scope": session.policy_scope,
            "page_bytes": session.page_bytes,
            "expires_at": session.expires_at,
        }
        if any(claims.get(key) != value for key, value in expected.items()):
            raise BrowseCursorError("browse cursor does not match its session")
        return session

    def cursor_for(self, session: BrowseSession, *, offset: int) -> str:
        if offset < 0 or offset > session.size:
            raise ValueError("Browse cursor offset is outside the snapshot")
        return self._codec.encode(
            session.kind,
            {
                "v": 2,
                "kind": session.kind,
                "browse_id": session.id,
                "source": session.source,
                "path": session.path,
                "offset": offset,
                "page_bytes": session.page_bytes,
                "policy_scope": session.policy_scope,
                "expires_at": session.expires_at,
            },
        )

    def _load_or_create_secret(self) -> bytes:
        secret_path = self._state_dir / "cursor_secret.bin"
        try:
            secret = secret_path.read_bytes()
        except FileNotFoundError:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            secret = secrets.token_bytes(32)
            try:
                descriptor = os.open(
                    secret_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                return secret_path.read_bytes()
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secret)
        if len(secret) < 32:
            raise BrowseCursorError("browse cursor secret is invalid")
        return secret

    def _write_session(self, session: BrowseSession) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "id": session.id,
            "kind": session.kind,
            "source": session.source,
            "path": session.path,
            "policy_scope": session.policy_scope,
            "page_bytes": session.page_bytes,
            "snapshot_sha256": session.snapshot_sha256,
            "size": session.size,
            "source_revision": session.source_revision,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._sessions_dir,
            prefix=f".{session.id}.",
            suffix=".tmp",
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self._sessions_dir / f"{session.id}.json")
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value:
        raise ValueError("empty base64 value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _timestamp(value: datetime) -> str:
    return _utc_now(value).isoformat()


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Browse timestamps must include a timezone")
    return current.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise BrowseCursorError("malformed browse cursor")
    try:
        return _utc_now(datetime.fromisoformat(value))
    except ValueError as exc:
        raise BrowseCursorError("malformed browse cursor") from exc


def _validate_claim_types(claims: dict[str, object]) -> None:
    if claims.get("v") != 2:
        raise BrowseCursorError("malformed browse cursor")
    for name in ("kind", "browse_id", "source", "path", "policy_scope", "expires_at"):
        if not isinstance(claims.get(name), str) or not claims[name]:
            raise BrowseCursorError("malformed browse cursor")
    if not isinstance(claims.get("offset"), int) or claims["offset"] < 0:
        raise BrowseCursorError("malformed browse cursor")
    if not isinstance(claims.get("page_bytes"), int) or claims["page_bytes"] <= 0:
        raise BrowseCursorError("malformed browse cursor")


def _string(record: dict[str, object], name: str) -> str:
    value = record[name]
    if not isinstance(value, str) or not value:
        raise ValueError(name)
    return value


def _optional_string(record: dict[str, object], name: str) -> str | None:
    value = record.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(name)
    return value


def _positive_int(record: dict[str, object], name: str) -> int:
    value = record[name]
    if not isinstance(value, int) or value <= 0:
        raise ValueError(name)
    return value


def _nonnegative_int(record: dict[str, object], name: str) -> int:
    value = record[name]
    if not isinstance(value, int) or value < 0:
        raise ValueError(name)
    return value
