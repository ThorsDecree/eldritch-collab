from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import ResolvedConfig
from .db import ContinuityDB
from .utils import new_id, sha256_text, stable_json, utc_now_iso


LEGIBLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS house_objects (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    locator TEXT NOT NULL,
    content_hash TEXT,
    evidence_state TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    verified_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(resident_id, object_type, locator)
);

CREATE INDEX IF NOT EXISTS idx_house_objects_locator
ON house_objects(resident_id, locator);

CREATE TABLE IF NOT EXISTS house_object_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES house_objects(id)
);

CREATE TABLE IF NOT EXISTS house_receipts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    turn_id TEXT,
    parent_receipt_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    source_envelope TEXT NOT NULL,
    normalized_envelope TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    result_hash TEXT NOT NULL,
    outward_effect TEXT NOT NULL DEFAULT 'none',
    pinned INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_house_receipts_resident
ON house_receipts(resident_id, completed_at);

CREATE TABLE IF NOT EXISTS house_attention_breadcrumbs (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    action TEXT NOT NULL,
    unresolved_target TEXT NOT NULL DEFAULT '',
    continuation_json TEXT NOT NULL DEFAULT '{}',
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(resident_id, room_id, receipt_id)
);

CREATE INDEX IF NOT EXISTS idx_house_attention_breadcrumbs
ON house_attention_breadcrumbs(resident_id, room_id, status, expires_at);

CREATE TABLE IF NOT EXISTS house_bookmarks (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    label TEXT NOT NULL,
    location_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES house_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_house_bookmarks_resident
ON house_bookmarks(resident_id, status, created_at);

CREATE TABLE IF NOT EXISTS house_activities (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    turn_id TEXT,
    job_id TEXT,
    status TEXT NOT NULL,
    operation TEXT NOT NULL,
    resident_note TEXT NOT NULL DEFAULT '',
    budget_json TEXT NOT NULL DEFAULT '{}',
    last_receipt_id TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_house_activities_resident
ON house_activities(resident_id, updated_at);
"""


class LegibleLedger:
    """Stable house objects, durable receipts, bookmarks, and activity cards."""

    def __init__(self, config: ResolvedConfig, db: ContinuityDB) -> None:
        self.config = config
        self.db = db
        self.home = config.home_path.resolve()
        self.resident_id = str(config.get("resident.id"))
        self.room_id = str(config.get("room.id"))
        with self.db.connect() as connection:
            connection.executescript(LEGIBLE_SCHEMA)

    # ---------- stable objects ----------

    @staticmethod
    def _prefix(object_type: str) -> str:
        return {
            "document": "doc",
            "folder": "folder",
            "image": "img",
            "memory": "mem",
            "note": "note",
            "job": "job",
            "curation_batch": "batch",
            "receipt": "receipt",
        }.get(object_type, "object")

    def register_object(
        self,
        *,
        object_type: str,
        locator: str,
        content_hash: str | None = None,
        evidence_state: str = "verified_now",
        metadata: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        preferred_id: str | None = None,
    ) -> str:
        now = utc_now_iso()
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM house_objects
                WHERE resident_id=? AND object_type=? AND locator=?
                """,
                (self.resident_id, object_type, locator),
            ).fetchone()
            object_id = str(existing["id"]) if existing else (
                preferred_id or new_id(self._prefix(object_type))
            )
            connection.execute(
                """
                INSERT INTO house_objects
                (id, resident_id, object_type, locator, content_hash, evidence_state,
                 metadata_json, provenance_json, first_seen_at, verified_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resident_id, object_type, locator) DO UPDATE SET
                  content_hash=excluded.content_hash,
                  evidence_state=excluded.evidence_state,
                  metadata_json=excluded.metadata_json,
                  provenance_json=excluded.provenance_json,
                  verified_at=excluded.verified_at,
                  updated_at=excluded.updated_at
                """,
                (
                    object_id,
                    self.resident_id,
                    object_type,
                    locator,
                    content_hash,
                    evidence_state,
                    stable_json(metadata or {}),
                    stable_json(provenance or {}),
                    now,
                    now if evidence_state.startswith("verified") else None,
                    now,
                ),
            )
        return object_id

    def object_by_reference(self, reference: str) -> dict[str, Any] | None:
        clean = str(reference).strip()
        if clean.startswith("house://"):
            clean = clean[8:]
            if clean.startswith(self.resident_id + "/"):
                clean = clean[len(self.resident_id) + 1 :]
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM house_objects
                WHERE resident_id=? AND (id=? OR locator=?)
                ORDER BY rowid DESC LIMIT 1
                """,
                (self.resident_id, clean, clean),
            ).fetchone()
        return self._object_row(row) if row else None

    def list_objects(
        self,
        *,
        scope: str = "",
        object_type: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM house_objects WHERE resident_id=?"
        params: list[Any] = [self.resident_id]
        if scope:
            sql += " AND locator LIKE ?"
            params.append(scope.rstrip("/") + "%")
        if object_type:
            sql += " AND object_type=?"
            params.append(object_type)
        sql += " ORDER BY locator LIMIT ?"
        params.append(min(300, max(1, int(limit))))
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._object_row(row) for row in rows]

    def object_event(
        self,
        object_id: str,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = new_id("object_event")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_object_events
                (id, resident_id, object_id, event_type, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    self.resident_id,
                    object_id,
                    event_type,
                    actor,
                    stable_json(payload or {}),
                    utc_now_iso(),
                ),
            )
        return event_id

    def object_history(self, object_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM house_object_events
                WHERE resident_id=? AND object_id=?
                ORDER BY rowid DESC LIMIT ?
                """,
                (self.resident_id, object_id, min(200, max(1, limit))),
            ).fetchall()
        return [self._json_row(row, ("payload_json",)) for row in rows]

    # ---------- immutable action receipts ----------

    def record_receipt(
        self,
        *,
        action: str,
        status: str,
        result: dict[str, Any],
        turn_id: str | None = None,
        source_envelope: str = "TOOL_ACTION",
        target: dict[str, Any] | None = None,
        parent_receipt_id: str | None = None,
        outward_effect: str = "none",
        started_at: str | None = None,
    ) -> str:
        receipt_id = new_id("receipt")
        now = utc_now_iso()
        safe_result = self._bounded_json_value(result)
        result_json = stable_json(safe_result)
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_receipts
                (id, resident_id, room_id, turn_id, parent_receipt_id, action, status,
                 source_envelope, normalized_envelope, adapter_version, target_json,
                 result_json, result_hash, outward_effect, pinned, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'TOOL_ACTION', 'v0.5', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    receipt_id,
                    self.resident_id,
                    self.room_id,
                    turn_id,
                    parent_receipt_id,
                    action,
                    status,
                    source_envelope,
                    stable_json(target or {}),
                    result_json,
                    sha256_text(result_json),
                    outward_effect,
                    started_at or now,
                    now,
                ),
            )
        self.register_object(
            object_type="receipt",
            locator=f"receipts/{receipt_id}",
            content_hash=sha256_text(result_json),
            metadata={"action": action, "status": status, "turn_id": turn_id},
            provenance={"source_envelope": source_envelope},
            preferred_id=receipt_id,
        )
        return receipt_id

    def list_receipts(
        self,
        *,
        limit: int = 20,
        pinned_only: bool = False,
        turn_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        object_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM house_receipts WHERE resident_id=?"
        params: list[Any] = [self.resident_id]
        if pinned_only:
            sql += " AND pinned=1"
        if turn_id:
            sql += " AND turn_id=?"
            params.append(turn_id)
        if action:
            sql += " AND action=?"
            params.append(action)
        if status:
            sql += " AND status=?"
            params.append(status)
        if object_id:
            sql += " AND target_json LIKE ?"
            params.append(f"%{object_id}%")
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(min(200, max(1, int(limit))))
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._receipt_row(row, include_result=False) for row in rows]

    def inspect_receipt(self, receipt_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM house_receipts WHERE id=? AND resident_id=?",
                (receipt_id, self.resident_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown house receipt")
        return self._receipt_row(row, include_result=True)

    def pin_receipt(self, receipt_id: str, pinned: bool) -> dict[str, Any]:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE house_receipts SET pinned=? WHERE id=? AND resident_id=?",
                (1 if pinned else 0, receipt_id, self.resident_id),
            )
        if cursor.rowcount != 1:
            raise KeyError("unknown house receipt")
        return {"receipt_id": receipt_id, "pinned": pinned}

    def preserve_breadcrumb(
        self,
        *,
        receipt_id: str,
        action: str,
        unresolved_target: str = "",
        continuation: dict[str, Any] | None = None,
        label: str = "",
        hours: int = 24,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        expires_at = (now + timedelta(hours=min(168, max(1, int(hours))))).isoformat()
        breadcrumb_id = new_id("breadcrumb")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_attention_breadcrumbs
                (id, resident_id, room_id, receipt_id, action, unresolved_target,
                 continuation_json, label, status, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(resident_id, room_id, receipt_id) DO UPDATE SET
                  action=excluded.action,
                  unresolved_target=excluded.unresolved_target,
                  continuation_json=excluded.continuation_json,
                  label=excluded.label,
                  status='active',
                  expires_at=excluded.expires_at,
                  resolved_at=NULL
                """,
                (
                    breadcrumb_id,
                    self.resident_id,
                    self.room_id,
                    receipt_id,
                    action,
                    unresolved_target,
                    stable_json(continuation or {}),
                    label,
                    expires_at,
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM house_attention_breadcrumbs
                WHERE resident_id=? AND room_id=? AND receipt_id=?
                """,
                (self.resident_id, self.room_id, receipt_id),
            ).fetchone()
        return self._breadcrumb_row(row)

    def list_breadcrumbs(self, *, limit: int = 12) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM house_attention_breadcrumbs
                WHERE resident_id=? AND room_id=? AND status='active'
                  AND expires_at>?
                ORDER BY rowid DESC LIMIT ?
                """,
                (
                    self.resident_id,
                    self.room_id,
                    datetime.now(UTC).isoformat(),
                    min(50, max(1, int(limit))),
                ),
            ).fetchall()
        return [self._breadcrumb_row(row) for row in rows]

    def resolve_breadcrumb(
        self,
        *,
        receipt_id: str | None = None,
        unresolved_target: str | None = None,
    ) -> int:
        if not receipt_id and not unresolved_target:
            return 0
        clauses = ["resident_id=?", "room_id=?", "status='active'"]
        params: list[Any] = [self.resident_id, self.room_id]
        if receipt_id:
            clauses.append("receipt_id=?")
            params.append(receipt_id)
        if unresolved_target:
            clauses.append("unresolved_target=?")
            params.append(unresolved_target)
        params.append(utc_now_iso())
        with self.db.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE house_attention_breadcrumbs
                SET status='resolved', resolved_at=?
                WHERE {' AND '.join(clauses)}
                """,
                [params[-1], *params[:-1]],
            )
        return int(cursor.rowcount)

    # ---------- bookmarks ----------

    def add_bookmark(
        self,
        object_id: str,
        *,
        label: str = "",
        location: dict[str, Any] | None = None,
        note: str = "",
    ) -> str:
        if not self.object_by_reference(object_id):
            raise KeyError("unknown house object")
        bookmark_id = new_id("bookmark")
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_bookmarks
                (id, resident_id, object_id, label, location_json, note, status,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    bookmark_id,
                    self.resident_id,
                    object_id,
                    label.strip(),
                    stable_json(location or {}),
                    note.strip(),
                    now,
                    now,
                ),
            )
        return bookmark_id

    def list_bookmarks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT b.*, o.object_type, o.locator, o.evidence_state
                FROM house_bookmarks b
                JOIN house_objects o ON o.id=b.object_id
                WHERE b.resident_id=? AND b.status='active'
                ORDER BY b.rowid DESC LIMIT ?
                """,
                (self.resident_id, min(200, max(1, limit))),
            ).fetchall()
        return [self._json_row(row, ("location_json",)) for row in rows]

    def bookmark(self, bookmark_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT b.*, o.object_type, o.locator, o.evidence_state
                FROM house_bookmarks b
                JOIN house_objects o ON o.id=b.object_id
                WHERE b.id=? AND b.resident_id=? AND b.status='active'
                """,
                (bookmark_id, self.resident_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown or removed bookmark")
        return self._json_row(row, ("location_json",))

    def remove_bookmark(self, bookmark_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE house_bookmarks SET status='removed', updated_at=?
                WHERE id=? AND resident_id=? AND status='active'
                """,
                (utc_now_iso(), bookmark_id, self.resident_id),
            )
        if cursor.rowcount != 1:
            raise KeyError("unknown or removed bookmark")
        return {"bookmark_id": bookmark_id, "status": "removed"}

    # ---------- honest activity cards ----------

    def start_activity(
        self,
        *,
        turn_id: str | None,
        operation: str,
        budget: dict[str, Any],
        job_id: str | None = None,
    ) -> str:
        activity_id = new_id("activity")
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_activities
                (id, resident_id, turn_id, job_id, status, operation, resident_note,
                 budget_json, started_at, updated_at)
                VALUES (?, ?, ?, ?, 'working', ?, '', ?, ?, ?)
                """,
                (
                    activity_id,
                    self.resident_id,
                    turn_id,
                    job_id,
                    operation,
                    stable_json(budget),
                    now,
                    now,
                ),
            )
        return activity_id

    def update_activity(
        self,
        activity_id: str,
        *,
        status: str | None = None,
        operation: str | None = None,
        note: str | None = None,
        budget: dict[str, Any] | None = None,
        last_receipt_id: str | None = None,
        complete: bool = False,
    ) -> None:
        fields = ["updated_at=?"]
        params: list[Any] = [utc_now_iso()]
        for column, value in (
            ("status", status),
            ("operation", operation),
            ("resident_note", note),
            ("budget_json", stable_json(budget) if budget is not None else None),
            ("last_receipt_id", last_receipt_id),
        ):
            if value is not None:
                fields.append(f"{column}=?")
                params.append(value)
        if complete:
            fields.append("completed_at=?")
            params.append(utc_now_iso())
        params.extend([activity_id, self.resident_id])
        with self.db.connect() as connection:
            connection.execute(
                f"UPDATE house_activities SET {', '.join(fields)} WHERE id=? AND resident_id=?",
                params,
            )

    def latest_activity(self, *, include_completed: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM house_activities WHERE resident_id=?"
        params: list[Any] = [self.resident_id]
        if not include_completed:
            sql += " AND status NOT IN ('completed','cancelled','failed')"
        sql += " ORDER BY rowid DESC LIMIT 1"
        with self.db.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return self._activity_row(row) if row else None

    def inspect_activity(self, activity_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM house_activities WHERE id=? AND resident_id=?",
                (activity_id, self.resident_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown house activity")
        return self._activity_row(row)

    # ---------- row helpers ----------

    @staticmethod
    def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
        if depth > 6:
            return "[nested result omitted]"
        if isinstance(value, dict):
            return {
                str(key): LegibleLedger._bounded_json_value(child, depth=depth + 1)
                for key, child in list(value.items())[:100]
                if not str(key).startswith("_")
            }
        if isinstance(value, list):
            return [
                LegibleLedger._bounded_json_value(child, depth=depth + 1)
                for child in value[:100]
            ]
        if isinstance(value, str) and len(value) > 20_000:
            return value[:20_000] + "\n[…receipt value bounded…]"
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _json_row(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
        item = dict(row)
        for column in columns:
            if column in item:
                item[column.removesuffix("_json")] = json.loads(item.pop(column) or "{}")
        return item

    @staticmethod
    def _object_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
        item["citation"] = f"house://{item['locator']}"
        return item

    @staticmethod
    def _receipt_row(row: Any, *, include_result: bool) -> dict[str, Any]:
        item = dict(row)
        item["pinned"] = bool(item["pinned"])
        item["target"] = json.loads(item.pop("target_json") or "{}")
        raw_result = item.pop("result_json")
        if include_result:
            item["result"] = json.loads(raw_result or "{}")
        return item

    @staticmethod
    def _breadcrumb_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["continuation"] = json.loads(item.pop("continuation_json") or "{}")
        return item

    @staticmethod
    def _activity_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["budget"] = json.loads(item.pop("budget_json") or "{}")
        return item
