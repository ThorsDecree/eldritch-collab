from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime
from typing import Any

from .db import ContinuityDB
from .utils import new_id, stable_json, utc_now_iso


MAX_MESSAGE_LENGTH = 1800
_DESTINATIONS = {"discord_channel", "discord_dm"}
_MENTION_PATTERN = re.compile(r"<@!?\d+>|<@&\d+>|@everyone\b|@here\b", re.IGNORECASE)


def _timestamp(value: str, *, name: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(UTC).isoformat()


def _now(value: str | None = None) -> datetime:
    return datetime.fromisoformat(_timestamp(value or utc_now_iso(), name="now"))


def _utf16_length(text: str) -> int:
    try:
        return len(text.encode("utf-16-le", errors="strict")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("message contains invalid Unicode") from exc


def _validate_body(body: str, maximum: int) -> int:
    if not isinstance(body, str):
        raise TypeError("message body must be text")
    try:
        body.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("message contains invalid Unicode") from exc
    if not body.strip():
        raise ValueError("message body must not be empty")
    if _MENTION_PATTERN.search(body):
        raise ValueError("message body contains a Discord mention")
    for character in body:
        if unicodedata.category(character) == "Cc" and character not in "\t\n\r":
            raise ValueError("message body contains a disallowed control character")
    measured = _utf16_length(body)
    if measured > maximum:
        raise ValueError(
            f"message is {measured} UTF-16 code units; grant limit is {maximum}"
        )
    return measured


class PrivateMessageService:
    """Resident-scoped storage and lifecycle rules for private outbound messages."""

    def __init__(self, db: ContinuityDB, resident_id: str) -> None:
        self.db = db
        self.resident_id = str(resident_id).strip()
        if not self.resident_id:
            raise ValueError("resident_id is required")

    def _event(
        self,
        connection: sqlite3.Connection,
        *,
        object_kind: str,
        object_id: str,
        event_type: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = new_id("msev")
        connection.execute(
            """
            INSERT INTO private_message_events
            (id, resident_id, object_kind, object_id, event_type, actor, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                self.resident_id,
                object_kind,
                object_id,
                event_type,
                actor,
                stable_json(metadata or {}),
                utc_now_iso(),
            ),
        )
        return event_id

    def _row_grant(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "resident_id": str(row["resident_id"]),
            "destination_kind": str(row["destination_kind"]),
            "destination_id": str(row["destination_id"]),
            "active_from": str(row["active_from"]),
            "expires_at": str(row["expires_at"]),
            "message_quota": int(row["message_quota"]),
            "max_length": int(row["max_length"]),
            "state": str(row["state"]),
            "terms": json.loads(str(row["terms_json"])),
            "terms_hash": str(row["terms_hash"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _grant_row(self, connection: sqlite3.Connection, grant_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM private_message_grants WHERE id=? AND resident_id=?",
            (grant_id, self.resident_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown private message grant: {grant_id}")
        return row

    def _expire_grant_if_due(
        self, connection: sqlite3.Connection, row: sqlite3.Row, now: datetime
    ) -> sqlite3.Row:
        if str(row["state"]) in {"pending_resident_acceptance", "active"}:
            expiry = datetime.fromisoformat(str(row["expires_at"]))
            if now >= expiry:
                connection.execute(
                    "UPDATE private_message_grants SET state='expired', updated_at=? WHERE id=?",
                    (utc_now_iso(), str(row["id"])),
                )
                self._event(
                    connection,
                    object_kind="grant",
                    object_id=str(row["id"]),
                    event_type="expired",
                    actor="runtime",
                    metadata={"terms_hash": str(row["terms_hash"])},
                )
                self._cancel_queued_for_grant(
                    connection, str(row["id"]), actor="runtime",
                    event_type="grant_expired",
                )
                return self._grant_row(connection, str(row["id"]))
        return row

    def create_grant(
        self,
        destination_kind: str,
        destination_id: str,
        active_from: str,
        expires_at: str,
        message_quota: int,
        max_length: int = MAX_MESSAGE_LENGTH,
    ) -> dict[str, Any]:
        kind = str(destination_kind).strip().lower()
        target_id = str(destination_id).strip()
        if kind not in _DESTINATIONS:
            raise ValueError("destination_kind must be discord_channel or discord_dm")
        if not target_id or not target_id.isdigit():
            raise ValueError("destination_id must be an exact numeric Discord ID")
        starts = _timestamp(active_from, name="active_from")
        expires = _timestamp(expires_at, name="expires_at")
        if datetime.fromisoformat(expires) <= datetime.fromisoformat(starts):
            raise ValueError("expires_at must be after active_from")
        quota = int(message_quota)
        limit = int(max_length)
        if quota <= 0:
            raise ValueError("message_quota must be positive")
        if limit < 1 or limit > MAX_MESSAGE_LENGTH:
            raise ValueError(f"max_length must be between 1 and {MAX_MESSAGE_LENGTH}")
        grant_id = new_id("grant")
        created = utc_now_iso()
        terms = {
            "schema_version": "v1",
            "resident_id": self.resident_id,
            "destination_kind": kind,
            "destination_id": target_id,
            "active_from": starts,
            "expires_at": expires,
            "message_quota": quota,
            "max_length": limit,
        }
        terms_json = stable_json(terms)
        terms_hash = hashlib.sha256(terms_json.encode("utf-8")).hexdigest()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO private_message_grants
                (id, resident_id, destination_kind, destination_id, active_from, expires_at,
                 message_quota, max_length, state, terms_json, terms_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_resident_acceptance', ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    self.resident_id,
                    kind,
                    target_id,
                    starts,
                    expires,
                    quota,
                    limit,
                    terms_json,
                    terms_hash,
                    created,
                    created,
                ),
            )
            self._event(
                connection,
                object_kind="grant",
                object_id=grant_id,
                event_type="created",
                actor="operator",
                metadata={"terms_hash": terms_hash, "terms": terms},
            )
            row = self._grant_row(connection, grant_id)
            return self._row_grant(row)

    def get_grant(self, grant_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_grant_if_due(
                connection, self._grant_row(connection, grant_id), _now()
            )
            return self._row_grant(row)

    def list_grants(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM private_message_grants WHERE resident_id=? ORDER BY created_at, id",
                (self.resident_id,),
            ).fetchall()
            now = _now()
            return [
                self._row_grant(self._expire_grant_if_due(connection, row, now))
                for row in rows
            ]

    def grant_action(
        self, grant_id: str, mode: str, terms_hash: str | None = None
    ) -> dict[str, Any]:
        action = str(mode).strip().lower()
        if action not in {"inspect", "accept", "decline", "relinquish"}:
            raise ValueError("mode must be inspect, accept, decline, or relinquish")
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_grant_if_due(
                connection, self._grant_row(connection, grant_id), _now()
            )
            grant = self._row_grant(row)
            if action == "inspect":
                return grant
            if action == "accept":
                if grant["state"] != "pending_resident_acceptance":
                    raise PermissionError("grant is not pending resident acceptance")
                if terms_hash != grant["terms_hash"]:
                    raise ValueError("terms hash does not match the exact grant terms")
                state, event_type = "active", "accepted"
            elif action == "decline":
                if grant["state"] != "pending_resident_acceptance":
                    raise PermissionError("only a pending grant can be declined")
                state, event_type = "relinquished", "declined"
            else:
                if grant["state"] != "active":
                    raise PermissionError("only an active grant can be relinquished")
                state, event_type = "relinquished", "relinquished"
            updated = utc_now_iso()
            connection.execute(
                "UPDATE private_message_grants SET state=?, updated_at=? WHERE id=? AND resident_id=?",
                (state, updated, grant_id, self.resident_id),
            )
            receipt_id = self._event(
                connection,
                object_kind="grant",
                object_id=grant_id,
                event_type=event_type,
                actor="resident",
                metadata={"terms_hash": grant["terms_hash"], "authorized_at": updated},
            )
            updated_grant = self._row_grant(self._grant_row(connection, grant_id))
            updated_grant["receipt_id"] = receipt_id
            return updated_grant

    def revoke_grant(self, grant_id: str, actor: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_grant_if_due(
                connection, self._grant_row(connection, grant_id), _now()
            )
            grant = self._row_grant(row)
            if grant["state"] not in {"pending_resident_acceptance", "active"}:
                raise PermissionError(f"grant cannot be revoked from state {grant['state']}")
            updated = utc_now_iso()
            connection.execute(
                "UPDATE private_message_grants SET state='revoked', updated_at=? WHERE id=?",
                (updated, grant_id),
            )
            self._cancel_queued_for_grant(
                connection, grant_id, actor=str(actor), event_type="grant_revoked"
            )
            receipt_id = self._event(
                connection,
                object_kind="grant",
                object_id=grant_id,
                event_type="revoked",
                actor=str(actor),
                metadata={"terms_hash": grant["terms_hash"], "authorized_at": updated},
            )
            updated_grant = self._row_grant(self._grant_row(connection, grant_id))
            updated_grant["receipt_id"] = receipt_id
            return updated_grant

    def draft(
        self,
        grant_id: str,
        body: str,
        *,
        turn_id: str,
        provider_round: int,
    ) -> dict[str, Any]:
        turn = str(turn_id).strip()
        round_index = int(provider_round)
        if not turn:
            raise ValueError("turn_id is required")
        if round_index < 0:
            raise ValueError("provider_round must be non-negative")
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._expire_grant_if_due(
                connection, self._grant_row(connection, grant_id), _now()
            )
            grant = self._row_grant(row)
            now = _now()
            if grant["state"] != "active":
                raise PermissionError(f"grant is not active: {grant['state']}")
            if now < datetime.fromisoformat(grant["active_from"]):
                raise PermissionError("grant is not active yet")
            length = _validate_body(body, int(grant["max_length"]))
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            draft_id = new_id("msgdraft")
            created = utc_now_iso()
            connection.execute(
                """
                INSERT INTO private_message_drafts
                (id, resident_id, grant_id, body, body_hash, length_utf16,
                 created_turn_id, provider_round, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'drafted', ?)
                """,
                (
                    draft_id,
                    self.resident_id,
                    grant_id,
                    body,
                    body_hash,
                    length,
                    turn,
                    round_index,
                    created,
                ),
            )
            receipt_id = self._event(
                connection,
                object_kind="draft",
                object_id=draft_id,
                event_type="drafted",
                actor="resident",
                metadata={
                    "grant_id": grant_id,
                    "terms_hash": grant["terms_hash"],
                    "draft_hash": body_hash,
                    "length_utf16": length,
                    "source_turn_id": turn,
                    "provider_round": round_index,
                },
            )
            return {
                "id": draft_id,
                "grant_id": grant_id,
                "destination_kind": grant["destination_kind"],
                "destination_id": grant["destination_id"],
                "draft_hash": body_hash,
                "length_utf16": length,
                "state": "drafted",
                "receipt_id": receipt_id,
            }

    def inspect_draft(self, draft_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, g.destination_kind, g.destination_id, g.terms_hash
                FROM private_message_drafts d
                JOIN private_message_grants g ON g.id=d.grant_id
                WHERE d.id=? AND d.resident_id=? AND g.resident_id=?
                """,
                (draft_id, self.resident_id, self.resident_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown private message draft: {draft_id}")
        return {
            "id": str(row["id"]),
            "grant_id": str(row["grant_id"]),
            "terms_hash": str(row["terms_hash"]),
            "destination_kind": str(row["destination_kind"]),
            "destination_id": str(row["destination_id"]),
            "draft_hash": str(row["body_hash"]),
            "length_utf16": int(row["length_utf16"]),
            "state": str(row["status"]),
            "created_turn_id": str(row["created_turn_id"]),
            "provider_round": int(row["provider_round"]),
            "created_at": str(row["created_at"]),
        }

    def submit(
        self,
        draft_id: str,
        draft_hash: str,
        *,
        turn_id: str,
        provider_round: int,
        source_invocation: str,
    ) -> dict[str, Any]:
        """Queue one exact draft after a distinct resident act in private curation."""

        if source_invocation != "private_curation":
            raise PermissionError("message submission is available only in private curation")
        turn = str(turn_id).strip()
        round_index = int(provider_round)
        if not turn:
            raise ValueError("turn_id is required")
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = connection.execute(
                "SELECT * FROM private_message_drafts WHERE id=? AND resident_id=?",
                (draft_id, self.resident_id),
            ).fetchone()
            if draft is None:
                raise KeyError(f"Unknown private message draft: {draft_id}")
            if str(draft["body_hash"]) != str(draft_hash):
                raise ValueError("draft hash does not match the exact stored message")
            if str(draft["created_turn_id"]) != turn:
                raise PermissionError("draft must be submitted in the same private turn")
            if round_index <= int(draft["provider_round"]):
                raise PermissionError("submission requires a later provider round")
            if str(draft["status"]) == "submitted":
                existing = connection.execute(
                    """
                    SELECT o.*, r.id AS reservation_id
                    FROM private_message_outbox o
                    JOIN private_message_quota_reservations r ON r.outbox_id=o.id
                    WHERE o.draft_id=? AND o.resident_id=?
                    """,
                    (draft_id, self.resident_id),
                ).fetchone()
                if existing is not None:
                    return self._outbox_summary(existing)
            if str(draft["status"]) != "drafted":
                raise PermissionError(f"draft is not submit-ready: {draft['status']}")
            grant_row = self._expire_grant_if_due(
                connection, self._grant_row(connection, str(draft["grant_id"])), _now()
            )
            grant = self._row_grant(grant_row)
            now = _now()
            if grant["state"] != "active":
                raise PermissionError(f"grant is not active: {grant['state']}")
            if now < datetime.fromisoformat(grant["active_from"]):
                raise PermissionError("grant is not active yet")
            reserved = connection.execute(
                """
                SELECT COUNT(*) AS count FROM private_message_quota_reservations
                WHERE grant_id=? AND resident_id=? AND state IN ('reserved', 'consumed')
                """,
                (grant["id"], self.resident_id),
            ).fetchone()
            if int(reserved["count"]) >= grant["message_quota"]:
                raise PermissionError("grant message quota is exhausted")
            outbox_id = new_id("msgout")
            reservation_id = new_id("msgres")
            submitted_at = utc_now_iso()
            connection.execute(
                """
                INSERT INTO private_message_outbox
                (id, resident_id, grant_id, draft_id, destination_kind, destination_id,
                 body, body_hash, status, submitted_turn_id, source_invocation, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    outbox_id,
                    self.resident_id,
                    grant["id"],
                    draft_id,
                    grant["destination_kind"],
                    grant["destination_id"],
                    str(draft["body"]),
                    str(draft["body_hash"]),
                    turn,
                    source_invocation,
                    submitted_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO private_message_quota_reservations
                (id, resident_id, grant_id, outbox_id, state, created_at)
                VALUES (?, ?, ?, ?, 'reserved', ?)
                """,
                (reservation_id, self.resident_id, grant["id"], outbox_id, submitted_at),
            )
            connection.execute(
                "UPDATE private_message_drafts SET status='submitted', body='', resolved_at=? WHERE id=?",
                (submitted_at, draft_id),
            )
            receipt_id = self._event(
                connection,
                object_kind="outbox",
                object_id=outbox_id,
                event_type="queued",
                actor="resident",
                metadata={
                    "grant_id": grant["id"],
                    "terms_hash": grant["terms_hash"],
                    "draft_id": draft_id,
                    "draft_hash": str(draft["body_hash"]),
                    "destination_kind": grant["destination_kind"],
                    "destination_id": grant["destination_id"],
                    "reservation_id": reservation_id,
                    "reservation_state": "reserved",
                    "source_invocation": source_invocation,
                    "source_turn_id": turn,
                    "authorization_action": "message.submit",
                    "authorized_at": submitted_at,
                },
            )
            return {
                "id": outbox_id,
                "draft_id": draft_id,
                "grant_id": grant["id"],
                "terms_hash": grant["terms_hash"],
                "draft_hash": str(draft["body_hash"]),
                "destination_kind": grant["destination_kind"],
                "destination_id": grant["destination_id"],
                "status": "queued",
                "reservation_id": reservation_id,
                "reservation_state": "reserved",
                "receipt_id": receipt_id,
                "submitted_at": submitted_at,
            }

    @staticmethod
    def _outbox_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "draft_id": str(row["draft_id"]),
            "grant_id": str(row["grant_id"]),
            "draft_hash": str(row["body_hash"]),
            "destination_kind": str(row["destination_kind"]),
            "destination_id": str(row["destination_id"]),
            "status": str(row["status"]),
            "reservation_id": str(row["reservation_id"]),
            "submitted_at": str(row["submitted_at"]),
        }

    def _reservation(self, connection: sqlite3.Connection, outbox_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM private_message_quota_reservations WHERE outbox_id=? AND resident_id=?",
            (outbox_id, self.resident_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("outbox item has no quota reservation")
        return row

    def _resolve_queued(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        status: str,
        actor: str,
        event_type: str,
        reservation_state: str,
        safe_error_code: str | None = None,
    ) -> None:
        now = utc_now_iso()
        reservation = self._reservation(connection, str(row["id"]))
        connection.execute(
            "UPDATE private_message_outbox SET status=?, body=NULL, resolved_at=?, safe_error_code=? WHERE id=? AND resident_id=?",
            (status, now, safe_error_code, str(row["id"]), self.resident_id),
        )
        connection.execute(
            "UPDATE private_message_drafts SET status=?, body='', resolved_at=? WHERE id=? AND resident_id=?",
            (status, now, str(row["draft_id"]), self.resident_id),
        )
        connection.execute(
            "UPDATE private_message_quota_reservations SET state=?, resolved_at=? WHERE id=?",
            (reservation_state, now, str(reservation["id"])),
        )
        grant = self._grant_row(connection, str(row["grant_id"]))
        self._event(
            connection,
            object_kind="outbox",
            object_id=str(row["id"]),
            event_type=event_type,
            actor=actor,
            metadata={
                "grant_id": str(row["grant_id"]),
                "terms_hash": str(grant["terms_hash"]),
                "draft_id": str(row["draft_id"]),
                "draft_hash": str(row["body_hash"]),
                "destination_kind": str(row["destination_kind"]),
                "destination_id": str(row["destination_id"]),
                "reservation_id": str(reservation["id"]),
                "reservation_state": reservation_state,
                "safe_error_code": safe_error_code,
            },
        )

    def _cancel_queued_for_grant(
        self,
        connection: sqlite3.Connection,
        grant_id: str,
        *,
        actor: str,
        event_type: str,
    ) -> int:
        rows = connection.execute(
            "SELECT * FROM private_message_outbox WHERE resident_id=? AND grant_id=? AND status='queued'",
            (self.resident_id, grant_id),
        ).fetchall()
        for item in rows:
            self._resolve_queued(
                connection, item, status="cancelled", actor=actor,
                event_type=event_type, reservation_state="released",
            )
        return len(rows)

    def peek_next(self) -> dict[str, Any] | None:
        """Return queue metadata only; destination lookup happens before claim."""
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT id, grant_id, destination_kind, destination_id, submitted_at "
                "FROM private_message_outbox WHERE resident_id=? AND status='queued' "
                "ORDER BY submitted_at, id LIMIT 1",
                (self.resident_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def claim(self, outbox_id: str, now: str | None = None) -> dict[str, Any] | None:
        """Atomically validate and claim one queued message for its sole send attempt."""
        moment = _now(now)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM private_message_outbox WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown private message outbox item: {outbox_id}")
            if str(row["status"]) != "queued":
                return None
            grant_row = self._expire_grant_if_due(
                connection, self._grant_row(connection, str(row["grant_id"])), moment
            )
            grant = self._row_grant(grant_row)
            row = connection.execute(
                "SELECT * FROM private_message_outbox WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            ).fetchone()
            if row is None or str(row["status"]) != "queued":
                return None
            valid_window = (
                grant["state"] == "active"
                and moment >= datetime.fromisoformat(grant["active_from"])
                and moment < datetime.fromisoformat(grant["expires_at"])
            )
            reservation = self._reservation(connection, outbox_id)
            draft = connection.execute(
                "SELECT * FROM private_message_drafts WHERE id=? AND resident_id=?",
                (str(row["draft_id"]), self.resident_id),
            ).fetchone()
            body = row["body"]
            payload_valid = False
            if isinstance(body, str) and draft is not None:
                try:
                    digest = hashlib.sha256(body.encode("utf-8", errors="strict")).hexdigest()
                    _validate_body(body, int(grant["max_length"]))
                    payload_valid = (
                        digest == str(row["body_hash"]) == str(draft["body_hash"])
                        and str(row["destination_kind"]) == grant["destination_kind"]
                        and str(row["destination_id"]) == grant["destination_id"]
                    )
                except (UnicodeEncodeError, ValueError):
                    payload_valid = False
            if not valid_window or str(reservation["state"]) != "reserved":
                self._resolve_queued(
                    connection, row, status="cancelled", actor="runtime",
                    event_type="grant_not_usable_at_claim", reservation_state="released",
                )
                return None
            if not payload_valid:
                self._resolve_queued(
                    connection, row, status="failed", actor="runtime",
                    event_type="payload_validation_failed", reservation_state="released",
                    safe_error_code="payload_validation_failed",
                )
                return None
            attempted_at = moment.isoformat()
            connection.execute(
                "UPDATE private_message_outbox SET status='attempting', attempted_at=? WHERE id=? AND resident_id=? AND status='queued'",
                (attempted_at, outbox_id, self.resident_id),
            )
            self._event(
                connection,
                object_kind="outbox",
                object_id=outbox_id,
                event_type="attempting",
                actor="discord_worker",
                metadata={
                    "grant_id": grant["id"],
                    "terms_hash": grant["terms_hash"],
                    "draft_id": str(row["draft_id"]),
                    "draft_hash": str(row["body_hash"]),
                    "destination_kind": str(row["destination_kind"]),
                    "destination_id": str(row["destination_id"]),
                    "reservation_id": str(reservation["id"]),
                    "reservation_state": "reserved",
                },
            )
            return {
                "id": outbox_id,
                "grant_id": grant["id"],
                "destination_kind": str(row["destination_kind"]),
                "destination_id": str(row["destination_id"]),
                "body": body,
                "draft_hash": str(row["body_hash"]),
                "status": "attempting",
            }

    def cancel(self, outbox_id: str, actor: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM private_message_outbox WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown private message outbox item: {outbox_id}")
            status = str(row["status"])
            if status == "queued":
                self._resolve_queued(
                    connection, row, status="cancelled", actor=str(actor),
                    event_type="cancelled", reservation_state="released",
                )
                return {"id": outbox_id, "status": "cancelled"}
            if status == "attempting":
                return {"id": outbox_id, "status": "too_late_to_cancel"}
            return {"id": outbox_id, "status": status}

    def finish_delivery(
        self,
        outbox_id: str,
        outcome: str,
        *,
        discord_message_id: str | None = None,
        safe_error_code: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"delivered", "failed", "delivery_uncertain"}:
            raise ValueError("outcome must be delivered, failed, or delivery_uncertain")
        if outcome == "delivered" and not str(discord_message_id or "").strip():
            raise ValueError("confirmed delivery requires a Discord message ID")
        reservation_outcome = "consumed" if outcome != "failed" else "released"
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM private_message_outbox WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown private message outbox item: {outbox_id}")
            if str(row["status"]) != "attempting":
                return {"id": outbox_id, "status": str(row["status"])}
            reservation = self._reservation(connection, outbox_id)
            now = utc_now_iso()
            connection.execute(
                "UPDATE private_message_outbox SET status=?, body=NULL, resolved_at=?, discord_message_id=?, safe_error_code=? WHERE id=?",
                (outcome, now, str(discord_message_id) if discord_message_id else None,
                 safe_error_code, outbox_id),
            )
            connection.execute(
                "UPDATE private_message_drafts SET status=?, body='', resolved_at=? WHERE id=?",
                (outcome, now, str(row["draft_id"])),
            )
            connection.execute(
                "UPDATE private_message_quota_reservations SET state=?, resolved_at=? WHERE id=?",
                (reservation_outcome, now, str(reservation["id"])),
            )
            grant = self._grant_row(connection, str(row["grant_id"]))
            self._event(
                connection,
                object_kind="outbox",
                object_id=outbox_id,
                event_type=outcome,
                actor="discord_worker",
                metadata={
                    "grant_id": str(row["grant_id"]),
                    "terms_hash": str(grant["terms_hash"]),
                    "draft_id": str(row["draft_id"]),
                    "draft_hash": str(row["body_hash"]),
                    "destination_kind": str(row["destination_kind"]),
                    "destination_id": str(row["destination_id"]),
                    "reservation_id": str(reservation["id"]),
                    "reservation_state": reservation_outcome,
                    "discord_message_id": str(discord_message_id) if discord_message_id else None,
                    "safe_error_code": safe_error_code,
                    "automatic_retry": False,
                },
            )
            return {"id": outbox_id, "status": outcome}

    def recover_attempting(self) -> int:
        """Convert interrupted sends to uncertainty; never put them back in queue."""
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM private_message_outbox WHERE resident_id=? AND status='attempting'",
                (self.resident_id,),
            ).fetchall()
            for row in rows:
                reservation = self._reservation(connection, str(row["id"]))
                now = utc_now_iso()
                connection.execute(
                    "UPDATE private_message_outbox SET status='delivery_uncertain', safe_error_code='process_interrupted', resolved_at=? WHERE id=?",
                    (now, str(row["id"])),
                )
                connection.execute(
                    "UPDATE private_message_quota_reservations SET state='consumed', resolved_at=? WHERE id=?",
                    (now, str(reservation["id"])),
                )
                grant = self._grant_row(connection, str(row["grant_id"]))
                self._event(
                    connection,
                    object_kind="outbox",
                    object_id=str(row["id"]),
                    event_type="delivery_uncertain",
                    actor="runtime_recovery",
                    metadata={
                        "grant_id": str(row["grant_id"]),
                        "terms_hash": str(grant["terms_hash"]),
                        "draft_id": str(row["draft_id"]),
                        "draft_hash": str(row["body_hash"]),
                        "destination_kind": str(row["destination_kind"]),
                        "destination_id": str(row["destination_id"]),
                        "reservation_id": str(reservation["id"]),
                        "reservation_state": "consumed",
                        "safe_error_code": "process_interrupted",
                        "automatic_retry": False,
                    },
                )
            return len(rows)

    def inspect_outbox(self, outbox_id: str, *, include_body: bool = False) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT o.*, g.terms_hash, r.id AS reservation_id,
                       r.state AS reservation_state
                FROM private_message_outbox o
                JOIN private_message_grants g ON g.id=o.grant_id
                JOIN private_message_quota_reservations r ON r.outbox_id=o.id
                WHERE o.id=? AND o.resident_id=? AND g.resident_id=? AND r.resident_id=?
                """,
                (outbox_id, self.resident_id, self.resident_id, self.resident_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown private message outbox item: {outbox_id}")
            result = {
                "id": str(row["id"]),
                "grant_id": str(row["grant_id"]),
                "terms_hash": str(row["terms_hash"]),
                "draft_id": str(row["draft_id"]),
                "draft_hash": str(row["body_hash"]),
                "destination_kind": str(row["destination_kind"]),
                "destination_id": str(row["destination_id"]),
                "status": str(row["status"]),
                "reservation_id": str(row["reservation_id"]),
                "reservation_state": str(row["reservation_state"]),
                "submitted_turn_id": str(row["submitted_turn_id"]),
                "source_invocation": str(row["source_invocation"]),
                "submitted_at": str(row["submitted_at"]),
                "attempted_at": row["attempted_at"],
                "resolved_at": row["resolved_at"],
                "discord_message_id": row["discord_message_id"],
                "safe_error_code": row["safe_error_code"],
                "body_available": row["body"] is not None,
            }
            if include_body and row["body"] is not None:
                result["body"] = str(row["body"])
            event = connection.execute(
                """
                SELECT metadata_json, created_at FROM private_message_events
                WHERE resident_id=? AND object_kind='outbox' AND object_id=?
                  AND event_type='reconciled'
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (self.resident_id, outbox_id),
            ).fetchone()
            if event is not None:
                result["reconciliation"] = json.loads(str(event["metadata_json"]))
                result["reconciliation"]["recorded_at"] = str(event["created_at"])
            return result

    def reconcile(
        self,
        outbox_id: str,
        outcome: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized = str(outcome).strip().lower()
        if normalized not in {"delivered", "not-delivered"}:
            raise ValueError("reconciliation outcome must be delivered or not-delivered")
        note = str(reason).strip()
        if not note or len(note) > 500:
            raise ValueError("reconciliation reason must contain 1 to 500 characters")
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM private_message_outbox WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown private message outbox item: {outbox_id}")
            if str(row["status"]) != "delivery_uncertain":
                raise PermissionError("only delivery_uncertain items can be reconciled")
            existing = connection.execute(
                "SELECT 1 FROM private_message_events WHERE resident_id=? "
                "AND object_kind='outbox' AND object_id=? AND event_type='reconciled' LIMIT 1",
                (self.resident_id, outbox_id),
            ).fetchone()
            if existing is not None:
                raise PermissionError("delivery uncertainty has already been reconciled")
            now = utc_now_iso()
            grant = self._grant_row(connection, str(row["grant_id"]))
            reservation = self._reservation(connection, outbox_id)
            connection.execute(
                "UPDATE private_message_outbox SET body=NULL WHERE id=? AND resident_id=?",
                (outbox_id, self.resident_id),
            )
            connection.execute(
                "UPDATE private_message_drafts SET body='' WHERE id=? AND resident_id=?",
                (str(row["draft_id"]), self.resident_id),
            )
            receipt_id = self._event(
                connection,
                object_kind="outbox",
                object_id=outbox_id,
                event_type="reconciled",
                actor=str(actor),
                metadata={
                    "reconciliation_outcome": normalized,
                    "reason": note,
                    "grant_id": str(row["grant_id"]),
                    "terms_hash": str(grant["terms_hash"]),
                    "draft_id": str(row["draft_id"]),
                    "draft_hash": str(row["body_hash"]),
                    "destination_kind": str(row["destination_kind"]),
                    "destination_id": str(row["destination_id"]),
                    "reservation_id": str(reservation["id"]),
                    "reservation_state": str(reservation["state"]),
                    "original_status_unchanged": True,
                    "automatic_retry": False,
                },
            )
            return {
                "id": outbox_id,
                "status": str(row["status"]),
                "reconciliation": {
                    "outcome": normalized,
                    "receipt_id": receipt_id,
                    "recorded_at": now,
                },
            }

