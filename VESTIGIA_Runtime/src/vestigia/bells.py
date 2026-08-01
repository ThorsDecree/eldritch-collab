from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from .db import ContinuityDB
from .utils import new_id, stable_json, utc_now_iso


PURPOSES = {
    "reflection",
    "creative_play",
    "maintenance",
    "relationship_tending",
    "archive_review",
    "room_inspection",
    "look_around",
    "hello",
    "other",
}
STRENGTHS = {"gentle", "repeated", "urgent", "outward_confirmation"}
SCHEDULE_KINDS = {"once", "interval", "daily", "weekly"}
ACKNOWLEDGEMENTS = {"seen", "ignored", "deferred", "answered"}
CONTROL_PATTERN = re.compile(r"^\[\[BELL_CONTROL\s+(\{.*\})\]\]\s*$")
DRAFT_PATTERN = re.compile(r"^\[\[BELL_DRAFT\s+(\{.*\})\]\]\s*$")


BELL_SCHEMA = """
CREATE TABLE IF NOT EXISTS bells (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL,
    prompt TEXT NOT NULL,
    strength TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    quiet_start TEXT,
    quiet_end TEXT,
    no_response_required INTEGER NOT NULL DEFAULT 1,
    choose_nothing INTEGER NOT NULL DEFAULT 1,
    action_scope TEXT NOT NULL DEFAULT 'conversation_only',
    delivery_interface TEXT NOT NULL,
    delivery_target_json TEXT NOT NULL,
    status TEXT NOT NULL,
    next_fire_at TEXT,
    last_fired_at TEXT,
    expires_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bells_due
ON bells(resident_id, status, next_fire_at);

CREATE TABLE IF NOT EXISTS bell_events (
    id TEXT PRIMARY KEY,
    bell_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    prompt_snapshot TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (bell_id) REFERENCES bells(id)
);

CREATE INDEX IF NOT EXISTS idx_bell_events
ON bell_events(bell_id, created_at);

CREATE TABLE IF NOT EXISTS bell_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    delivery_interface TEXT NOT NULL,
    delivery_target_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


@dataclass(frozen=True)
class Bell:
    id: str
    resident_id: str
    room_id: str
    title: str
    purpose: str
    prompt: str
    strength: str
    schedule_kind: str
    schedule: dict[str, Any]
    timezone: str
    quiet_start: str | None
    quiet_end: str | None
    no_response_required: bool
    choose_nothing: bool
    action_scope: str
    delivery_interface: str
    delivery_target: dict[str, Any]
    status: str
    next_fire_at: str | None
    last_fired_at: str | None
    expires_at: str | None
    revision: int
    created_by: str
    created_at: str
    updated_at: str


def _aware(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clock(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Clock times must use HH:MM or HH:MM:SS") from exc
    return parsed.replace(tzinfo=None)


def in_quiet_hours(
    now: datetime,
    *,
    timezone: str,
    quiet_start: str | None,
    quiet_end: str | None,
) -> bool:
    if not quiet_start or not quiet_end or quiet_start == quiet_end:
        return False
    local = _aware(now).astimezone(ZoneInfo(timezone)).time().replace(tzinfo=None)
    start, end = _clock(quiet_start), _clock(quiet_end)
    if start < end:
        return start <= local < end
    return local >= start or local < end


def quiet_end_after(
    now: datetime,
    *,
    timezone: str,
    quiet_start: str | None,
    quiet_end: str | None,
) -> datetime:
    current = _aware(now)
    if not in_quiet_hours(
        current, timezone=timezone, quiet_start=quiet_start, quiet_end=quiet_end
    ):
        return current
    zone = ZoneInfo(timezone)
    local = current.astimezone(zone)
    end = _clock(str(quiet_end))
    candidate = datetime.combine(local.date(), end, tzinfo=zone)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def next_fire(
    *,
    kind: str,
    schedule: dict[str, Any],
    timezone: str,
    after: datetime,
) -> datetime | None:
    current = _aware(after)
    zone = ZoneInfo(timezone)
    if kind == "once":
        candidate = _aware(str(schedule["at"]))
        return candidate if candidate > current else None
    if kind == "interval":
        seconds = int(schedule["seconds"])
        if seconds < 3600:
            raise ValueError("Bell intervals must be at least 3600 seconds")
        anchor = _aware(str(schedule.get("anchor") or current.isoformat()))
        candidate = anchor
        while candidate <= current:
            candidate += timedelta(seconds=seconds)
        return candidate
    local = current.astimezone(zone)
    clock = _clock(str(schedule["time"]))
    if kind == "daily":
        candidate = datetime.combine(local.date(), clock, tzinfo=zone)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)
    if kind == "weekly":
        weekdays = sorted({int(item) for item in schedule["weekdays"]})
        if not weekdays or any(item < 0 or item > 6 for item in weekdays):
            raise ValueError("Weekly bells require weekdays from 0 (Monday) to 6 (Sunday)")
        for offset in range(0, 8):
            day = local.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            candidate = datetime.combine(day, clock, tzinfo=zone)
            if candidate > local:
                return candidate.astimezone(UTC)
    raise ValueError(f"Unsupported bell schedule: {kind}")


class BellService:
    def __init__(self, db: ContinuityDB, resident_id: str, room_id: str) -> None:
        self.db = db
        self.resident_id = resident_id
        self.room_id = room_id
        with self.db.connect() as connection:
            connection.executescript(BELL_SCHEMA)

    def create(
        self,
        *,
        title: str,
        purpose: str,
        prompt: str,
        schedule_kind: str,
        schedule: dict[str, Any],
        timezone: str,
        created_by: str,
        delivery_interface: str,
        delivery_target: dict[str, Any],
        strength: str = "gentle",
        quiet_start: str | None = None,
        quiet_end: str | None = None,
        no_response_required: bool = True,
        choose_nothing: bool = True,
        expires_at: str | None = None,
    ) -> Bell:
        title, prompt = title.strip(), prompt.strip()
        if not title or not prompt:
            raise ValueError("Bell title and prompt may not be empty")
        if purpose not in PURPOSES:
            raise ValueError(f"Unknown bell purpose: {purpose}")
        if strength not in STRENGTHS:
            raise ValueError(f"Unknown bell strength: {strength}")
        if schedule_kind not in SCHEDULE_KINDS:
            raise ValueError(f"Unknown schedule kind: {schedule_kind}")
        ZoneInfo(timezone)
        if quiet_start:
            _clock(quiet_start)
        if quiet_end:
            _clock(quiet_end)
        now = datetime.now(UTC)
        first = next_fire(
            kind=schedule_kind, schedule=schedule, timezone=timezone, after=now - timedelta(microseconds=1)
        )
        if first is None:
            raise ValueError("Bell schedule has no future firing")
        expiry = _aware(expires_at).isoformat() if expires_at else None
        if expiry and first >= _aware(expiry):
            raise ValueError("Bell expires before its first firing")
        bell_id = new_id("bell")
        stamp = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO bells (
                    id, resident_id, room_id, title, purpose, prompt, strength,
                    schedule_kind, schedule_json, timezone, quiet_start, quiet_end,
                    no_response_required, choose_nothing, action_scope,
                    delivery_interface, delivery_target_json, status, next_fire_at,
                    expires_at, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    bell_id, self.resident_id, self.room_id, title, purpose, prompt, strength,
                    schedule_kind, stable_json(schedule), timezone, quiet_start, quiet_end,
                    int(no_response_required), int(choose_nothing), "conversation_only",
                    delivery_interface, stable_json(delivery_target), first.isoformat(),
                    expiry, created_by, stamp, stamp,
                ),
            )
        self.event(bell_id, "created", "active", payload={"created_by": created_by})
        return self.get(bell_id)

    def draft_create(
        self,
        payload: dict[str, Any],
        *,
        delivery_interface: str,
        delivery_target: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and preserve a resident-authored candidate without activating it."""
        if not delivery_interface or not delivery_target:
            raise ValueError("bell creation requires an authenticated delivery doorway")
        allowed = {
            "title", "purpose", "prompt", "strength", "schedule_kind", "schedule",
            "timezone", "quiet_start", "quiet_end", "no_response_required",
            "choose_nothing", "expires_at", "reason",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported bell draft fields: {', '.join(sorted(unknown))}")
        required = {"title", "purpose", "prompt", "schedule_kind", "schedule"}
        missing = required - set(payload)
        if missing:
            raise ValueError(f"Missing bell draft fields: {', '.join(sorted(missing))}")
        candidate = dict(payload)
        candidate.setdefault("strength", "gentle")
        candidate.setdefault("timezone", "UTC")
        candidate.setdefault("quiet_start", None)
        candidate.setdefault("quiet_end", None)
        candidate.setdefault("no_response_required", True)
        candidate.setdefault("choose_nothing", True)
        if str(candidate["purpose"]) not in PURPOSES:
            raise ValueError(f"Unknown bell purpose: {candidate['purpose']}")
        if str(candidate["strength"]) not in STRENGTHS:
            raise ValueError(f"Unknown bell strength: {candidate['strength']}")
        if str(candidate["schedule_kind"]) not in SCHEDULE_KINDS:
            raise ValueError(f"Unknown schedule kind: {candidate['schedule_kind']}")
        ZoneInfo(str(candidate["timezone"]))
        if candidate["quiet_start"]:
            _clock(str(candidate["quiet_start"]))
        if candidate["quiet_end"]:
            _clock(str(candidate["quiet_end"]))
        first = next_fire(
            kind=str(candidate["schedule_kind"]),
            schedule=dict(candidate["schedule"]),
            timezone=str(candidate["timezone"]),
            after=datetime.now(UTC) - timedelta(microseconds=1),
        )
        if first is None:
            raise ValueError("Bell schedule has no future firing")
        if candidate.get("expires_at") and first >= _aware(str(candidate["expires_at"])):
            raise ValueError("Bell expires before its first firing")
        canonical = stable_json(candidate)
        payload_hash = __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
        draft_id = new_id("bell_draft")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO bell_drafts
                (id, resident_id, room_id, payload_json, payload_hash, delivery_interface,
                 delivery_target_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    draft_id, self.resident_id, self.room_id, canonical, payload_hash,
                    delivery_interface, stable_json(delivery_target), utc_now_iso(),
                ),
            )
        return {
            "draft_id": draft_id,
            "expected_hash": payload_hash,
            "payload": candidate,
            "delivery_interface": delivery_interface,
            "delivery_target": delivery_target,
            "next_fire_at": first.isoformat(),
        }

    def resolve_create_draft(
        self,
        draft_id: str,
        *,
        action: str,
        expected_hash: str,
        actor: str,
    ) -> Bell | None:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM bell_drafts WHERE id=? AND resident_id=?",
                (draft_id, self.resident_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown bell draft: {draft_id}")
        if str(row["status"]) != "pending":
            raise ValueError("Bell draft is no longer pending")
        if str(row["payload_hash"]) != expected_hash:
            raise ValueError("Bell draft hash mismatch; review the current preview")
        normalized = action.strip().lower()
        if normalized not in {"claim", "reject"}:
            raise ValueError("Bell draft action must be claim or reject")
        if normalized == "reject":
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE bell_drafts SET status='rejected', resolved_at=? WHERE id=?",
                    (utc_now_iso(), draft_id),
                )
            return None
        payload = json.loads(str(row["payload_json"]))
        payload.pop("reason", None)
        bell = self.create(
            **payload,
            created_by=actor,
            delivery_interface=str(row["delivery_interface"]),
            delivery_target=json.loads(str(row["delivery_target_json"])),
        )
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE bell_drafts SET status='claimed', resolved_at=? WHERE id=?",
                (utc_now_iso(), draft_id),
            )
        self.event(
            bell.id, "resident_draft_claimed", "active",
            payload={"actor": actor, "draft_id": draft_id, "payload_hash": expected_hash},
        )
        return bell

    def get(self, bell_id: str) -> Bell:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM bells WHERE id=?", (bell_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown bell: {bell_id}")
        return self._row(row)

    def list(self, *, include_deleted: bool = False, limit: int = 100) -> list[Bell]:
        clause = "" if include_deleted else "AND status != 'deleted'"
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM bells
                WHERE resident_id=? {clause}
                ORDER BY CASE WHEN next_fire_at IS NULL THEN 1 ELSE 0 END, next_fire_at, rowid
                LIMIT ?
                """,
                (self.resident_id, max(1, int(limit))),
            ).fetchall()
        return [self._row(row) for row in rows]

    def due(self, now: datetime | None = None, limit: int = 20) -> list[Bell]:
        stamp = _aware(now or datetime.now(UTC)).isoformat()
        self.expire_stale(_aware(stamp))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM bells
                WHERE resident_id=? AND status='active'
                  AND next_fire_at IS NOT NULL AND next_fire_at <= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY next_fire_at LIMIT ?
                """,
                (self.resident_id, stamp, stamp, max(1, int(limit))),
            ).fetchall()
        return [self._row(row) for row in rows]

    def expire_stale(self, now: datetime | None = None) -> list[str]:
        stamp = _aware(now or datetime.now(UTC)).isoformat()
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM bells
                WHERE resident_id=? AND status='active'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (self.resident_id, stamp),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE bells SET status='expired', next_fire_at=NULL, updated_at=?
                    WHERE id IN ({placeholders})
                    """,
                    [utc_now_iso(), *ids],
                )
        for bell_id in ids:
            self.event(bell_id, "expired", "expired", payload={"at": stamp})
        return ids

    def revise(self, bell_id: str, *, actor: str, **changes: Any) -> Bell:
        allowed = {
            "title", "purpose", "prompt", "strength", "quiet_start", "quiet_end",
            "no_response_required", "choose_nothing", "expires_at",
            "schedule_kind", "schedule", "timezone",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported bell revisions: {', '.join(sorted(unknown))}")
        bell = self.get(bell_id)
        values = asdict(bell)
        values.update(changes)
        if values["purpose"] not in PURPOSES or values["strength"] not in STRENGTHS:
            raise ValueError("Invalid purpose or strength")
        if values["schedule_kind"] not in SCHEDULE_KINDS:
            raise ValueError("Invalid schedule kind")
        ZoneInfo(str(values["timezone"]))
        if not str(values["prompt"]).strip() or not str(values["title"]).strip():
            raise ValueError("Bell title and prompt may not be empty")
        assignments: list[str] = []
        parameters: list[Any] = []
        for key, value in changes.items():
            if key in {"no_response_required", "choose_nothing"}:
                value = int(bool(value))
            if key == "schedule":
                value = stable_json(value)
                key = "schedule_json"
            assignments.append(f"{key}=?")
            parameters.append(value)
        if {"schedule_kind", "schedule", "timezone"} & set(changes):
            recalculated = next_fire(
                kind=str(values["schedule_kind"]),
                schedule=dict(values["schedule"]),
                timezone=str(values["timezone"]),
                after=datetime.now(UTC),
            )
            assignments.extend(["next_fire_at=?", "status='active'"])
            parameters.append(recalculated.isoformat() if recalculated else None)
        assignments.extend(["revision=revision+1", "updated_at=?"])
        parameters.extend([utc_now_iso(), bell_id])
        with self.db.connect() as connection:
            connection.execute(
                f"UPDATE bells SET {', '.join(assignments)} WHERE id=?", parameters
            )
        self.event(
            bell_id, "revised", self.get(bell_id).status,
            prompt_snapshot=str(changes.get("prompt", bell.prompt)),
            payload={"actor": actor, "fields": sorted(changes)},
        )
        return self.get(bell_id)

    def set_status(self, bell_id: str, status: str, *, actor: str, reason: str = "") -> Bell:
        if status not in {"active", "paused", "deleted", "completed", "expired"}:
            raise ValueError(f"Invalid bell status: {status}")
        bell = self.get(bell_id)
        next_at = bell.next_fire_at
        if status == "active" and not next_at:
            next_value = next_fire(
                kind=bell.schedule_kind,
                schedule=bell.schedule,
                timezone=bell.timezone,
                after=datetime.now(UTC),
            )
            next_at = next_value.isoformat() if next_value else None
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE bells SET status=?, next_fire_at=?, updated_at=? WHERE id=?",
                (status, next_at, utc_now_iso(), bell_id),
            )
        self.event(bell_id, status, status, payload={"actor": actor, "reason": reason})
        return self.get(bell_id)

    def defer(self, bell_id: str, until: datetime, *, reason: str) -> Bell:
        stamp = _aware(until).isoformat()
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE bells SET next_fire_at=?, updated_at=? WHERE id=?",
                (stamp, utc_now_iso(), bell_id),
            )
        self.event(bell_id, "deferred", "active", payload={"until": stamp, "reason": reason})
        return self.get(bell_id)

    def mark_fired(self, bell_id: str, *, fired_at: datetime | None = None) -> Bell:
        bell = self.get(bell_id)
        when = _aware(fired_at or datetime.now(UTC))
        following = next_fire(
            kind=bell.schedule_kind,
            schedule=bell.schedule,
            timezone=bell.timezone,
            after=when,
        )
        status = "active"
        if bell.schedule_kind == "once" or following is None:
            status = "completed"
            following = None
        if bell.expires_at and following and following >= _aware(bell.expires_at):
            status, following = "expired", None
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE bells SET last_fired_at=?, next_fire_at=?, status=?, updated_at=?
                WHERE id=?
                """,
                (
                    when.isoformat(),
                    following.isoformat() if following else None,
                    status,
                    utc_now_iso(),
                    bell_id,
                ),
            )
        self.event(
            bell_id, "fired", status, prompt_snapshot=bell.prompt,
            payload={"scheduled_for": bell.next_fire_at},
        )
        return self.get(bell_id)

    def event(
        self,
        bell_id: str,
        event_type: str,
        status: str,
        *,
        prompt_snapshot: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = new_id("bev")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO bell_events
                (id, bell_id, event_type, status, prompt_snapshot, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, bell_id, event_type, status, prompt_snapshot,
                    stable_json(payload or {}), utc_now_iso(),
                ),
            )
        return event_id

    def events(self, bell_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self.get(bell_id)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM bell_events WHERE bell_id=?
                ORDER BY rowid DESC LIMIT ?
                """,
                (bell_id, max(1, int(limit))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def acknowledge(self, bell_id: str, state: str, *, actor: str, note: str = "") -> str:
        if state not in ACKNOWLEDGEMENTS:
            raise ValueError(f"Unknown acknowledgement: {state}")
        return self.event(
            bell_id, "acknowledged", state, payload={"actor": actor, "note": note}
        )

    def invitation_text(self, bell: Bell) -> str:
        options = (
            "\nYou may tend one thing, question one thing, change one thing, "
            "or leave everything alone."
            if bell.choose_nothing else ""
        )
        return f"""[VESTIGIA BELL — invitation, not instruction]
Bell: {bell.title}
Bell ID: {bell.id}
Purpose: {bell.purpose}
Strength: {bell.strength}
No response required: {"yes" if bell.no_response_required else "no"}

{bell.prompt}{options}

This bell authorizes one conversational response through its configured doorway. It does not
authorize posting elsewhere, messaging another person, spending resources, changing a
relationship, or altering public state. Draft or discuss any such action, but require explicit
confirmation before performing it. Silence is not failure or consent and will not make this
bell escalate. The bell fired before this turn; that ordering does not prove it caused anything
that follows.

You may edit the scheduler itself. To do so, place one single-line control at the end:
[[BELL_CONTROL {{"bell_id":"{bell.id}","action":"pause"}}]]
Actions: pause, delete, defer (with "minutes"), or revise (with any of "prompt", "title",
"purpose", "strength", "schedule_kind", "schedule", "timezone"). The control changes only the
bell registry and is recorded."""

    @staticmethod
    def _row(row: Any) -> Bell:
        return Bell(
            id=str(row["id"]),
            resident_id=str(row["resident_id"]),
            room_id=str(row["room_id"]),
            title=str(row["title"]),
            purpose=str(row["purpose"]),
            prompt=str(row["prompt"]),
            strength=str(row["strength"]),
            schedule_kind=str(row["schedule_kind"]),
            schedule=json.loads(row["schedule_json"]),
            timezone=str(row["timezone"]),
            quiet_start=str(row["quiet_start"]) if row["quiet_start"] else None,
            quiet_end=str(row["quiet_end"]) if row["quiet_end"] else None,
            no_response_required=bool(row["no_response_required"]),
            choose_nothing=bool(row["choose_nothing"]),
            action_scope=str(row["action_scope"]),
            delivery_interface=str(row["delivery_interface"]),
            delivery_target=json.loads(row["delivery_target_json"]),
            status=str(row["status"]),
            next_fire_at=str(row["next_fire_at"]) if row["next_fire_at"] else None,
            last_fired_at=str(row["last_fired_at"]) if row["last_fired_at"] else None,
            expires_at=str(row["expires_at"]) if row["expires_at"] else None,
            revision=int(row["revision"]),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


def apply_resident_controls(
    text: str,
    service: BellService,
    *,
    actor: str,
    delivery_interface: str | None = None,
    delivery_target: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Apply explicit scheduler-only controls emitted by the resident."""
    kept: list[str] = []
    results: list[str] = []
    for line in text.splitlines():
        draft_match = DRAFT_PATTERN.match(line.strip())
        match = CONTROL_PATTERN.match(line.strip())
        if not draft_match and not match:
            kept.append(line)
            continue
        try:
            if draft_match:
                if not delivery_interface or not delivery_target:
                    raise ValueError("bell creation requires an authenticated delivery doorway")
                draft = service.draft_create(
                    json.loads(draft_match.group(1)),
                    delivery_interface=delivery_interface,
                    delivery_target=delivery_target,
                )
                results.append(
                    "bell_draft:pending:"
                    f"{draft['draft_id']}:{draft['expected_hash']}:"
                    f"next={draft['next_fire_at']}"
                )
                continue
            control = json.loads(match.group(1))
            if control.get("action") in {"claim", "reject"} and control.get("draft_id"):
                resolved = service.resolve_create_draft(
                    str(control["draft_id"]),
                    action=str(control["action"]),
                    expected_hash=str(control.get("expected_hash", "")),
                    actor=actor,
                )
                results.append(
                    f"{control['draft_id']}:{control['action']}:"
                    + (f"created:{resolved.id}" if resolved else "applied")
                )
                continue
            bell_id = str(control["bell_id"])
            action = str(control["action"])
            if action == "pause":
                service.set_status(bell_id, "paused", actor=actor, reason="resident control")
            elif action == "delete":
                service.set_status(bell_id, "deleted", actor=actor, reason="resident control")
            elif action == "defer":
                minutes = int(control["minutes"])
                if minutes < 1 or minutes > 525600:
                    raise ValueError("defer minutes out of range")
                service.defer(
                    bell_id,
                    datetime.now(UTC) + timedelta(minutes=minutes),
                    reason="resident control",
                )
            elif action == "revise":
                allowed = {
                    "prompt", "title", "purpose", "strength",
                    "schedule_kind", "schedule", "timezone",
                }
                changes = {key: control[key] for key in allowed if key in control}
                if not changes:
                    raise ValueError("revise control has no fields")
                service.revise(bell_id, actor=actor, **changes)
            else:
                raise ValueError(f"unsupported action: {action}")
            results.append(f"{bell_id}:{action}:applied")
        except Exception as exc:
            results.append(f"control:rejected:{exc}")
    return "\n".join(kept).rstrip(), results
