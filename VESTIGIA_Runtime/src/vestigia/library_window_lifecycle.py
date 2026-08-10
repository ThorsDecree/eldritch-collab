from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .utils import new_id, sha256_text, stable_json, utc_now_iso


CONFIRMATION_SCHEMA = "vestigia.library-lifecycle-confirmation.v0.1"
CONFIRMATION_TTL_MINUTES = 10
_INTERACTIVE_INTERFACES = {"cli", "discord"}


class LifecycleConfirmationRequired(PermissionError):
    """Fail-closed signal that asks a participant to confirm a destructive action."""

    def __init__(self, prompt: str, *, suggested_retry: dict[str, Any]) -> None:
        super().__init__(prompt)
        self.house_error_code = "confirmation_required"
        self.house_suggested_retry = suggested_retry


def ensure_confirmation_schema(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_action_confirmations (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_fingerprint TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_turn_id TEXT NOT NULL,
                resolved_turn_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_action_confirmations_pending
            ON library_action_confirmations(
                resident_id, action, target_id, status, created_at
            );
            """
        )


def _interactive_participant_context(context: dict[str, Any]) -> bool:
    return bool(context.get("turn_id")) and str(
        context.get("interface") or ""
    ).lower() in _INTERACTIVE_INTERFACES


def _participant_turn_text(house: Any, context: dict[str, Any]) -> str:
    turn_id = str(context.get("turn_id") or "").strip()
    if not turn_id:
        raise PermissionError("lifecycle confirmation requires a current participant turn")
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT content FROM turns WHERE id=? AND resident_id=?",
            (turn_id, house.resident_id),
        ).fetchone()
    if row is None:
        raise PermissionError(
            "lifecycle confirmation could not verify the current participant turn"
        )
    return str(row["content"])


def _reply(text: str) -> str | None:
    normalized = re.sub(r"[^a-z]", "", text.strip().casefold())
    if normalized in {"y", "yes", "confirm", "confirmed"}:
        return "yes"
    if normalized in {"n", "no", "cancel", "cancelled", "canceled"}:
        return "no"
    return None


def _fingerprint(target_id: str) -> str:
    return "sha256:" + hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:12]


def _payload_hash(action: str, target_id: str) -> str:
    return "sha256:" + sha256_text(
        stable_json({"action": action, "target_id": target_id})
    )


def _expiry() -> str:
    return (
        datetime.now(UTC) + timedelta(minutes=CONFIRMATION_TTL_MINUTES)
    ).isoformat().replace("+00:00", "Z")


def _confirm(
    house: Any,
    *,
    action: str,
    target_id: str,
    context: dict[str, Any],
    verb: str,
) -> None:
    ensure_confirmation_schema(house)
    turn_id = str(context.get("turn_id") or "").strip()
    participant_text = _participant_turn_text(house, context)
    answer = _reply(participant_text)
    payload_hash = _payload_hash(action, target_id)
    fingerprint = _fingerprint(target_id)
    now = utc_now_iso()

    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE library_action_confirmations SET status='expired' "
            "WHERE resident_id=? AND status='pending' AND expires_at<?",
            (house.resident_id, now),
        )
        pending = connection.execute(
            """
            SELECT id FROM library_action_confirmations
            WHERE resident_id=? AND action=? AND target_id=? AND payload_hash=?
              AND status='pending' AND expires_at>=?
            ORDER BY rowid DESC LIMIT 1
            """,
            (house.resident_id, action, target_id, payload_hash, now),
        ).fetchone()

        if answer == "yes":
            if pending is None:
                raise PermissionError(
                    f"No pending confirmation matches {fingerprint}; "
                    "request the lifecycle action again first"
                )
            connection.execute(
                "UPDATE library_action_confirmations "
                "SET status='confirmed', resolved_turn_id=? WHERE id=?",
                (turn_id, str(pending["id"])),
            )
            return

        if answer == "no":
            if pending is not None:
                connection.execute(
                    "UPDATE library_action_confirmations "
                    "SET status='cancelled', resolved_turn_id=? WHERE id=?",
                    (turn_id, str(pending["id"])),
                )
            raise PermissionError(
                f"{verb} cancelled for {fingerprint}; no lifecycle mutation occurred"
            )

        if pending is None:
            connection.execute(
                """
                INSERT INTO library_action_confirmations
                (id, resident_id, action, target_id, target_fingerprint, payload_hash,
                 status, requested_turn_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    new_id("library_confirm"),
                    house.resident_id,
                    action,
                    target_id,
                    fingerprint,
                    payload_hash,
                    turn_id,
                    now,
                    _expiry(),
                ),
            )

    raise LifecycleConfirmationRequired(
        f"{verb} {fingerprint}? Y/N",
        suggested_retry={
            "schema_version": CONFIRMATION_SCHEMA,
            "action": action,
            "target_fingerprint": fingerprint,
            "reply": "Y or N in a fresh participant turn",
            "expires_in_minutes": CONFIRMATION_TTL_MINUTES,
        },
    )


def authorize_notebook_lifecycle(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> None:
    # Retain and source detachment are explicit structured resident operations; they
    # never derive authority from natural-language keyword matching. Only destructive
    # notebook discard requires a human confirmation on interactive participant turns.
    if str(payload.get("mode") or "list").strip().lower() != "discard":
        return
    if not _interactive_participant_context(context):
        return
    notebook_id = str(payload.get("notebook_id") or "").strip()
    if not notebook_id:
        raise ValueError("notebook_id is required for discard confirmation")
    _confirm(
        house,
        action="research.notebook:discard",
        target_id=notebook_id,
        context=context,
        verb="Delete notebook",
    )


def authorize_source_management(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if not _interactive_participant_context(context):
        return
    source_id = str(payload.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("source_id is required for revocation confirmation")
    _confirm(
        house,
        action="source.manage:revoke",
        target_id=source_id,
        context=context,
        verb="Revoke source retrieval",
    )


def register_composition() -> None:
    """Install the lifecycle policy overlay before HousePort capability construction."""

    from . import library_window

    library_window.authorize_notebook_lifecycle = authorize_notebook_lifecycle
    library_window.authorize_source_management = authorize_source_management
