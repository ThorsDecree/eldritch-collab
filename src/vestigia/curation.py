from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .config import ResolvedConfig
from .db import ContinuityDB
from .models import AuthorityState, MemoryStatus, MemoryType, ResidencyTier
from .utils import TokenCounter, atomic_write_json, new_id, sha256_text, stable_json, utc_now_iso


CURATION_DRAFT_PATTERN = re.compile(r"^\[\[CURATION_DRAFT\s+(\{.*\})\]\]\s*$")
CURATION_CONTROL_PATTERN = re.compile(r"^\[\[CURATION_CONTROL\s+(\{.*\})\]\]\s*$")
CURATION_SURFACE_PATTERN = re.compile(r"^\[\[CURATION_SURFACE\s+(\{.*\})\]\]\s*$")

CURATION_ACTIONS = {
    "claim",
    "revise",
    "propose",
    "reject",
    "dispute",
    "defer",
    "release",
}
SURFACE_MODES = {"discard", "resident_note", "next_natural_turn", "surface_now"}


CURATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS curation_state (
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    eligible_exchanges INTEGER NOT NULL DEFAULT 0,
    cadence INTEGER NOT NULL DEFAULT 3,
    paused INTEGER NOT NULL DEFAULT 0,
    last_considered_turn_rowid INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (resident_id, room_id)
);

CREATE TABLE IF NOT EXISTS curation_batches (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    turn_ids_json TEXT NOT NULL DEFAULT '[]',
    memory_ids_json TEXT NOT NULL DEFAULT '[]',
    queue_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS curation_queue (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_id TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS curation_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (batch_id) REFERENCES curation_batches(id)
);

CREATE TABLE IF NOT EXISTS curation_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    batch_id TEXT,
    draft_id TEXT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curation_reflections (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    batch_id TEXT,
    mode TEXT NOT NULL,
    content TEXT,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS resident_jobs (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(resident_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_curation_queue
ON curation_queue(resident_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_curation_drafts
ON curation_drafts(resident_id, status, created_at);
"""


class Curator:
    """Consent-first curation room plus the original deterministic audit."""

    def __init__(self, config: ResolvedConfig, db: ContinuityDB) -> None:
        self.config = config
        self.db = db
        self.resident_id = str(config.get("resident.id"))
        self.room_id = str(config.get("room.id"))
        self.counter = TokenCounter(str(config.get("models.default")))
        cadence = max(1, int(config.get("curation.cadence_exchanges", 3)))
        with self.db.connect() as connection:
            connection.executescript(CURATION_SCHEMA)
            connection.execute(
                """
                INSERT INTO curation_state
                (resident_id, room_id, eligible_exchanges, cadence, paused,
                 last_considered_turn_rowid, updated_at)
                VALUES (?, ?, 0, ?, 0, 0, ?)
                ON CONFLICT(resident_id, room_id) DO NOTHING
                """,
                (self.resident_id, self.room_id, cadence, utc_now_iso()),
            )
            if config.sources.get("curation.cadence_exchanges") != "built-in":
                connection.execute(
                    """
                    UPDATE curation_state SET cadence=?, updated_at=?
                    WHERE resident_id=? AND room_id=?
                    """,
                    (cadence, utc_now_iso(), self.resident_id, self.room_id),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO resident_jobs
                (id, resident_id, kind, status, config_json, updated_at)
                VALUES (?, ?, 'curation', 'active', ?, ?)
                """,
                (
                    new_id("job"),
                    self.resident_id,
                    stable_json({"cadence_exchanges": cadence, "silence_escalation": False}),
                    utc_now_iso(),
                ),
            )

    # ---------- deterministic audit retained from v0.1 ----------

    def dry_run(self) -> dict[str, Any]:
        records = self.db.list_memories(
            resident_id=self.resident_id,
            room_id=self.room_id,
            limit=100000,
        )
        by_hash: dict[str, list] = defaultdict(list)
        for record in records:
            by_hash[record.content_hash].append(record)
        duplicate_groups = []
        for content_hash, group in by_hash.items():
            if len(group) < 2:
                continue
            independent = sorted(
                {
                    item.independent_source_key
                    for item in group
                    if item.independent_source_key
                }
            )
            duplicate_groups.append(
                {
                    "content_hash": content_hash,
                    "record_ids": [item.id for item in group],
                    "independent_source_count": len(independent),
                    "eligible_as_recurrence": len(independent) > 1,
                }
            )
        now = datetime.now(UTC).isoformat()
        verification_due = [
            item.id
            for item in records
            if item.verification_due_at and item.verification_due_at <= now
        ]
        core = [
            item
            for item in records
            if item.tier == "core"
            and item.status in {MemoryStatus.ACCEPTED.value, MemoryStatus.INHERITED_UNREVIEWED.value}
        ]
        core_tokens = sum(self.counter.count(item.content) for item in core)
        hard_limit = int(self.config.get("memory.core_hard_limit_tokens", 2000))
        report = {
            "schema_version": "vestigia.curation-report.v0.3",
            "created_at": utc_now_iso(),
            "mode": "dry-run",
            "resident_id": self.resident_id,
            "counts": {
                "records": len(records),
                "candidate": sum(item.status == MemoryStatus.CANDIDATE.value for item in records),
                "inherited_unreviewed": sum(
                    item.status == MemoryStatus.INHERITED_UNREVIEWED.value for item in records
                ),
                "rejected": sum(item.status == MemoryStatus.REJECTED.value for item in records),
            },
            "core": {
                "tokens": core_tokens,
                "hard_limit": hard_limit,
                "over_limit": core_tokens > hard_limit,
                "record_ids": [item.id for item in core],
            },
            "duplicate_groups": duplicate_groups,
            "verification_due_record_ids": verification_due,
            "recommendations": [],
            "mutations_performed": 0,
        }
        if core_tokens > hard_limit:
            report["recommendations"].append(
                "Core exceeds its hard limit; review before any further promotion."
            )
        if duplicate_groups:
            report["recommendations"].append(
                "Review duplicate groups. Derived copies from one lineage are not independent recurrence."
            )
        if verification_due:
            report["recommendations"].append(
                "Revalidate external claims whose verification date has passed."
            )
        output = self.config.home_path / "traces" / "curation-latest.json"
        atomic_write_json(output, report)
        report["path"] = str(output)
        return report

    # ---------- queue and cadence ----------

    def queue(self, item: dict[str, Any]) -> str:
        kind = str(item.get("kind", "other")).strip() or "other"
        content = str(item.get("content", "")).strip()
        if not content:
            raise ValueError("curation queue item may not be empty")
        queue_id = new_id("curation_queue")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO curation_queue
                (id, resident_id, room_id, kind, source_id, content, content_hash,
                 provenance_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    queue_id,
                    self.resident_id,
                    self.room_id,
                    kind,
                    str(item.get("source_id")) if item.get("source_id") else None,
                    content,
                    sha256_text(content),
                    stable_json(item.get("provenance") or {}),
                    utc_now_iso(),
                ),
            )
        return queue_id

    def eligible_exchange(self, turn_id: str, *, interface: str) -> dict[str, Any] | None:
        if not bool(self.config.get("curation.enabled", True)):
            return None
        if interface not in {"discord", "cli"}:
            return None
        with self.db.connect() as connection:
            job = connection.execute(
                """
                SELECT status FROM resident_jobs
                WHERE resident_id=? AND kind='curation'
                """,
                (self.resident_id,),
            ).fetchone()
            state = connection.execute(
                """
                SELECT * FROM curation_state
                WHERE resident_id=? AND room_id=?
                """,
                (self.resident_id, self.room_id),
            ).fetchone()
            if not state or bool(state["paused"]) or not job or str(job["status"]) != "active":
                return None
            count = int(state["eligible_exchanges"]) + 1
            cadence = int(state["cadence"])
            connection.execute(
                """
                UPDATE curation_state SET eligible_exchanges=?, updated_at=?
                WHERE resident_id=? AND room_id=?
                """,
                (count, utc_now_iso(), self.resident_id, self.room_id),
            )
        if count < cadence and not self._pressure_triggered():
            return None
        return self.create_batch(trigger_reason="cadence" if count >= cadence else "queue_pressure")

    def _pressure_triggered(self) -> bool:
        threshold = max(1, int(self.config.get("curation.queue_pressure", 8)))
        with self.db.connect() as connection:
            queued = connection.execute(
                """
                SELECT COUNT(*) AS n FROM curation_queue
                WHERE resident_id=? AND room_id=? AND status='pending'
                """,
                (self.resident_id, self.room_id),
            ).fetchone()
        return int(queued["n"]) >= threshold

    def create_batch(self, *, trigger_reason: str = "explicit") -> dict[str, Any] | None:
        maximum_items = max(1, int(self.config.get("curation.batch_max_items", 8)))
        with self.db.connect() as connection:
            state = connection.execute(
                """
                SELECT * FROM curation_state
                WHERE resident_id=? AND room_id=?
                """,
                (self.resident_id, self.room_id),
            ).fetchone()
            last_rowid = int(state["last_considered_turn_rowid"]) if state else 0
            turns = connection.execute(
                """
                SELECT rowid AS _rowid, * FROM turns
                WHERE resident_id=? AND room_id=? AND rowid>? AND interface!='bell'
                ORDER BY rowid LIMIT 24
                """,
                (self.resident_id, self.room_id, last_rowid),
            ).fetchall()
            queue_rows = connection.execute(
                """
                SELECT * FROM curation_queue
                WHERE resident_id=? AND room_id=? AND status='pending'
                ORDER BY rowid LIMIT ?
                """,
                (self.resident_id, self.room_id, maximum_items),
            ).fetchall()
        memories = self.db.list_memories(
            resident_id=self.resident_id,
            room_id=self.room_id,
            statuses=[
                MemoryStatus.CANDIDATE.value,
                MemoryStatus.INHERITED_UNREVIEWED.value,
                MemoryStatus.DEFERRED.value,
                MemoryStatus.DISPUTED.value,
            ],
            limit=maximum_items,
        )
        if (
            not turns
            and not queue_rows
            and not memories
            and not self.pending_drafts()
            and trigger_reason in {"cadence", "queue_pressure"}
        ):
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE curation_state SET eligible_exchanges=0, updated_at=?
                    WHERE resident_id=? AND room_id=?
                    """,
                    (utc_now_iso(), self.resident_id, self.room_id),
                )
            return None
        batch_id = new_id("curation_batch")
        turn_ids = [str(row["id"]) for row in turns]
        memory_ids = [item.id for item in memories]
        queue_ids = [str(row["id"]) for row in queue_rows]
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO curation_batches
                (id, resident_id, room_id, trigger_reason, turn_ids_json,
                 memory_ids_json, queue_ids_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'considering', ?)
                """,
                (
                    batch_id,
                    self.resident_id,
                    self.room_id,
                    trigger_reason,
                    stable_json(turn_ids),
                    stable_json(memory_ids),
                    stable_json(queue_ids),
                    now,
                ),
            )
            maximum_rowid = max([int(row["_rowid"]) for row in turns], default=last_rowid)
            connection.execute(
                """
                UPDATE curation_state
                SET eligible_exchanges=0, last_considered_turn_rowid=?, updated_at=?
                WHERE resident_id=? AND room_id=?
                """,
                (maximum_rowid, now, self.resident_id, self.room_id),
            )
            connection.execute(
                """
                INSERT INTO curation_events
                (id, resident_id, batch_id, event_type, status, payload_json, created_at)
                VALUES (?, ?, ?, 'batch_created', 'considering', ?, ?)
                """,
                (
                    new_id("curation_event"),
                    self.resident_id,
                    batch_id,
                    stable_json(
                        {
                            "trigger": trigger_reason,
                            "turn_count": len(turn_ids),
                            "memory_count": len(memory_ids),
                            "queue_count": len(queue_ids),
                        }
                    ),
                    now,
                ),
            )
        try:
            return self.packet(batch_id)
        except Exception as exc:
            self.fail_batch(batch_id, exc)
            raise

    def fail_batch(self, batch_id: str, error: Exception) -> None:
        """Make a failed private pass retryable without losing transcript coverage."""
        now = utc_now_iso()
        with self.db.connect() as connection:
            batch = connection.execute(
                """
                SELECT * FROM curation_batches
                WHERE id=? AND resident_id=? AND room_id=?
                """,
                (batch_id, self.resident_id, self.room_id),
            ).fetchone()
            if not batch or str(batch["status"]) in {"claimed", "failed_retryable"}:
                return
            turn_ids = json.loads(str(batch["turn_ids_json"]))
            rowids = []
            for turn_id in turn_ids:
                turn = connection.execute(
                    "SELECT rowid AS _rowid FROM turns WHERE id=?", (turn_id,)
                ).fetchone()
                if turn:
                    rowids.append(int(turn["_rowid"]))
            state = connection.execute(
                """
                SELECT last_considered_turn_rowid FROM curation_state
                WHERE resident_id=? AND room_id=?
                """,
                (self.resident_id, self.room_id),
            ).fetchone()
            rewind_to = (
                min(rowids) - 1
                if rowids
                else int(state["last_considered_turn_rowid"]) if state else 0
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE curation_batches
                SET status='failed_retryable', resolved_at=? WHERE id=?
                """,
                (now, batch_id),
            )
            connection.execute(
                """
                UPDATE curation_state
                SET eligible_exchanges=0,
                    last_considered_turn_rowid=MIN(last_considered_turn_rowid, ?),
                    updated_at=?
                WHERE resident_id=? AND room_id=?
                """,
                (rewind_to, now, self.resident_id, self.room_id),
            )
            self._insert_event(
                connection,
                batch_id=batch_id,
                draft_id=None,
                event_type="private_pass_failed",
                status="failed_retryable",
                payload={
                    "error_type": type(error).__name__,
                    "error_hash": sha256_text(str(error)),
                    "transcript_coverage_rewound": bool(rowids),
                },
                now=now,
            )

    def packet(self, batch_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            batch = connection.execute(
                """
                SELECT * FROM curation_batches
                WHERE id=? AND resident_id=?
                """,
                (batch_id, self.resident_id),
            ).fetchone()
        if not batch:
            raise KeyError("unknown curation batch")
        turn_ids = json.loads(str(batch["turn_ids_json"]))
        memory_ids = json.loads(str(batch["memory_ids_json"]))
        queue_ids = json.loads(str(batch["queue_ids_json"]))
        turns = [self.db.get_turn(turn_id) for turn_id in turn_ids]
        memories = [self.db.get_memory(memory_id) for memory_id in memory_ids]
        with self.db.connect() as connection:
            queue_rows = [
                connection.execute(
                    "SELECT * FROM curation_queue WHERE id=?", (queue_id,)
                ).fetchone()
                for queue_id in queue_ids
            ]
        packet = {
            "schema_version": "vestigia.curation-packet.v0.3",
            "batch_id": batch_id,
            "trigger": str(batch["trigger_reason"]),
            "invitation": (
                "Consider any, all, or none. Attention is not assent. Silence does not escalate."
            ),
            "turns": [
                {
                    "id": str(turn["id"]),
                    "role": str(turn["speaker_role"]),
                    "speaker": str(turn["speaker_id"]),
                    "content": self.counter.trim(str(turn["content"]), 500),
                    "content_hash": str(turn["content_hash"]),
                }
                for turn in turns
                if turn
            ],
            "memories": [
                {
                    "id": item.id,
                    "content": self.counter.trim(item.content, 500),
                    "content_hash": item.content_hash,
                    "type": item.memory_type,
                    "tier": item.tier,
                    "status": item.status,
                    "authority": item.authority_state,
                    "source_id": item.source_id,
                }
                for item in memories
                if item
            ],
            "queued": [
                {
                    "id": str(row["id"]),
                    "kind": str(row["kind"]),
                    "source_id": row["source_id"],
                    "content": self.counter.trim(str(row["content"]), 500),
                    "content_hash": str(row["content_hash"]),
                    "provenance": json.loads(str(row["provenance_json"]) or "{}"),
                }
                for row in queue_rows
                if row
            ],
            "pending_drafts": self.pending_drafts(),
            "controls": {
                "draft": (
                    '[[CURATION_DRAFT {"batch_id":"...","actions":[...]}]]'
                ),
                "claim": (
                    '[[CURATION_CONTROL {"draft_id":"...","action":"claim",'
                    '"expected_hash":"..."}]]'
                ),
                "surface": (
                    '[[CURATION_SURFACE {"mode":"resident_note|next_natural_turn|'
                    'surface_now|discard","text":"..."}]]'
                ),
            },
        }
        maximum = max(1000, int(self.config.get("curation.packet_tokens", 4500)))
        return self._fit_packet_budget(packet, maximum)

    def _fit_packet_budget(
        self, packet: dict[str, Any], maximum: int
    ) -> dict[str, Any]:
        """Keep every selected item's provenance while enforcing a real packet ceiling."""
        original = json.loads(json.dumps(packet, ensure_ascii=False))
        candidate = original
        for content_cap in (500, 320, 220, 160, 120, 80, 40, 20):
            candidate = json.loads(json.dumps(original, ensure_ascii=False))
            for collection in ("turns", "memories", "queued"):
                for item in candidate[collection]:
                    item["content"] = self.counter.trim(
                        str(item.get("content", "")), content_cap
                    )
            for draft in candidate["pending_drafts"]:
                for action in draft.get("actions", []):
                    if action.get("content"):
                        action["content"] = self.counter.trim(
                            str(action["content"]), content_cap
                        )
            encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            if self.counter.count(encoded) <= maximum:
                candidate["packet_budget"] = {
                    "maximum_tokens": maximum,
                    "used_tokens": self.counter.count(encoded),
                    "content_excerpt_tokens": content_cap,
                    "items_omitted": 0,
                }
                return candidate
        # At an unusually small configured ceiling, preserve the complete selected-ID
        # inventory and hashes rather than silently dropping review coverage.
        for collection in ("turns", "memories", "queued"):
            for item in candidate[collection]:
                item["content"] = ""
        for draft in candidate["pending_drafts"]:
            for action in draft.get("actions", []):
                content = str(action.get("content") or "")
                if content:
                    action["content_hash"] = sha256_text(content)
                    action["content_tokens"] = self.counter.count(content)
                    action["content"] = ""
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        used = self.counter.count(encoded)
        if used > maximum:
            raise ValueError(
                "curation packet metadata alone exceeds its configured token ceiling; "
                "increase curation.packet_tokens or reduce the batch size"
            )
        candidate["packet_budget"] = {
            "maximum_tokens": maximum,
            "used_tokens": used,
            "content_excerpt_tokens": 0,
            "items_omitted": 0,
        }
        return candidate

    def pending_drafts(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM curation_drafts
                WHERE resident_id=? AND status='pending'
                ORDER BY rowid LIMIT 20
                """,
                (self.resident_id,),
            ).fetchall()
        return [
            {
                "draft_id": str(row["id"]),
                "batch_id": str(row["batch_id"]),
                "expected_hash": str(row["payload_hash"]),
                "actions": json.loads(str(row["actions_json"])),
                "preview": json.loads(str(row["preview_json"])),
            }
            for row in rows
        ]

    # ---------- two-breath memory controls ----------

    def draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"batch_id", "actions", "reason"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("unsupported curation draft fields: " + ", ".join(sorted(unknown)))
        batch_id = str(payload.get("batch_id", "")).strip()
        with self.db.connect() as connection:
            batch = connection.execute(
                """
                SELECT * FROM curation_batches
                WHERE id=? AND resident_id=?
                """,
                (batch_id, self.resident_id),
            ).fetchone()
        if not batch or str(batch["status"]) not in {"considering", "awaiting_claim"}:
            raise KeyError("unknown or closed curation batch")
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("curation draft requires at least one action")
        if len(actions) > 12:
            raise ValueError("one curation draft may contain at most 12 actions")
        normalized = [self._validate_action(item, batch) for item in actions]
        memory_targets = [
            str(item["memory_id"]) for item in normalized if item.get("memory_id")
        ]
        if len(memory_targets) != len(set(memory_targets)):
            raise ValueError(
                "one curation draft may contain only one action per memory record"
            )
        queue_targets = [
            str(item["queue_id"]) for item in normalized if item.get("queue_id")
        ]
        if len(queue_targets) != len(set(queue_targets)):
            raise ValueError(
                "one curation draft may contain only one action per curation queue item"
            )
        canonical = {
            "batch_id": batch_id,
            "actions": normalized,
            "reason": str(payload.get("reason", "")).strip(),
        }
        preview = self._preview(normalized)
        draft_limit = max(
            800,
            int(self.config.get("curation.packet_tokens", 4500)) - 800,
        )
        if self.counter.count(stable_json(canonical)) > draft_limit:
            raise ValueError(
                "curation draft exceeds the bounded review size; split it into smaller drafts"
            )
        digest = sha256_text(stable_json(canonical))
        draft_id = new_id("curation_draft")
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO curation_drafts
                (id, resident_id, room_id, batch_id, actions_json, payload_hash,
                 preview_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    draft_id,
                    self.resident_id,
                    self.room_id,
                    batch_id,
                    stable_json(normalized),
                    digest,
                    stable_json(preview),
                    now,
                ),
            )
            connection.execute(
                "UPDATE curation_batches SET status='awaiting_claim' WHERE id=?",
                (batch_id,),
            )
            connection.execute(
                """
                INSERT INTO curation_events
                (id, resident_id, batch_id, draft_id, event_type, status,
                 payload_json, created_at)
                VALUES (?, ?, ?, ?, 'draft_created', 'pending', ?, ?)
                """,
                (
                    new_id("curation_event"),
                    self.resident_id,
                    batch_id,
                    draft_id,
                    stable_json(preview),
                    now,
                ),
            )
        return {
            "draft_id": draft_id,
            "expected_hash": digest,
            "preview": preview,
            "applied": False,
        }

    def _validate_action(self, raw: Any, batch: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("every curation action must be an object")
        allowed = {
            "action",
            "memory_id",
            "content",
            "type",
            "tier",
            "reason",
            "source_turn_ids",
            "tags",
            "glyphs",
            "until",
            "queue_id",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("unsupported curation action fields: " + ", ".join(sorted(unknown)))
        action = str(raw.get("action", "")).strip().lower()
        if action not in CURATION_ACTIONS:
            raise ValueError(f"unknown curation action: {action}")
        raw_memory_id = raw.get("memory_id")
        memory_id = str(raw_memory_id).strip() if raw_memory_id else None
        if action != "propose" and not memory_id:
            raise ValueError(f"{action} requires memory_id")
        record = self.db.get_memory(memory_id) if memory_id else None
        if memory_id and (record is None or record.resident_id != self.resident_id):
            raise KeyError(f"unknown resident memory: {memory_id}")
        content = str(raw.get("content", "")).strip() or None
        if action in {"revise", "propose"} and not content:
            raise ValueError(f"{action} requires content")
        memory_type = str(raw.get("type") or (record.memory_type if record else "other"))
        tier = str(raw.get("tier") or (record.tier if record else "warm"))
        if memory_type not in {item.value for item in MemoryType}:
            raise ValueError(f"unknown memory type: {memory_type}")
        if tier not in {item.value for item in ResidencyTier}:
            raise ValueError(f"unknown memory tier: {tier}")
        source_turn_ids = [str(item) for item in raw.get("source_turn_ids", [])]
        batch_turn_ids = set(json.loads(str(batch["turn_ids_json"])))
        for turn_id in source_turn_ids:
            turn = self.db.get_turn(turn_id)
            if turn is None or str(turn["resident_id"]) != self.resident_id:
                raise KeyError("curation action references an unknown resident source turn")
            if turn_id not in batch_turn_ids:
                # Intentional scoped retrieval may add another turn from this resident;
                # provenance must still resolve to a real local ledger row.
                continue
        return {
            "action": action,
            "memory_id": memory_id,
            "content": content,
            "type": memory_type,
            "tier": tier,
            "reason": str(raw.get("reason", "")).strip(),
            "source_turn_ids": source_turn_ids,
            "tags": sorted({str(item).strip() for item in raw.get("tags", []) if str(item).strip()}),
            "glyphs": sorted(
                {str(item).strip() for item in raw.get("glyphs", []) if str(item).strip()}
            ),
            "until": str(raw.get("until", "")).strip() or None,
            "queue_id": (
                str(raw.get("queue_id")).strip() if raw.get("queue_id") else None
            ),
        }

    def _preview(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        core_delta = 0
        for action in actions:
            counts[action["action"]] += 1
            record = self.db.get_memory(action["memory_id"]) if action["memory_id"] else None
            old_core = (
                self.counter.count(record.content)
                if record and record.tier == ResidencyTier.CORE.value
                and record.status in {
                    MemoryStatus.ACCEPTED.value,
                    MemoryStatus.INHERITED_UNREVIEWED.value,
                }
                else 0
            )
            new_content = action["content"] or (record.content if record else "")
            new_core = (
                self.counter.count(new_content)
                if action["action"] in {"claim", "revise", "propose"}
                and action["tier"] == ResidencyTier.CORE.value
                else 0
            )
            core_delta += new_core - old_core
        current = self.dry_run()["core"]["tokens"]
        projected = current + core_delta
        hard_limit = int(self.config.get("memory.core_hard_limit_tokens", 2000))
        if projected > hard_limit:
            raise ValueError(
                f"draft would exceed Core hard limit ({projected}>{hard_limit} tokens)"
            )
        return {
            "action_counts": dict(sorted(counts.items())),
            "records_affected": len(actions),
            "core_tokens_before": current,
            "core_token_delta": core_delta,
            "core_tokens_after": projected,
            "automatic_promotion": False,
            "changes_applied": 0,
        }

    def resolve(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft_id = str(payload.get("draft_id", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"claim", "reject"}:
            raise ValueError("curation control action must be claim or reject")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM curation_drafts
                WHERE id=? AND resident_id=?
                """,
                (draft_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "pending":
            raise KeyError("unknown or resolved curation draft")
        if str(payload.get("expected_hash", "")) != str(row["payload_hash"]):
            raise PermissionError("curation draft hash mismatch")
        now = utc_now_iso()
        if action == "reject":
            with self.db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE curation_drafts SET status='rejected', resolved_at=?
                    WHERE id=?
                    """,
                    (now, draft_id),
                )
                connection.execute(
                    """
                    UPDATE curation_batches SET status='considering'
                    WHERE id=?
                    """,
                    (str(row["batch_id"]),),
                )
                self._insert_event(
                    connection,
                    batch_id=str(row["batch_id"]),
                    draft_id=draft_id,
                    event_type="draft_rejected",
                    status="rejected",
                    payload={},
                    now=now,
                )
            return {"draft_id": draft_id, "status": "rejected", "changes_applied": 0}
        actions = json.loads(str(row["actions_json"]))
        # Revalidate against current state before beginning the atomic write.
        with self.db.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM curation_batches WHERE id=?", (str(row["batch_id"]),)
            ).fetchone()
        normalized = [self._validate_action(item, batch) for item in actions]
        self._preview(normalized)
        created: list[str] = []
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in normalized:
                result = self._apply_action(connection, item, batch)
                if result:
                    created.append(result)
                if item.get("queue_id"):
                    connection.execute(
                        """
                        UPDATE curation_queue SET status='reviewed', resolved_at=?
                        WHERE id=? AND resident_id=?
                        """,
                        (now, item["queue_id"], self.resident_id),
                    )
            connection.execute(
                """
                UPDATE curation_drafts SET status='claimed', resolved_at=?
                WHERE id=?
                """,
                (now, draft_id),
            )
            connection.execute(
                """
                UPDATE curation_batches SET status='claimed', resolved_at=?
                WHERE id=?
                """,
                (now, str(row["batch_id"])),
            )
            self._insert_event(
                connection,
                batch_id=str(row["batch_id"]),
                draft_id=draft_id,
                event_type="draft_claimed",
                status="claimed",
                payload={"actions": len(normalized), "created_record_ids": created},
                now=now,
            )
        return {
            "draft_id": draft_id,
            "status": "claimed",
            "changes_applied": len(normalized),
            "created_record_ids": created,
        }

    def _apply_action(self, connection: Any, item: dict[str, Any], batch: Any) -> str | None:
        action = item["action"]
        memory_id = item["memory_id"]
        reason = item["reason"] or f"resident curation {action}"
        if action in {"reject", "dispute", "defer", "release"}:
            status = {
                "reject": MemoryStatus.REJECTED.value,
                "dispute": MemoryStatus.DISPUTED.value,
                "defer": MemoryStatus.DEFERRED.value,
                "release": MemoryStatus.RELEASED.value,
            }[action]
            self._insert_memory_event(
                connection,
                memory_id,
                event_type=action,
                status=status,
                reason=reason,
                payload={"until": item["until"]} if item["until"] else {},
            )
            return None
        old = (
            connection.execute("SELECT * FROM memory_records WHERE id=?", (memory_id,)).fetchone()
            if memory_id
            else None
        )
        content = item["content"] or (str(old["content"]) if old else "")
        changed = bool(
            old
            and (
                content != str(old["content"])
                or item["type"] != str(old["memory_type"])
                or item["tier"] != str(old["tier"])
            )
        )
        if action == "claim" and old and not changed:
            self._insert_memory_event(
                connection,
                memory_id,
                event_type="accepted",
                status=MemoryStatus.ACCEPTED.value,
                reason=reason,
                authority_state=AuthorityState.RESIDENT_ACCEPTED.value,
                payload={"actor_role": "resident", "batch_id": str(batch["id"])},
            )
            return None
        new_record = new_id("mem")
        now = utc_now_iso()
        turn_ids = item["source_turn_ids"] or json.loads(str(batch["turn_ids_json"]))
        source_id = turn_ids[0] if turn_ids else (str(old["source_id"]) if old and old["source_id"] else None)
        lineage = (
            str(old["source_lineage_id"])
            if old and old["source_lineage_id"]
            else (source_id or new_record)
        )
        tags = item["tags"] or (json.loads(str(old["tags_json"])) if old else [])
        glyphs = item["glyphs"] or (json.loads(str(old["glyphs_json"])) if old else [])
        provenance = json.loads(str(old["provenance_json"])) if old else {}
        provenance.update(
            {
                "curation_batch_id": str(batch["id"]),
                "source_turn_ids": turn_ids,
                "resident_claimed": True,
            }
        )
        connection.execute(
            """
            INSERT INTO memory_records (
                id, resident_id, room_id, content, content_hash, memory_type, tier,
                authorship, authority_state, privacy, source_id, source_lineage_id,
                independent_source_key, expires_at, verification_due_at, supersedes_id,
                tags_json, glyphs_json, provenance_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'private', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_record,
                self.resident_id,
                self.room_id,
                content,
                sha256_text(content),
                item["type"],
                item["tier"],
                f"resident:{self.resident_id}",
                AuthorityState.RESIDENT_ACCEPTED.value,
                source_id,
                lineage,
                source_id,
                str(old["expires_at"]) if old and old["expires_at"] else None,
                str(old["verification_due_at"]) if old and old["verification_due_at"] else None,
                memory_id,
                stable_json(tags),
                stable_json(glyphs),
                stable_json(provenance),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO memory_fts(record_id, content, tags, glyphs) VALUES (?, ?, ?, ?)",
            (new_record, content, " ".join(tags), " ".join(glyphs)),
        )
        self._insert_memory_event(
            connection,
            new_record,
            event_type="created",
            status=MemoryStatus.ACCEPTED.value,
            reason=reason,
            authority_state=AuthorityState.RESIDENT_ACCEPTED.value,
            payload={"batch_id": str(batch["id"]), "action": action},
        )
        if old:
            self._insert_memory_event(
                connection,
                memory_id,
                event_type="superseded",
                status=MemoryStatus.SUPERSEDED.value,
                reason=reason,
                replacement_id=new_record,
            )
        return new_record

    def _insert_memory_event(
        self,
        connection: Any,
        record_id: str,
        *,
        event_type: str,
        status: str,
        reason: str,
        replacement_id: str | None = None,
        authority_state: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_events
            (id, record_id, event_type, status, actor, reason, replacement_id,
             authority_state, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mev"),
                record_id,
                event_type,
                status,
                f"resident:{self.resident_id}",
                reason,
                replacement_id,
                authority_state,
                stable_json(payload or {}),
                utc_now_iso(),
            ),
        )

    def _insert_event(
        self,
        connection: Any,
        *,
        batch_id: str | None,
        draft_id: str | None,
        event_type: str,
        status: str,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO curation_events
            (id, resident_id, batch_id, draft_id, event_type, status,
             payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("curation_event"),
                self.resident_id,
                batch_id,
                draft_id,
                event_type,
                status,
                stable_json(payload),
                now,
            ),
        )

    # ---------- reflection routing ----------

    def surface(self, payload: dict[str, Any], *, batch_id: str | None) -> dict[str, Any]:
        allowed = {"mode", "text"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("unsupported curation surface fields: " + ", ".join(sorted(unknown)))
        mode = str(payload.get("mode", "")).strip().lower()
        if mode not in SURFACE_MODES:
            raise ValueError(f"unknown reflection destination: {mode}")
        text = str(payload.get("text", "")).strip()
        if mode != "discard" and not text:
            raise ValueError(f"{mode} requires reflection text")
        if self.counter.count(text) > 1200:
            raise ValueError("one curation reflection may contain at most 1200 tokens")
        reflection_id = new_id("reflection")
        stored_content = text if mode != "discard" else None
        status = "discarded" if mode == "discard" else (
            "queued" if mode == "next_natural_turn" else "stored"
        )
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO curation_reflections
                (id, resident_id, batch_id, mode, content, content_hash,
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reflection_id,
                    self.resident_id,
                    batch_id,
                    mode,
                    stored_content,
                    sha256_text(text),
                    status,
                    utc_now_iso(),
                ),
            )
        return {
            "reflection_id": reflection_id,
            "mode": mode,
            "status": status,
            "text": text if mode == "surface_now" else None,
            "memory_promotion": False,
        }

    def queued_reflections(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, content, created_at FROM curation_reflections
                WHERE resident_id=? AND mode='next_natural_turn' AND status='queued'
                ORDER BY rowid LIMIT 6
                """,
                (self.resident_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_reflections_delivered(self, reflection_ids: list[str]) -> None:
        if not reflection_ids:
            return
        placeholders = ",".join("?" for _ in reflection_ids)
        with self.db.connect() as connection:
            connection.execute(
                f"""
                UPDATE curation_reflections SET status='delivered', delivered_at=?
                WHERE resident_id=? AND id IN ({placeholders})
                """,
                [utc_now_iso(), self.resident_id, *reflection_ids],
            )

    # ---------- authenticated output parser ----------

    def apply_resident_controls(
        self,
        text: str,
        *,
        batch_id: str | None = None,
        internal: bool = False,
    ) -> tuple[str, list[str], list[str]]:
        kept: list[str] = []
        receipts: list[str] = []
        surfaced: list[str] = []
        with self.db.connect() as connection:
            preexisting = {
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id FROM curation_drafts
                    WHERE resident_id=? AND status='pending'
                    """,
                    (self.resident_id,),
                ).fetchall()
            }
        for line in text.splitlines():
            stripped = line.strip()
            draft_match = CURATION_DRAFT_PATTERN.match(stripped)
            control_match = CURATION_CONTROL_PATTERN.match(stripped)
            surface_match = CURATION_SURFACE_PATTERN.match(stripped)
            if not draft_match and not control_match and not surface_match:
                kept.append(line)
                continue
            try:
                if draft_match:
                    result = self.draft(json.loads(draft_match.group(1)))
                    receipts.append(f"curation_draft:pending:{stable_json(result)}")
                elif control_match:
                    payload = json.loads(control_match.group(1))
                    if str(payload.get("draft_id", "")) not in preexisting:
                        raise PermissionError(
                            "curation claim requires a draft from an earlier resident breath"
                        )
                    result = self.resolve(payload)
                    receipts.append(f"curation_control:ok:{stable_json(result)}")
                else:
                    result = self.surface(
                        json.loads(surface_match.group(1)), batch_id=batch_id
                    )
                    receipts.append(
                        f"curation_surface:{result['mode']}:{result['reflection_id']}"
                    )
                    if result.get("text"):
                        surfaced.append(str(result["text"]))
            except Exception as exc:
                receipts.append(f"curation:rejected:{exc}")
        visible = "" if internal else "\n".join(kept).strip()
        if internal and kept:
            self._record_private_prose(batch_id, "\n".join(kept))
        return visible, receipts, surfaced

    def _record_private_prose(self, batch_id: str | None, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        with self.db.connect() as connection:
            self._insert_event(
                connection,
                batch_id=batch_id,
                draft_id=None,
                event_type="private_prose_not_surfaced",
                status="hash_only",
                payload={
                    "content_hash": sha256_text(clean),
                    "tokens": self.counter.count(clean),
                },
                now=utc_now_iso(),
            )

    def internal_prompt(self, packet: dict[str, Any]) -> str:
        return (
            "# Private VESTIGIA curation room\n\n"
            "This is an internal resident maintenance invocation. It is an invitation, not "
            "an obligation. You may do nothing. No ordinary prose is posted. To retain or "
            "surface a deliberately authored reflection, use CURATION_SURFACE. Memory changes "
            "must use CURATION_DRAFT and a later hash-bound CURATION_CONTROL claim. A draft "
            "and its claim cannot occur in the same invocation. Compression changes "
            "accessibility, not authority. Runtime protections are not personal-memory toggles.\n\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
