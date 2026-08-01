from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import MemoryRecord
from .utils import new_id, sha256_text, stable_json, utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    tier TEXT NOT NULL,
    authorship TEXT NOT NULL,
    authority_state TEXT NOT NULL,
    privacy TEXT NOT NULL,
    source_id TEXT,
    source_lineage_id TEXT,
    independent_source_key TEXT,
    expires_at TEXT,
    verification_due_at TEXT,
    supersedes_id TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    glyphs_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (supersedes_id) REFERENCES memory_records(id)
);

CREATE TABLE IF NOT EXISTS memory_events (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    replacement_id TEXT,
    authority_state TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES memory_records(id),
    FOREIGN KEY (replacement_id) REFERENCES memory_records(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_resident ON memory_records(resident_id, room_id);
CREATE INDEX IF NOT EXISTS idx_memory_hash ON memory_records(content_hash);
CREATE INDEX IF NOT EXISTS idx_memory_lineage ON memory_records(source_lineage_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_record ON memory_events(record_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    content,
    tags,
    glyphs,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    speaker_role TEXT NOT NULL,
    speaker_id TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    interface TEXT NOT NULL,
    external_id TEXT,
    parent_turn_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_room ON turns(resident_id, room_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_external
ON turns(interface, external_id)
WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    turn_id TEXT,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    prompt_text TEXT,
    source_images_json TEXT NOT NULL DEFAULT '[]',
    visual_records_json TEXT NOT NULL DEFAULT '[]',
    privacy TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_events (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_created ON artifacts(resident_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifact_events ON artifact_events(artifact_id, created_at);

CREATE TABLE IF NOT EXISTS image_assets (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    artifact_id TEXT,
    content_hash TEXT NOT NULL,
    path TEXT NOT NULL,
    original_filename TEXT,
    media_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    source_kind TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    privacy TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(resident_id, content_hash)
);

CREATE TABLE IF NOT EXISTS image_events (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_interpretations (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    route TEXT NOT NULL,
    model TEXT NOT NULL,
    detail TEXT NOT NULL,
    question_category TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    cache_key TEXT NOT NULL UNIQUE,
    result_text TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_share_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    image_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_turn_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (image_id) REFERENCES image_assets(id)
);

CREATE TABLE IF NOT EXISTS image_jobs (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error_type TEXT,
    error_hash TEXT,
    created_turn_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_image_assets_resident
ON image_assets(resident_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_assets_artifact
ON image_assets(artifact_id);
CREATE INDEX IF NOT EXISTS idx_image_events_asset
ON image_events(image_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_interpretations_asset
ON image_interpretations(image_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_jobs_status
ON image_jobs(resident_id, status, created_at);
"""


class ContinuityDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            memory_event_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(memory_events)").fetchall()
            }
            if "authority_state" not in memory_event_columns:
                connection.execute("ALTER TABLE memory_events ADD COLUMN authority_state TEXT")
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                ("schema_version", "4"),
            )
        self.check_fts5()

    def check_fts5(self) -> None:
        try:
            with self.connect() as connection:
                connection.execute("SELECT count(*) FROM memory_fts").fetchone()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "This Python SQLite build does not provide FTS5, which VESTIGIA v0.1 requires."
            ) from exc

    def append_state(
        self,
        *,
        resident_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str,
    ) -> str:
        event_id = new_id("state")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO state_events
                (id, resident_id, from_state, to_state, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, resident_id, from_state, to_state, actor, reason, utc_now_iso()),
            )
        return event_id

    def current_state(self, resident_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT to_state FROM state_events
                WHERE resident_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (resident_id,),
            ).fetchone()
        return str(row["to_state"]) if row else None

    def state_history(self, resident_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM state_events WHERE resident_id=? ORDER BY rowid",
                (resident_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(
        self,
        *,
        resident_id: str,
        room_id: str,
        content: str,
        memory_type: str,
        tier: str,
        authorship: str,
        authority_state: str,
        status: str,
        actor: str,
        reason: str,
        privacy: str = "private",
        source_id: str | None = None,
        source_lineage_id: str | None = None,
        independent_source_key: str | None = None,
        expires_at: str | None = None,
        verification_due_at: str | None = None,
        supersedes_id: str | None = None,
        tags: Sequence[str] = (),
        glyphs: Sequence[str] = (),
        provenance: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> str:
        record_id = new_id("mem")
        event_id = new_id("mev")
        clean = content.strip()
        if not clean:
            raise ValueError("Memory content may not be empty")
        now = created_at or utc_now_iso()
        content_hash = sha256_text(clean)
        tags_list = sorted({str(item).strip() for item in tags if str(item).strip()})
        glyphs_list = sorted({str(item).strip() for item in glyphs if str(item).strip()})
        provenance_value = provenance or {}
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO memory_records (
                    id, resident_id, room_id, content, content_hash, memory_type, tier,
                    authorship, authority_state, privacy, source_id, source_lineage_id,
                    independent_source_key, expires_at, verification_due_at, supersedes_id,
                    tags_json, glyphs_json, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    resident_id,
                    room_id,
                    clean,
                    content_hash,
                    memory_type,
                    tier,
                    authorship,
                    authority_state,
                    privacy,
                    source_id,
                    source_lineage_id,
                    independent_source_key,
                    expires_at,
                    verification_due_at,
                    supersedes_id,
                    stable_json(tags_list),
                    stable_json(glyphs_list),
                    stable_json(provenance_value),
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(record_id, content, tags, glyphs) VALUES (?, ?, ?, ?)",
                (record_id, clean, " ".join(tags_list), " ".join(glyphs_list)),
            )
            connection.execute(
                """
                INSERT INTO memory_events
                (id, record_id, event_type, status, actor, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, record_id, "created", status, actor, reason, "{}", now),
            )
        return record_id

    def append_memory_event(
        self,
        record_id: str,
        *,
        event_type: str,
        status: str,
        actor: str,
        reason: str,
        replacement_id: str | None = None,
        authority_state: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        if self.get_memory(record_id) is None:
            raise KeyError(f"Unknown memory record: {record_id}")
        event_id = new_id("mev")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_events
                (id, record_id, event_type, status, actor, reason, replacement_id,
                 authority_state, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    record_id,
                    event_type,
                    status,
                    actor,
                    reason,
                    replacement_id,
                    authority_state,
                    stable_json(payload or {}),
                    utc_now_iso(),
                ),
            )
        return event_id

    def revise_memory(
        self,
        record_id: str,
        *,
        content: str,
        actor: str,
        reason: str,
        status: str = "candidate",
    ) -> str:
        old = self.get_memory(record_id)
        if old is None:
            raise KeyError(f"Unknown memory record: {record_id}")
        new_record = self.add_memory(
            resident_id=old.resident_id,
            room_id=old.room_id,
            content=content,
            memory_type=old.memory_type,
            tier=old.tier,
            authorship=old.authorship,
            authority_state=old.authority_state,
            status=status,
            actor=actor,
            reason=reason,
            privacy=old.privacy,
            source_id=old.source_id,
            source_lineage_id=old.source_lineage_id,
            independent_source_key=old.independent_source_key,
            expires_at=old.expires_at,
            verification_due_at=old.verification_due_at,
            supersedes_id=old.id,
            tags=old.tags,
            glyphs=old.glyphs,
            provenance={**old.provenance, "revised_from": old.id},
        )
        self.append_memory_event(
            old.id,
            event_type="superseded",
            status="superseded",
            actor=actor,
            reason=reason,
            replacement_id=new_record,
        )
        return new_record

    def get_memory(self, record_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                self._memory_projection_sql("WHERE r.id=?"),
                (record_id,),
            ).fetchone()
        return self._row_to_memory(row) if row else None

    def list_memories(
        self,
        *,
        resident_id: str | None = None,
        room_id: str | None = None,
        statuses: Sequence[str] | None = None,
        tiers: Sequence[str] | None = None,
        memory_types: Sequence[str] | None = None,
        limit: int = 500,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if resident_id:
            clauses.append("r.resident_id=?")
            params.append(resident_id)
        if room_id:
            clauses.append("r.room_id=?")
            params.append(room_id)
        for column, values in (
            ("latest_status", statuses),
            ("r.tier", tiers),
            ("r.memory_type", memory_types),
        ):
            if values:
                placeholders = ",".join("?" for _ in values)
                clauses.append(f"{column} IN ({placeholders})")
                params.extend(values)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, int(limit)))
        sql = self._memory_projection_sql(where) + " ORDER BY r.rowid DESC LIMIT ?"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def find_duplicate_hashes(self, content_hash: str) -> list[MemoryRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                self._memory_projection_sql("WHERE r.content_hash=?"),
                (content_hash,),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def search_fts(self, query: str, limit: int = 100) -> dict[str, float]:
        clean = query.strip()
        if not clean:
            return {}
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT record_id, bm25(memory_fts) AS rank
                    FROM memory_fts
                    WHERE memory_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (clean, max(1, int(limit))),
                ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {str(row["record_id"]): float(row["rank"]) for row in rows}

    def add_turn(
        self,
        *,
        resident_id: str,
        room_id: str,
        speaker_role: str,
        speaker_id: str,
        content: str,
        interface: str,
        external_id: str | None = None,
        parent_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> str:
        actual_id = turn_id or new_id("turn")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO turns (
                    id, resident_id, room_id, speaker_role, speaker_id, content,
                    content_hash, interface, external_id, parent_turn_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actual_id,
                    resident_id,
                    room_id,
                    speaker_role,
                    speaker_id,
                    content,
                    sha256_text(content),
                    interface,
                    external_id,
                    parent_turn_id,
                    stable_json(metadata or {}),
                    utc_now_iso(),
                ),
            )
        return actual_id

    def recent_turns(self, resident_id: str, room_id: str, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT rowid AS _rowid, turns.* FROM turns
                    WHERE resident_id=? AND room_id=?
                    ORDER BY rowid DESC LIMIT ?
                ) ORDER BY _rowid
                """,
                (resident_id, room_id, max(1, int(limit))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("_rowid", None)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            result.append(item)
        return result

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item

    def add_artifact(
        self,
        *,
        resident_id: str,
        room_id: str,
        turn_id: str | None,
        operation: str,
        provider: str,
        model: str,
        path: str,
        content_hash: str,
        prompt_hash: str,
        prompt_text: str | None,
        source_images: Sequence[str],
        visual_records: Sequence[str],
        privacy: str,
        status: str = "ephemeral",
    ) -> str:
        artifact_id = new_id("image")
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, resident_id, room_id, turn_id, operation, provider, model, path,
                    content_hash, prompt_hash, prompt_text, source_images_json,
                    visual_records_json, privacy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    resident_id,
                    room_id,
                    turn_id,
                    operation,
                    provider,
                    model,
                    path,
                    content_hash,
                    prompt_hash,
                    prompt_text,
                    stable_json(list(source_images)),
                    stable_json(list(visual_records)),
                    privacy,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_events
                (id, artifact_id, event_type, status, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id("aev"), artifact_id, "created", status, "runtime", "artifact created", now),
            )
        return artifact_id

    def append_artifact_event(
        self,
        artifact_id: str,
        *,
        event_type: str,
        status: str,
        actor: str,
        reason: str,
    ) -> str:
        event_id = new_id("aev")
        with self.connect() as connection:
            exists = connection.execute("SELECT 1 FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not exists:
                raise KeyError(f"Unknown artifact: {artifact_id}")
            connection.execute(
                """
                INSERT INTO artifact_events
                (id, artifact_id, event_type, status, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, artifact_id, event_type, status, actor, reason, utc_now_iso()),
            )
        return event_id

    def list_artifacts(self, resident_id: str, *, since_iso: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [resident_id]
        where = "WHERE a.resident_id=?"
        if since_iso:
            where += " AND a.created_at>=?"
            params.append(since_iso)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT artifact_id, status,
                           ROW_NUMBER() OVER (PARTITION BY artifact_id ORDER BY rowid DESC) AS rn
                    FROM artifact_events
                )
                SELECT a.*, latest.status
                FROM artifacts a
                LEFT JOIN latest ON latest.artifact_id=a.id AND latest.rn=1
                {where}
                ORDER BY a.rowid DESC
                """,
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_images"] = json.loads(item.pop("source_images_json") or "[]")
            item["visual_records"] = json.loads(item.pop("visual_records_json") or "[]")
            result.append(item)
        return result

    def checkpoint(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @staticmethod
    def _memory_projection_sql(where: str) -> str:
        return f"""
        WITH latest AS (
            SELECT record_id, status,
                   ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY rowid DESC) AS rn
            FROM memory_events
        )
        SELECT r.*, COALESCE(latest.status, 'candidate') AS latest_status
             , COALESCE(
                   (
                       SELECT me.authority_state
                       FROM memory_events me
                       WHERE me.record_id=r.id AND me.authority_state IS NOT NULL
                       ORDER BY me.rowid DESC LIMIT 1
                   ),
                   r.authority_state
               ) AS effective_authority_state
        FROM memory_records r
        LEFT JOIN latest ON latest.record_id=r.id AND latest.rn=1
        {where}
        """

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]),
            resident_id=str(row["resident_id"]),
            room_id=str(row["room_id"]),
            content=str(row["content"]),
            memory_type=str(row["memory_type"]),
            tier=str(row["tier"]),
            authorship=str(row["authorship"]),
            authority_state=str(row["effective_authority_state"]),
            privacy=str(row["privacy"]),
            status=str(row["latest_status"]),
            created_at=str(row["created_at"]),
            content_hash=str(row["content_hash"]),
            source_id=row["source_id"],
            source_lineage_id=row["source_lineage_id"],
            independent_source_key=row["independent_source_key"],
            expires_at=row["expires_at"],
            verification_due_at=row["verification_due_at"],
            supersedes_id=row["supersedes_id"],
            tags=tuple(json.loads(row["tags_json"] or "[]")),
            glyphs=tuple(json.loads(row["glyphs_json"] or "[]")),
            provenance=json.loads(row["provenance_json"] or "{}"),
        )
