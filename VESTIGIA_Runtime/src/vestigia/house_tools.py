from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

from .capabilities import CapabilityRegistry, CapabilitySpec
from .capability_contracts import bell_contracts, contract_for
from .config import ResolvedConfig
from .context_controls import (
    VISIBILITY_MODES,
    default_context_controls,
    load_context_controls,
    save_context_controls,
)
from .db import ContinuityDB
from .images import ImageService
from .legible import LegibleLedger
from .utils import (
    TokenCounter,
    atomic_write_text,
    new_id,
    sha256_file,
    sha256_text,
    stable_json,
    utc_now_iso,
)


class HouseCursorError(KeyError):
    """A resident-visible structured cursor lookup failure."""

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


class HouseCursorExpiredError(ValueError):
    """A resident-visible structured cursor expiry failure."""

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


HOUSE_PATTERN = re.compile(r"^\[\[HOUSE_TOOL\s+(\{.*\})\]\]\s*$")
TOOL_ACTION_PATTERN = re.compile(r"^\[\[TOOL_ACTION\s+(\{.*\})\]\]\s*$")
IDENTITY_DRAFT_PATTERN = re.compile(r"^\[\[IDENTITY_DRAFT\s+(\{.*\})\]\]\s*$")
IDENTITY_CONTROL_PATTERN = re.compile(r"^\[\[IDENTITY_CONTROL\s+(\{.*\})\]\]\s*$")
TOOL_DRAFT_PATTERN = re.compile(r"^\[\[TOOL_DRAFT\s+(\{.*\})\]\]\s*$")
TOOL_CONTROL_PATTERN = re.compile(r"^\[\[TOOL_CONTROL\s+(\{.*\})\]\]\s*$")

TEXT_SUFFIXES = {".txt", ".md", ".json", ".jsonl", ".csv", ".yaml", ".yml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DEFAULT_ROOTS = (
    "identity",
    "imports",
    "sessions",
    "scrapbook",
    "artifacts",
    "exports",
    "workspace",
)
VIRTUAL_IMAGE_SHELVES = {
    "images/originals": "artifacts/images/originals",
    "images/generated": "artifacts/images/generated",
    "images/shelf": "artifacts/images/shelf",
    "images/edits": "artifacts/images/edits",
}


def extract_action_envelopes(
    text: str,
) -> tuple[str, list[dict[str, Any]], list[str], list[str]]:
    """Extract executable house envelopes from anywhere in provider prose.

    The original router accepted only one envelope occupying a complete line.
    Residents legitimately place multiple calls together or continue prose after
    a call, so use JSON's own balanced decoder instead of a greedy regular
    expression.  Malformed markers are removed from outward prose and reported
    separately; arbitrary bracketed text remains untouched.
    """

    decoder = json.JSONDecoder()
    markers = (
        ("[[TOOL_ACTION", "tool_action"),
        ("[[HOUSE_TOOL", "house_tool"),
        ("[[REACT", "react"),
    )
    kept: list[str] = []
    calls: list[dict[str, Any]] = []
    kinds: list[str] = []
    errors: list[str] = []
    cursor = 0
    while cursor < len(text):
        candidates = [
            (text.find(marker, cursor), marker, kind)
            for marker, kind in markers
            if text.find(marker, cursor) >= 0
        ]
        if not candidates:
            kept.append(text[cursor:])
            break
        start, marker, kind = min(candidates, key=lambda item: item[0])
        kept.append(text[cursor:start])
        json_start = start + len(marker)
        while json_start < len(text) and text[json_start].isspace():
            json_start += 1
        try:
            payload, json_end = decoder.raw_decode(text, json_start)
            closing = json_end
            while closing < len(text) and text[closing].isspace():
                closing += 1
            if text[closing : closing + 2] != "]]":
                raise ValueError("missing closing ]]")
            if not isinstance(payload, dict):
                raise ValueError("tool payload must be a JSON object")
            if kind == "react":
                payload = {"action": "discord.react", **payload}
            calls.append(payload)
            kinds.append(kind)
            cursor = closing + 2
        except Exception as exc:
            errors.append(f"{kind}:invalid envelope:{exc}")
            closing = text.find("]]", json_start)
            cursor = len(text) if closing < 0 else closing + 2
    cleaned = "".join(kept)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls, kinds, errors
FORGE_STEP_ACTIONS = {
    "list",
    "search",
    "read",
    "stat",
    "bookmark",
    "object.list",
    "object.search",
    "object.stat",
    "object.inspect",
    "object.history",
    "object.provenance",
    "file.diff",
    "file.write",
    "file.patch",
    "bookmark.add",
    "bookmark.list",
    "bookmark.open",
    "receipt.list",
    "receipt.inspect",
    "activity.status",
    "activity.note",
    "curation.list",
    "curation.inspect",
    "curation.history",
    "memory.search",
    "memory.read",
    "memory.history",
    "memory.provenance",
    "memory.queue_for_review",
    "note.append",
    "note.read",
    "note.search",
}
FORGE_STEP_FIELDS = {
    "list": {"action", "scope", "limit"},
    "search": {"action", "scope", "query", "max_results"},
    "read": {"action", "path", "heading", "chunk", "max_tokens"},
    "stat": {"action", "path"},
    "bookmark": {"action", "path", "heading", "chunk", "max_tokens"},
    "object.list": {"action", "scope", "type", "limit"},
    "object.search": {"action", "scope", "query", "limit"},
    "object.stat": {"action", "reference", "object_id", "path"},
    "object.inspect": {
        "action", "reference", "object_id", "path", "heading", "chunk",
        "max_tokens", "routes", "question", "language",
    },
    "object.history": {"action", "reference", "object_id", "limit"},
    "object.provenance": {"action", "reference", "object_id"},
    "file.diff": {"action", "path", "content", "expected_hash"},
    "file.write": {"action", "path", "content", "expected_hash"},
    "file.patch": {"action", "path", "old", "new", "expected_hash"},
    "bookmark.add": {
        "action", "reference", "object_id", "path", "label", "note",
        "heading", "chunk", "cursor",
    },
    "bookmark.list": {"action", "limit"},
    "bookmark.open": {"action", "bookmark_id", "max_tokens"},
    "receipt.list": {"action", "limit", "pinned_only", "turn_id"},
    "receipt.inspect": {"action", "receipt_id", "reference"},
    "activity.status": {"action", "activity_id"},
    "activity.note": {"action", "activity_id", "note"},
    "curation.list": {"action", "limit"},
    "curation.inspect": {"action", "batch_id", "reference"},
    "curation.history": {"action", "batch_id", "reference"},
    "memory.search": {"action", "query", "limit"},
    "memory.read": {"action", "memory_id"},
    "memory.history": {"action", "memory_id"},
    "memory.provenance": {"action", "memory_id"},
    "memory.queue_for_review": {"action", "memory_id"},
    "note.append": {"action", "content", "reason"},
    "note.read": {"action", "note_id"},
    "note.search": {"action", "query", "limit"},
}


HOUSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS house_documents (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    document_type TEXT NOT NULL,
    heading_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS house_chunks (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    heading TEXT,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (path) REFERENCES house_documents(path) ON DELETE CASCADE,
    UNIQUE(path, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS house_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    path,
    heading,
    content,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS house_cursors (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    path TEXT NOT NULL,
    next_chunk INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS house_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_notes (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS resident_notes_fts USING fts5(
    note_id UNINDEXED,
    content,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS identity_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_hash TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS resident_tool_drafts (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    name TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS resident_tools (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(resident_id, name)
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

CREATE TABLE IF NOT EXISTS attention_tray_items (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    reference TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    position INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_sessions (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    query TEXT NOT NULL,
    scope TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    results_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_session_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    query TEXT NOT NULL,
    scope TEXT NOT NULL,
    results_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES search_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_attention_tray_active
ON attention_tray_items(resident_id, room_id, status, position);
CREATE INDEX IF NOT EXISTS idx_search_sessions_active
ON search_sessions(resident_id, room_id, status, updated_at);
"""


class HousePort:
    """Bounded local reading and low-authority resident tools.

    This port deliberately has no shell, network, secret, raw-database, or arbitrary
    filesystem operation. Calls are accepted only when the runtime has extracted them
    from an authenticated provider response.
    """

    def __init__(
        self,
        config: ResolvedConfig,
        db: ContinuityDB,
        *,
        queue_for_review: Callable[[dict[str, Any]], str] | None = None,
        open_curation: Callable[..., dict[str, Any] | None] | None = None,
        image_service: ImageService | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.home = config.home_path.resolve()
        self.resident_id = str(config.get("resident.id"))
        self.room_id = str(config.get("room.id"))
        self.counter = TokenCounter(str(config.get("models.default")))
        self.queue_for_review = queue_for_review
        self.open_curation = open_curation
        self.images = image_service
        self.legible = LegibleLedger(config, db)
        self.registry = CapabilityRegistry(config)
        with self.db.connect() as connection:
            connection.executescript(HOUSE_SCHEMA)
            identity_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(identity_drafts)"
                ).fetchall()
            }
            if "author" not in identity_columns:
                connection.execute(
                    "ALTER TABLE identity_drafts ADD COLUMN author TEXT "
                    "NOT NULL DEFAULT 'resident'"
                )
            if "source_json" not in identity_columns:
                connection.execute(
                    "ALTER TABLE identity_drafts ADD COLUMN source_json TEXT "
                    "NOT NULL DEFAULT '{}'"
                )
            if "conflicts_json" not in identity_columns:
                connection.execute(
                    "ALTER TABLE identity_drafts ADD COLUMN conflicts_json TEXT "
                    "NOT NULL DEFAULT '[]'"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO resident_jobs
                (id, resident_id, kind, status, config_json, updated_at)
                VALUES (?, ?, 'house_index', 'active', '{}', ?)
                """,
                (new_id("job"), self.resident_id, utc_now_iso()),
            )
        self._install_capabilities()
        (self.home / "workspace").mkdir(parents=True, exist_ok=True)

    # ---------- index and path boundary ----------

    def refresh_index(self) -> dict[str, int]:
        seen: set[str] = set()
        indexed = 0
        unchanged = 0
        skipped = 0
        maximum = int(self.config.get("house.max_file_bytes", 5_000_000))
        for path in self._iter_readable_files():
            relative = path.relative_to(self.home).as_posix()
            seen.add(relative)
            stat = path.stat()
            if stat.st_size > maximum:
                skipped += 1
                continue
            with self.db.connect() as connection:
                old = connection.execute(
                    "SELECT content_hash, size_bytes, mtime_ns FROM house_documents WHERE path=?",
                    (relative,),
                ).fetchone()
            if (
                old
                and int(old["size_bytes"]) == stat.st_size
                and int(old["mtime_ns"]) == stat.st_mtime_ns
            ):
                unchanged += 1
                continue
            text = self._read_index_text(path, relative)
            digest = sha256_text(text)
            if old and str(old["content_hash"]) == digest:
                with self.db.connect() as connection:
                    connection.execute(
                        "UPDATE house_documents SET size_bytes=?, mtime_ns=?, indexed_at=? WHERE path=?",
                        (stat.st_size, stat.st_mtime_ns, utc_now_iso(), relative),
                    )
                unchanged += 1
                continue
            chunks = self._chunk_document(relative, text)
            with self.db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = connection.execute(
                    "SELECT id FROM house_chunks WHERE path=?", (relative,)
                ).fetchall()
                for row in prior:
                    connection.execute(
                        "DELETE FROM house_chunks_fts WHERE chunk_id=?", (str(row["id"]),)
                    )
                connection.execute("DELETE FROM house_chunks WHERE path=?", (relative,))
                connection.execute(
                    """
                    INSERT INTO house_documents
                    (path, content_hash, size_bytes, mtime_ns, document_type,
                     heading_count, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      content_hash=excluded.content_hash,
                      size_bytes=excluded.size_bytes,
                      mtime_ns=excluded.mtime_ns,
                      document_type=excluded.document_type,
                      heading_count=excluded.heading_count,
                      indexed_at=excluded.indexed_at
                    """,
                    (
                        relative,
                        digest,
                        stat.st_size,
                        stat.st_mtime_ns,
                        path.suffix.lower().lstrip(".") or "text",
                        len({heading for heading, _ in chunks if heading}),
                        utc_now_iso(),
                    ),
                )
                for index, (heading, content) in enumerate(chunks):
                    chunk_id = "hchunk_" + sha256_text(f"{relative}\0{index}\0{content}")[:24]
                    connection.execute(
                        """
                        INSERT INTO house_chunks
                        (id, path, heading, chunk_index, content, content_hash)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (chunk_id, relative, heading, index, content, sha256_text(content)),
                    )
                    connection.execute(
                        """
                        INSERT INTO house_chunks_fts(chunk_id, path, heading, content)
                        VALUES (?, ?, ?, ?)
                        """,
                        (chunk_id, relative, heading or "", content),
                    )
            indexed += 1
        with self.db.connect() as connection:
            stale = connection.execute("SELECT path FROM house_documents").fetchall()
            for row in stale:
                relative = str(row["path"])
                if relative in seen:
                    continue
                ids = connection.execute(
                    "SELECT id FROM house_chunks WHERE path=?", (relative,)
                ).fetchall()
                for item in ids:
                    connection.execute(
                        "DELETE FROM house_chunks_fts WHERE chunk_id=?", (str(item["id"]),)
                    )
                connection.execute("DELETE FROM house_documents WHERE path=?", (relative,))
        self._sync_objects()
        self._write_home_index()
        return {"indexed": indexed, "unchanged": unchanged, "skipped": skipped}

    def _write_home_index(self) -> None:
        """Maintain the resident-readable front door from verified house state."""

        file_rows: list[str] = []
        for path in self._iter_readable_files():
            relative = path.relative_to(self.home).as_posix()
            if relative == "index.md":
                continue
            stat = path.stat()
            obj = self.legible.object_by_reference(relative)
            file_rows.append(
                "| `{}` | document | `{}` | {} | {} | resident home |".format(
                    relative,
                    str(obj["id"]) if obj else "not-yet-indexed",
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                )
            )
        image_rows: list[str] = []
        if self.images is not None:
            reverse_shelves = {
                actual: alias for alias, actual in VIRTUAL_IMAGE_SHELVES.items()
            }
            for item in self.images.history(limit=100):
                locator = str(item["path"])
                for actual, alias in reverse_shelves.items():
                    if locator == actual or locator.startswith(actual + "/"):
                        locator = alias + locator[len(actual) :]
                        break
                image_rows.append(
                    "| `{}` | `{}` | {}×{} | {} | {} | {} |".format(
                        locator,
                        item["id"],
                        item.get("width") or "?",
                        item.get("height") or "?",
                        item.get("created_at") or "unknown",
                        item.get("source_kind") or "unknown",
                        item.get("privacy") or "private",
                    )
                )
        generated_at = utc_now_iso()
        text = (
            "# Resident Home Index\n\n"
            "> Automatically generated from verified house state. This page is a map, "
            "not authority: presence does not imply adoption, review, sharing, or delivery.\n\n"
            f"Updated: `{generated_at}`\n\n"
            "Evidence vocabulary: participant-supplied locators remain unverified until "
            "resolved; an indexed path is verified present; a read or pixel inspection "
            "requires its own receipt.\n\n"
            "## Accessible files\n\n"
            "| House path | Type | Stable ID | Bytes | Modified (UTC) | Provenance |\n"
            "|---|---|---:|---:|---|---|\n"
            + ("\n".join(file_rows) if file_rows else "| *(none)* | | | | | |\n")
            + "\n\n## Image artifacts\n\n"
            "| House path | Image ID | Dimensions | Created | Provenance | Privacy |\n"
            "|---|---|---:|---|---|---|\n"
            + ("\n".join(image_rows) if image_rows else "| *(none)* | | | | | |\n")
            + "\n\n## Useful doors\n\n"
            "- `object.list` / `object.search` — current verified registry\n"
            "- `object.inspect` / `image.inspect` — read text or pixels\n"
            "- `receipt.list` / `receipt.inspect` — action evidence\n"
            "- `bookmark.add` / `bookmark.open` — saved reading positions\n"
            "- `image.drawer` — searchable picture cards, aliases, notes, and pockets\n"
            "- `image.share` — resident-controlled quick-draw plus optional high assurance\n"
            "- `attention.tray` — temporary resident-selected working context\n"
            "- `search.session` / `retrieval.inspect` — durable search and visible attention\n"
        )
        path = self.home / "index.md"
        # Avoid replacing the file merely because the generation timestamp changed.
        comparable = re.sub(r"Updated: `[^`]+`", "Updated: `<time>`", text)
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        current_comparable = re.sub(r"Updated: `[^`]+`", "Updated: `<time>`", current)
        if comparable != current_comparable:
            atomic_write_text(path, text)
        digest = sha256_text(path.read_text(encoding="utf-8"))
        self.legible.register_object(
            object_type="document",
            locator="index.md",
            content_hash=digest,
            evidence_state="verified_now",
            metadata={
                "document_type": "md",
                "generated": True,
                "updated_at": generated_at,
            },
            provenance={"source": "house_object_registry", "automatic": True},
        )

    def _sync_objects(self) -> None:
        with self.db.connect() as connection:
            documents = connection.execute(
                "SELECT * FROM house_documents ORDER BY path"
            ).fetchall()
        folder_locators: set[str] = {
            "identity",
            "imports",
            "sessions",
            "scrapbook",
            "artifacts",
            "exports",
            "workspace",
            *VIRTUAL_IMAGE_SHELVES.values(),
        }
        for row in documents:
            parts = Path(str(row["path"])).parts[:-1]
            for index in range(1, len(parts) + 1):
                folder_locators.add(Path(*parts[:index]).as_posix())
            self.legible.register_object(
                object_type="document",
                locator=str(row["path"]),
                content_hash=str(row["content_hash"]),
                metadata={
                    "size_bytes": int(row["size_bytes"]),
                    "document_type": str(row["document_type"]),
                    "heading_count": int(row["heading_count"]),
                    "indexed_at": str(row["indexed_at"]),
                },
                provenance={
                    "source": "resident_home",
                    "verified_path": str(row["path"]),
                },
            )
        if self.images is not None:
            for item in self.images.history(limit=100):
                card = self.images.card(str(item["id"]))
                parts = Path(str(item["path"])).parts[:-1]
                for index in range(1, len(parts) + 1):
                    folder_locators.add(Path(*parts[:index]).as_posix())
                self.legible.register_object(
                    object_type="image",
                    locator=str(item["path"]),
                    content_hash=str(item["content_hash"]),
                    evidence_state=(
                        "verified_now"
                        if (self.home / str(item["path"])).is_file()
                        else "unavailable_now"
                    ),
                    metadata={
                        "image_id": str(item["id"]),
                        "original_filename": item.get("original_filename"),
                        "media_type": item.get("media_type"),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "source_kind": item.get("source_kind"),
                        "created_at": item.get("created_at"),
                        "status": item.get("status"),
                        "alias": card.get("alias"),
                        "summary": card.get("summary"),
                        "pockets": card.get("pockets", []),
                        "adoption_state": card.get("adoption_state"),
                        "privacy": card.get("privacy"),
                    },
                    provenance={
                        "source": item.get("source_kind"),
                        "details": item.get("source", {}),
                    },
                    preferred_id=str(item["id"]),
                )
        for locator in sorted(folder_locators):
            path = (self.home / locator).resolve()
            self.legible.register_object(
                object_type="folder",
                locator=locator,
                evidence_state="verified_now" if path.is_dir() else "unavailable_now",
                metadata={"writable": locator == "workspace"},
                provenance={"source": "configured_virtual_shelf"},
            )
        with self.db.connect() as connection:
            memories = (
                connection.execute(
                    """
                    WITH latest AS (
                        SELECT record_id, status,
                               ROW_NUMBER() OVER (
                                 PARTITION BY record_id ORDER BY rowid DESC
                               ) AS rn
                        FROM memory_events
                    )
                    SELECT m.id, m.content_hash, m.memory_type, m.tier,
                           m.authority_state, COALESCE(latest.status, 'candidate') AS status,
                           m.source_id, m.created_at
                    FROM memory_records m
                    LEFT JOIN latest ON latest.record_id=m.id AND latest.rn=1
                    WHERE m.resident_id=? ORDER BY m.rowid DESC LIMIT 300
                    """,
                    (self.resident_id,),
                ).fetchall()
                if self._table_exists(connection, "memory_records")
                else []
            )
            notes = (
                connection.execute(
                    "SELECT id, content_hash, status, source_json, created_at "
                    "FROM resident_notes WHERE resident_id=? "
                    "ORDER BY rowid DESC LIMIT 300",
                    (self.resident_id,),
                ).fetchall()
                if self._table_exists(connection, "resident_notes")
                else []
            )
            batches = (
                connection.execute(
                    "SELECT id, trigger_reason, status, created_at, resolved_at "
                    "FROM curation_batches WHERE resident_id=? "
                    "ORDER BY rowid DESC LIMIT 200",
                    (self.resident_id,),
                ).fetchall()
                if self._table_exists(connection, "curation_batches")
                else []
            )
            jobs = connection.execute(
                "SELECT id, kind, status, updated_at FROM resident_jobs "
                "WHERE resident_id=? ORDER BY rowid DESC",
                (self.resident_id,),
            ).fetchall()
        for row in memories:
            self.legible.register_object(
                object_type="memory",
                locator=f"memories/{row['id']}",
                content_hash=str(row["content_hash"]),
                metadata={
                    "memory_id": str(row["id"]),
                    "type": str(row["memory_type"]),
                    "tier": str(row["tier"]),
                    "authority": str(row["authority_state"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                },
                provenance={"source_id": row["source_id"]},
                preferred_id=str(row["id"]),
            )
        for row in notes:
            self.legible.register_object(
                object_type="note",
                locator=f"notes/{row['id']}",
                content_hash=str(row["content_hash"]),
                metadata={
                    "note_id": str(row["id"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                },
                provenance=json.loads(str(row["source_json"]) or "{}"),
                preferred_id=str(row["id"]),
            )
        for row in batches:
            self.legible.register_object(
                object_type="curation_batch",
                locator=f"curation/{row['id']}",
                metadata={
                    "batch_id": str(row["id"]),
                    "trigger": str(row["trigger_reason"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                    "resolved_at": row["resolved_at"],
                },
                provenance={"source": "curation_room"},
                preferred_id=str(row["id"]),
            )
        for row in jobs:
            self.legible.register_object(
                object_type="job",
                locator=f"jobs/{row['id']}",
                metadata={
                    "job_id": str(row["id"]),
                    "kind": str(row["kind"]),
                    "status": str(row["status"]),
                    "updated_at": str(row["updated_at"]),
                },
                provenance={"source": "resident_job_registry"},
                preferred_id=str(row["id"]),
            )

    def _normalize_house_locator(self, raw: str) -> str:
        candidate = str(raw).strip().replace("\\", "/")
        if candidate.startswith("house://"):
            candidate = candidate[8:]
            if candidate.startswith(self.resident_id + "/"):
                candidate = candidate[len(self.resident_id) + 1 :]
        candidate = candidate.strip("/")
        for alias, actual in VIRTUAL_IMAGE_SHELVES.items():
            if candidate == alias or candidate.startswith(alias + "/"):
                candidate = actual + candidate[len(alias) :]
                break
        if candidate == "scratch" or candidate.startswith("scratch/"):
            candidate = "workspace" + candidate[len("scratch") :]
        return candidate

    def _iter_readable_files(self):
        for special in ("index.md", "runtime_contract.md", "home.yaml"):
            path = self.home / special
            if path.is_file() and not path.is_symlink():
                yield path
        roots = tuple(self.config.get("house.accessible_roots", list(DEFAULT_ROOTS)))
        for root_name in roots:
            if str(root_name) not in DEFAULT_ROOTS:
                continue
            root = self.home / str(root_name)
            if not root.is_dir() or root.is_symlink():
                continue
            for path in sorted(root.rglob("*")):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.suffix.lower() in TEXT_SUFFIXES
                    and not any(part.startswith(".") for part in path.relative_to(self.home).parts)
                ):
                    relative = path.relative_to(self.home).as_posix()
                    if relative == "memory/continuity.db" or relative.startswith("traces/"):
                        continue
                    yield path

    def _resolve_readable(self, raw: str) -> tuple[Path, str]:
        candidate = self._normalize_house_locator(raw)
        if not candidate or candidate.startswith("/") or "\x00" in candidate:
            raise PermissionError("house paths must be non-empty relative paths")
        if any(part in {"", ".", ".."} for part in candidate.split("/")):
            raise PermissionError("path traversal is not available through the house port")
        unresolved = self.home / candidate
        if unresolved.is_symlink():
            raise PermissionError("symbolic-link traversal is not available")
        cursor = unresolved.parent
        while cursor != self.home and cursor != cursor.parent:
            if cursor.is_symlink():
                raise PermissionError("symbolic-link traversal is not available")
            cursor = cursor.parent
        path = unresolved.resolve()
        try:
            relative = path.relative_to(self.home).as_posix()
        except ValueError as exc:
            raise PermissionError("path leaves the resident house") from exc
        if relative not in {"index.md", "runtime_contract.md", "home.yaml"}:
            root = relative.split("/", 1)[0]
            configured_roots = {
                str(item)
                for item in self.config.get("house.accessible_roots", list(DEFAULT_ROOTS))
                if str(item) in DEFAULT_ROOTS
            }
            if root not in configured_roots:
                raise PermissionError("that shelf is not exposed through the reading port")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            raise FileNotFoundError(f"readable house document not found: {candidate}")
        return path, relative

    def _resolve_writable(self, raw: str, *, may_not_exist: bool = False) -> tuple[Path, str]:
        candidate = self._normalize_house_locator(raw)
        if not candidate or candidate.startswith("/") or "\x00" in candidate:
            raise PermissionError("workspace paths must be non-empty relative paths")
        if any(
            part in {"", ".", ".."} or part.startswith(".")
            for part in candidate.split("/")
        ):
            raise PermissionError("unsafe workspace path")
        root = candidate.split("/", 1)[0]
        writable = {
            str(item)
            for item in self.config.get("house.writable_roots", ["workspace"])
            if str(item) == "workspace"
        }
        if root not in writable:
            raise PermissionError(
                "immediate file edits are confined to the resident workspace shelf"
            )
        if Path(candidate).suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("the resident editor supports bounded text files only")
        unresolved = self.home / candidate
        cursor = unresolved.parent
        while cursor != self.home and cursor != cursor.parent:
            if cursor.is_symlink():
                raise PermissionError("symbolic-link traversal is not available")
            cursor = cursor.parent
        path = unresolved.resolve()
        try:
            path.relative_to((self.home / "workspace").resolve())
        except ValueError as exc:
            raise PermissionError("workspace path leaves the writable shelf") from exc
        if path.exists() and (not path.is_file() or path.is_symlink()):
            raise PermissionError("workspace target is not a regular text file")
        if not may_not_exist and not path.is_file():
            raise FileNotFoundError(candidate)
        return path, candidate

    @staticmethod
    def _redact_yaml(text: str) -> str:
        try:
            value = yaml.safe_load(text)
        except Exception:
            return "[home.yaml could not be safely rendered]"

        sensitive = re.compile(r"(?i)(token|secret|password|api[_-]?key|credential)")

        def clean(item: Any) -> Any:
            if isinstance(item, dict):
                return {
                    str(key): ("[redacted]" if sensitive.search(str(key)) else clean(child))
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [clean(child) for child in item]
            return item

        return yaml.safe_dump(clean(value), sort_keys=False, allow_unicode=True)

    def _read_index_text(self, path: Path, relative: str) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        return self._redact_yaml(text) if relative == "home.yaml" else text

    def _chunk_document(self, relative: str, text: str) -> list[tuple[str | None, str]]:
        maximum = max(1000, int(self.config.get("house.chunk_chars", 6000)))
        heading: str | None = None
        blocks: list[tuple[str | None, str]] = []
        current: list[str] = []
        current_size = 0
        for line in text.splitlines(keepends=True):
            match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
            if match and current:
                blocks.append((heading, "".join(current).strip()))
                current, current_size = [], 0
            if match:
                heading = match.group(1).strip()
            if current and current_size + len(line) > maximum:
                blocks.append((heading, "".join(current).strip()))
                current, current_size = [], 0
            while len(line) > maximum:
                take, line = line[:maximum], line[maximum:]
                if current:
                    blocks.append((heading, "".join(current).strip()))
                    current, current_size = [], 0
                blocks.append((heading, take.strip()))
            current.append(line)
            current_size += len(line)
        if current or not blocks:
            blocks.append((heading, "".join(current).strip()))
        return [(item_heading, item) for item_heading, item in blocks if item]

    # ---------- resident-facing dispatch ----------

    def _install_capabilities(self) -> None:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list": self._list,
            "search": self._search,
            "read": self._read,
            "continue": self._continue,
            "stat": self._stat,
            "bookmark": self._bookmark,
            "object.list": self._object_list,
            "object.search": self._object_search,
            "object.stat": self._object_stat,
            "object.inspect": self._object_inspect,
            "object.history": self._object_history,
            "object.provenance": self._object_provenance,
            "file.write": self._file_write,
            "file.patch": self._file_patch,
            "file.diff": self._file_diff,
            "bookmark.add": self._bookmark_add,
            "bookmark.list": self._bookmark_list,
            "bookmark.open": self._bookmark_open,
            "bookmark.remove": self._bookmark_remove,
            "receipt.list": self._receipt_list,
            "receipt.inspect": self._receipt_inspect,
            "receipt.pin": self._receipt_pin,
            "receipt.unpin": self._receipt_unpin,
            "activity.status": self._activity_status,
            "activity.note": self._activity_note,
            "identity.history": self._identity_history,
            "identity.compare": self._identity_compare,
            "identity.provenance": self._identity_provenance,
            "attention.tray": self._attention_tray,
            "search.session": self._search_session,
            "retrieval.inspect": self._retrieval_inspect,
            "next_step": self._next_step,
            "context.control": self._context_control,
            "source.visibility": self._source_visibility,
            "capabilities": self._capabilities,
            "help": self._help,
            "pending": self._pending,
            "status": self._status,
            "memory.search": self._memory_search,
            "memory.read": self._memory_read,
            "memory.history": self._memory_history,
            "memory.provenance": self._memory_provenance,
            "memory.queue_for_review": self._memory_queue,
            "note.append": self._note_append,
            "note.read": self._note_read,
            "note.search": self._note_search,
            "note.release": self._note_release,
            "jobs.list": self._jobs_list,
            "jobs.inspect": self._jobs_inspect,
            "jobs.create": self._jobs_create,
            "jobs.step": self._jobs_step,
            "jobs.chalkboard": self._jobs_chalkboard,
            "jobs.receipts": self._jobs_receipts,
            "jobs.pause": self._jobs_change,
            "jobs.resume": self._jobs_change,
            "jobs.cancel": self._jobs_change,
            "curation.review_now": self._curation_review_now,
            "curation.configure": self._curation_configure,
            "curation.reflections": self._curation_reflections,
            "curation.list": self._curation_list,
            "curation.inspect": self._curation_inspect,
            "curation.history": self._curation_history,
            "tool.run": self._tool_run,
        }
        read_only = {
            "list", "search", "read", "continue", "stat",
            "object.list", "object.search", "object.stat", "object.inspect",
            "object.history", "object.provenance", "bookmark.list", "bookmark.open",
            "receipt.list", "receipt.inspect", "activity.status",
            "curation.list", "curation.inspect", "curation.history",
            "identity.history", "identity.compare", "identity.provenance",
            "retrieval.inspect",
            "jobs.receipts",
        }
        memory_read = {
            "memory.search",
            "memory.read",
            "memory.history",
            "memory.provenance",
        }
        low_authority = {
            "bookmark",
            "memory.queue_for_review",
            "note.append",
            "note.read",
            "note.search",
            "note.release",
            "bookmark.add",
            "bookmark.remove",
            "receipt.pin",
            "receipt.unpin",
            "activity.note",
            "jobs.create",
            "jobs.step",
            "jobs.chalkboard",
            "attention.tray",
            "search.session",
            "context.control",
            "source.visibility",
        }
        inspection = {
            "capabilities",
            "help",
            "pending",
            "status",
            "jobs.list",
                "jobs.inspect",
                "jobs.receipts",
        }
        for name, handler in handlers.items():
            effects = ("filesystem:read",) if name in read_only else ("database:read",)
            if name in low_authority:
                effects = ("database:write_low_authority",)
            if name in {"file.write", "file.patch"}:
                effects = ("filesystem:write_workspace", "database:audit_write")
            if name == "file.diff":
                effects = ("filesystem:read",)
            if name in {"jobs.pause", "jobs.resume", "jobs.cancel", "curation.configure"}:
                effects = ("database:control",)
            if name in {"jobs.create", "jobs.step", "jobs.chalkboard"}:
                effects = ("database:write_low_authority",)
            if name == "tool.run":
                effects = ("composed_existing_capabilities",)
            description = {
                "list": "List readable documents on an exposed house shelf.",
                "search": "Search indexed house documents locally.",
                "read": "Read a bounded cited excerpt from a house document.",
                "continue": "Continue a prior bounded house read from its cursor.",
                "stat": "Inspect safe metadata for one readable house document.",
                "bookmark": "Place a cited excerpt into the curation review queue.",
                "object.list": "List typed objects across bounded virtual house shelves.",
                "object.search": "Search documents, images, notes, and object metadata.",
                "object.stat": "Verify one supplied locator or stable object reference.",
                "object.inspect": "Inspect one stable object through its type-appropriate route.",
                "object.history": "Inspect append-only events for one stable object.",
                "object.provenance": "Show what a stable object is actually from.",
                "file.write": "Create or save a bounded text file on the resident workspace shelf.",
                "file.patch": "Apply an exact hash-aware text replacement on the workspace shelf.",
                "file.diff": "Preview a proposed workspace text edit without writing it.",
                "bookmark.add": "Save a durable place in a document or house object.",
                "bookmark.list": "List active durable bookmarks.",
                "bookmark.open": "Open the object and position named by a bookmark.",
                "bookmark.remove": "Remove a bookmark without deleting its target.",
                "receipt.list": "List durable action receipts, including pinned rollover evidence.",
                "receipt.inspect": "Inspect the full result and routing metadata of one receipt.",
                "receipt.pin": "Pin a compact receipt into future rollover context.",
                "receipt.unpin": "Stop carrying a receipt into future rollover context.",
                "activity.status": "Inspect honest current or recent private work activity.",
                "activity.note": "Write a short resident-authored chalkboard status note.",
                "identity.history": "Inspect identity proposals, claims, rejections, and preserved versions.",
                "identity.compare": "Compare current identity text with a prior or pending revision.",
                "identity.provenance": "Inspect authorship, authority, and revision lineage for identity text.",
                "attention.tray": "Keep resident-selected references close as temporary working context.",
                "search.session": "Start, refine, inspect, or close a durable scoped search desk.",
                "retrieval.inspect": "Explain what continuity crossed into a turn and why.",
                "next_step": "Explain the next safe or required move for one receipt, draft, job, bell, object, or action.",
                "context.control": "Inspect or arrange the resident's prompt and transcript drawers.",
                "source.visibility": "Choose which authorized Discord history is visible as ambient context.",
                "curation.list": "List curation batches and their explicit states.",
                "curation.inspect": "Inspect one curation batch, its selected evidence, and drafts.",
                "curation.history": "Inspect append-only events for one curation batch.",
                "jobs.create": "Create a bounded private task with an explicit action allowlist.",
                "jobs.step": "Run one allowlisted operation inside a bounded private task.",
                "jobs.chalkboard": "Update a job-scoped what-I-was-doing card.",
                "jobs.receipts": "List durable receipts associated with a private task.",
                "capabilities": "Inspect the executable live capability registry.",
                "help": "Read resident-facing capability syntax and boundaries.",
                "pending": "Inspect unresolved resident drafts and outward actions.",
                "status": "Inspect safe house status and local index state.",
                "tool.run": "Run a claimed declarative tool within its inherited powers.",
            }.get(name, f"Resident capability {name}.")
            self.registry.register(
                CapabilitySpec(
                    name=name,
                    description=description,
                    effects=effects,
                    cost_class="free",
                    confirmation="none",
                    default_after="continue",
                    result_visibility="resident_private",
                    config_key=(
                        "forge.enabled"
                        if name == "tool.run"
                        else "curation.enabled"
                        if name.startswith("curation.")
                        else "house.enabled"
                    ),
                    forgeable=name in FORGE_STEP_ACTIONS,
                    **contract_for(name),
                ),
                lambda payload, _context, target=handler: target(
                    {key: value for key, value in payload.items() if key != "after"}
                ),
            )
        reaction_contract = contract_for("discord.react")
        self.registry.register(
            CapabilitySpec(
                name="discord.react",
                description="Add or remove the resident's emoji reaction on a visible Discord message.",
                effects=("outward_reaction",),
                confirmation="resident_authenticated_doorway",
                default_after="finish",
                result_visibility="resident_private_then_current_doorway",
                outward_facing=True,
                invocation_envelope="REACT",
                **reaction_contract,
            ),
            self._discord_react,
            authorizer=self._discord_react_authorizer,
        )
        if self.images is not None:
            image_specs = (
                CapabilitySpec(
                    name="image.inspect",
                    description="Read a stored image with cached local OCR and/or configured vision.",
                    effects=("filesystem:read", "database:cache_write", "network:conditional"),
                    cost_class="free_or_metered",
                    confirmation="none",
                    default_after="continue",
                    result_visibility="resident_private",
                    input_schema={
                        "image_id": "required",
                        "question": "optional",
                        "routes": "ocr|vision_low|vision_high",
                    },
                ),
                CapabilitySpec(
                    name="image.generate",
                    description="Create private images with visual-canon context and configured brakes.",
                    effects=("network:metered", "filesystem:write", "database:write"),
                    cost_class="metered",
                    confirmation="configured_budget",
                    default_after="continue",
                    result_visibility="resident_private",
                    config_key="images.enabled",
                    input_schema={"prompt": "required", "count": "1..configured maximum"},
                ),
                CapabilitySpec(
                    name="image.edit",
                    description="Create private edits from stored image references.",
                    effects=("network:metered", "filesystem:write", "database:write"),
                    cost_class="metered",
                    confirmation="configured_budget",
                    default_after="continue",
                    result_visibility="resident_private",
                    config_key="images.edits_enabled",
                    input_schema={"prompt": "required", "image_ids": "non-empty list"},
                ),
                CapabilitySpec(
                    name="image.history",
                    description="Inspect the resident-visible image shelf and provenance.",
                    effects=("database:read",),
                    cost_class="free",
                    confirmation="none",
                    default_after="continue",
                    result_visibility="resident_private",
                ),
                CapabilitySpec(
                    name="image.drawer",
                    description=(
                        "Browse, search, name, annotate, summarize, pocket, or inspect "
                        "the timeline of resident-owned image memory cards."
                    ),
                    effects=("database:read_write_low_authority", "network:conditional"),
                    cost_class="free_or_metered",
                    confirmation="none",
                    default_after="continue",
                    result_visibility="resident_private",
                    schema_version="v1",
                    input_schema={
                        "mode": "browse|search|get|update|summarize|pocket|timeline",
                        "image_id": "required except browse/search",
                        "query": "required for search",
                        "changes": "resident-owned card fields for update",
                        "pocket": "collection name for pocket mode",
                        "present": "true to add; false to remove",
                        "inspect_if_missing": "may spend a configured vision call",
                    },
                    example_envelopes=(
                        {
                            "action": "image.drawer",
                            "mode": "search",
                            "query": "smug neon mall reaction",
                            "after": "continue",
                        },
                        {
                            "action": "image.drawer",
                            "mode": "update",
                            "image_id": "img_...",
                            "changes": {
                                "alias": "lipstick-attack",
                                "privacy": "shareable",
                                "uses": ["affectionate ambush"],
                            },
                            "after": "continue",
                        },
                    ),
                ),
                CapabilitySpec(
                    name="image.review",
                    description="Apply a resident review state to a stored image.",
                    effects=("database:write_low_authority",),
                    cost_class="free",
                    confirmation="none",
                    default_after="continue",
                    result_visibility="resident_private",
                ),
                CapabilitySpec(
                    name="image.share",
                    description=(
                        "Quick-draw a picture through the current authenticated doorway, "
                        "or use the legacy previewable hash-bound handoff."
                    ),
                    effects=("outward_attachment",),
                    cost_class="free",
                    confirmation="resident_only_if_private_or_legacy_two_breath",
                    default_after="continue",
                    result_visibility="resident_private_then_current_doorway",
                    schema_version="v2",
                    outward_facing=True,
                    input_schema={
                        "mode": "send|preview|prepare|claim|reject",
                        "image_id": "required for send or preview/prepare without draft_id",
                        "confirm": "required only for private send or legacy claim",
                        "reason": "optional resident purpose",
                        "draft_id": "legacy high-assurance route",
                        "expected_hash": "legacy high-assurance route",
                        "after": "continue|finish",
                    },
                    example_envelopes=(
                        {
                            "action": "image.share",
                            "schema_version": "v2",
                            "mode": "send",
                            "image_id": "img_...",
                            "after": "finish",
                        },
                        {
                            "action": "image.share",
                            "schema_version": "v2",
                            "mode": "send",
                            "image_id": "img_private_...",
                            "confirm": True,
                            "after": "finish",
                        },
                        {
                            "action": "image.share",
                            "schema_version": "v1",
                            "mode": "prepare",
                            "image_id": "img_...",
                            "reason": "high-assurance handoff",
                            "after": "continue",
                        },
                    ),
                ),
            )
            image_handlers = {
                "image.inspect": self._image_inspect,
                "image.generate": self._image_generate,
                "image.edit": self._image_edit,
                "image.history": self._image_history,
                "image.drawer": self._image_drawer,
                "image.review": self._image_review,
                "image.share": self._image_share,
            }
            image_authorizers = {
                "image.share": self._image_share_authorizer,
                "image.generate": self._image_generate_authorizer,
                "image.edit": self._image_edit_authorizer,
            }
            for spec in image_specs:
                contract = contract_for(spec.name)
                authorizer = image_authorizers.get(spec.name)
                self.registry.register(
                    CapabilitySpec(
                        **{
                            **asdict(spec),
                            **contract,
                        }
                    ),
                    image_handlers[spec.name],
                    authorizer=authorizer,
                )
        bell_draft, bell_control = bell_contracts()
        self.registry.register_contract(
            CapabilitySpec(
                name="bell.draft",
                description="Preview a resident-authored scheduled invitation before hash-bound claim.",
                effects=("database:write_pending_draft",),
                confirmation="later_resident_hash_bound_claim",
                result_visibility="resident_private",
                config_key="bells.enabled",
                invocation_envelope="BELL_DRAFT",
                dispatchable_via_tool_action=False,
                next_step="Copy the returned draft_id and expected_hash into BELL_CONTROL action:claim.",
                **bell_draft,
            )
        )
        self.registry.register_contract(
            CapabilitySpec(
                name="bell.control",
                description="Claim/reject a bell draft or pause, resume, revise, defer, or delete an existing bell.",
                effects=("database:control",),
                confirmation="hash_bound_for_claim",
                result_visibility="resident_private",
                config_key="bells.enabled",
                invocation_envelope="BELL_CONTROL",
                dispatchable_via_tool_action=False,
                next_step="Inspect the returned bell state; no further action is required unless another revision is wanted.",
                **bell_control,
            )
        )

    def dispatch(
        self,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not bool(self.config.get("house.enabled", True)):
            raise PermissionError("the resident house port is paused")
        if not isinstance(payload, dict):
            raise ValueError("tool payload must be an object")
        action = str(payload.get("action", "")).strip().lower()
        clean = dict(payload)
        clean["action"] = action
        execution_context = dict(context or {})
        source_envelope = str(
            execution_context.get("source_envelope") or "TOOL_ACTION"
        ).upper()
        started_at = utc_now_iso()
        target = {
            key: payload[key]
            for key in (
                "path", "scope", "reference", "object_id", "image_id", "memory_id",
                "note_id", "receipt_id", "bookmark_id", "batch_id", "job_id",
                "draft_id", "bell_id", "action_name", "target",
            )
            if payload.get(key) is not None
        }
        try:
            result, spec, after = self.registry.dispatch(
                clean,
                turn_id=turn_id,
                context=execution_context,
            )
            response = {
                "ok": True,
                "action": action,
                "after": after,
                "routing": {
                    "source_envelope": source_envelope,
                    "normalized_envelope": "TOOL_ACTION",
                    "adapter_version": "v0.5",
                    "translation_loss": False,
                },
                "capability": {
                    "schema_version": spec.schema_version,
                    "cost_class": spec.cost_class,
                    "confirmation": spec.confirmation,
                    "result_visibility": spec.result_visibility,
                    "outward_facing": spec.outward_facing,
                },
                **result,
            }
            if action in {
                "image.generate",
                "image.edit",
                "image.drawer",
                "image.review",
                "image.share",
            }:
                try:
                    response["index_update"] = {
                        "status": "succeeded",
                        **self.refresh_index(),
                    }
                except Exception as index_exc:
                    # The primary action remains truthful and durable even if its
                    # derived front-door document needs repair on the next refresh.
                    response["index_update"] = {
                        "status": "failed",
                        "error_type": type(index_exc).__name__,
                        "error": str(index_exc)[:300],
                    }
            receipt_id = self.legible.record_receipt(
                action=action,
                status="succeeded",
                result=response,
                turn_id=turn_id,
                source_envelope=source_envelope,
                target=target,
                outward_effect=(
                    "current_authenticated_doorway"
                    if any(effect.startswith("outward") for effect in spec.effects)
                    else "none"
                ),
                started_at=started_at,
            )
            response["receipt_id"] = receipt_id
            self._event(
                action,
                str(payload.get("path") or payload.get("memory_id") or ""),
                "ok",
                response,
            )
            return response
        except Exception as exc:
            failure_result = {
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            if getattr(exc, "house_error_code", None):
                failure_result["error_code"] = exc.house_error_code
            if getattr(exc, "house_suggested_retry", None):
                failure_result["suggested_retry"] = exc.house_suggested_retry
            if action == "image.share":
                failure_result["outward_action"] = False
                failure_result["invariant"] = "No outward action occurred."
            receipt_id = self.legible.record_receipt(
                action=action or "(missing)",
                status="failed",
                result=failure_result,
                turn_id=turn_id,
                source_envelope=source_envelope,
                target=target,
                started_at=started_at,
            )
            try:
                setattr(exc, "house_receipt_id", receipt_id)
            except Exception:
                pass
            self._event(action, str(target), "failed", {"receipt_id": receipt_id})
            raise

    def _list(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        scope = self._normalize_house_locator(str(payload.get("scope", "")))
        limit = min(200, max(1, int(payload.get("limit", 50))))
        with self.db.connect() as connection:
            if scope:
                rows = connection.execute(
                    """
                    SELECT * FROM house_documents WHERE path LIKE ?
                    ORDER BY path LIMIT ?
                    """,
                    (scope + "/%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM house_documents ORDER BY path LIMIT ?", (limit,)
                ).fetchall()
        return {
            "documents": [
                {
                    "path": str(row["path"]),
                    "size_bytes": int(row["size_bytes"]),
                    "content_hash": str(row["content_hash"]),
                    "heading_count": int(row["heading_count"]),
                    "object_id": (
                        self.legible.object_by_reference(str(row["path"])) or {}
                    ).get("id"),
                    "evidence_state": "verified_now",
                }
                for row in rows
            ]
        }

    @staticmethod
    def _fts_query(raw: str) -> str:
        words = re.findall(r"[\w-]{2,}", raw, flags=re.UNICODE)
        if not words:
            raise ValueError("search query must contain searchable words")
        return " AND ".join(f'"{word.replace(chr(34), "")}"' for word in words[:12])

    def _search(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        query = str(payload.get("query", "")).strip()
        limit = min(20, max(1, int(payload.get("max_results", 8))))
        scope = self._normalize_house_locator(str(payload.get("scope", "")))
        fts = self._fts_query(query)
        sql = """
            SELECT c.path, c.heading, c.chunk_index, c.content,
                   d.content_hash, bm25(house_chunks_fts) AS rank
            FROM house_chunks_fts
            JOIN house_chunks c ON c.id=house_chunks_fts.chunk_id
            JOIN house_documents d ON d.path=c.path
            WHERE house_chunks_fts MATCH ?
        """
        params: list[Any] = [fts]
        if scope:
            sql += " AND c.path LIKE ?"
            params.append(scope + "/%")
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            with self.db.connect() as connection:
                rows = connection.execute(sql, params).fetchall()
        except Exception:
            # FTS syntax availability is verified at home initialization; return no
            # match rather than broadening a malformed query into arbitrary SQL.
            rows = []
        return {
            "query": query,
            "results": [
                {
                    "path": str(row["path"]),
                    "heading": row["heading"],
                    "chunk_index": int(row["chunk_index"]),
                    "excerpt": self.counter.trim(
                        " ".join(str(row["content"]).split()), 220
                    ),
                    "file_hash": str(row["content_hash"]),
                    "object_id": (
                        self.legible.object_by_reference(str(row["path"])) or {}
                    ).get("id"),
                    "evidence_state": "verified_now",
                }
                for row in rows
            ],
        }

    def _read(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        bookmark_location: dict[str, Any] = {}
        supplied = str(payload.get("path") or payload.get("reference") or "")
        if payload.get("bookmark_id"):
            bookmark = self.legible.bookmark(str(payload["bookmark_id"]))
            supplied = str(bookmark["object_id"])
            bookmark_location = bookmark.get("location", {})
        referenced = self.legible.object_by_reference(supplied)
        _, relative = self._resolve_readable(
            str(referenced["locator"]) if referenced else supplied
        )
        heading = str(
            payload.get("heading", bookmark_location.get("heading", ""))
        ).strip()
        start = max(
            0, int(payload.get("chunk", bookmark_location.get("chunk", 0)))
        )
        maximum = min(
            int(self.config.get("house.max_result_tokens", 4000)),
            max(100, int(payload.get("max_tokens", 3000))),
        )
        with self.db.connect() as connection:
            document = connection.execute(
                "SELECT * FROM house_documents WHERE path=?", (relative,)
            ).fetchone()
            if heading:
                selected = connection.execute(
                    """
                    SELECT * FROM house_chunks
                    WHERE path=? AND heading LIKE ?
                    ORDER BY chunk_index
                    """,
                    (relative, f"%{heading}%"),
                ).fetchall()
            else:
                selected = connection.execute(
                    """
                    SELECT * FROM house_chunks
                    WHERE path=? AND chunk_index>=?
                    ORDER BY chunk_index
                    """,
                    (relative, start),
                ).fetchall()
        if not document or not selected:
            raise LookupError("no matching readable chunk")
        text_parts: list[str] = []
        used = 0
        last_index = start - 1
        for row in selected:
            content = str(row["content"])
            remaining = maximum - used
            if remaining <= 0:
                break
            trimmed = self.counter.trim(content, remaining)
            if not trimmed:
                break
            label = f"[{relative} · {row['heading'] or 'unheaded'} · chunk {row['chunk_index']}]"
            block = label + "\n" + trimmed
            text_parts.append(block)
            used += self.counter.count(block)
            last_index = int(row["chunk_index"])
            if trimmed != content:
                break
        with self.db.connect() as connection:
            more = connection.execute(
                "SELECT 1 FROM house_chunks WHERE path=? AND chunk_index>? LIMIT 1",
                (relative, last_index),
            ).fetchone()
        cursor = None
        if more:
            cursor = new_id("house_cursor")
            now = datetime.now(UTC)
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO house_cursors
                    (id, resident_id, path, next_chunk, status, created_at, expires_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        cursor,
                        self.resident_id,
                        relative,
                        last_index + 1,
                        now.isoformat(),
                        (now + timedelta(days=7)).isoformat(),
                    ),
                )
        house_object = self.legible.object_by_reference(relative)
        return {
            "citation": f"house://{self.resident_id}/{relative}",
            "path": relative,
            "object_id": house_object["id"] if house_object else None,
            "file_hash": str(document["content_hash"]),
            "text": "\n\n".join(text_parts),
            "cursor": cursor,
            "more": bool(more),
            "evidence": {
                "participant_supplied_locator": supplied or None,
                "verified_locator": relative,
                "verified": True,
                "state": "verified_now",
                "action": "read",
                "verified_at": utc_now_iso(),
            },
        }

    def _continue(self, payload: dict[str, Any]) -> dict[str, Any]:
        cursor_id = str(payload.get("cursor", "")).strip()
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM house_cursors WHERE id=? AND resident_id=?",
                (cursor_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "active":
            raise HouseCursorError(
                "unknown or closed house cursor",
                code="cursor_unknown_or_closed",
                suggested_retry={
                    "action": "read",
                    "instruction": "Open the source again to obtain a fresh cursor.",
                },
            )
        if datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(UTC):
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE house_cursors SET status='expired' WHERE id=?", (cursor_id,)
                )
            raise HouseCursorExpiredError(
                "house cursor expired",
                code="cursor_expired",
                suggested_retry={
                    "action": "read",
                    "path": str(row["path"]),
                    "chunk": int(row["next_chunk"]),
                },
            )
        result = self._read(
            {
                "path": str(row["path"]),
                "chunk": int(row["next_chunk"]),
                "max_tokens": payload.get("max_tokens", 3000),
            }
        )
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE house_cursors SET status='consumed' WHERE id=?", (cursor_id,)
            )
        return result

    def _stat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        supplied = str(payload.get("path") or payload.get("reference") or "")
        referenced = self.legible.object_by_reference(supplied)
        _, relative = self._resolve_readable(
            str(referenced["locator"]) if referenced else supplied
        )
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM house_documents WHERE path=?", (relative,)
            ).fetchone()
            headings = connection.execute(
                """
                SELECT DISTINCT heading FROM house_chunks
                WHERE path=? AND heading IS NOT NULL ORDER BY chunk_index LIMIT 100
                """,
                (relative,),
            ).fetchall()
            chunks = connection.execute(
                "SELECT COUNT(*) AS n FROM house_chunks WHERE path=?", (relative,)
            ).fetchone()
        if not row:
            raise FileNotFoundError(relative)
        house_object = self.legible.object_by_reference(relative)
        return {
            "path": relative,
            "object_id": house_object["id"] if house_object else None,
            "file_hash": str(row["content_hash"]),
            "size_bytes": int(row["size_bytes"]),
            "chunks": int(chunks["n"]),
            "headings": [str(item["heading"]) for item in headings],
            "evidence": {
                "participant_supplied_locator": supplied or None,
                "verified_locator": relative,
                "verified": True,
                "state": "verified_now",
            },
        }

    def _bookmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        read = self._read(payload)
        content = self.counter.trim(str(read["text"]), 1200)
        note_id = self._create_note(
            content,
            source={
                "kind": "house_bookmark",
                "path": read["path"],
                "file_hash": read["file_hash"],
                "citation": read["citation"],
            },
        )
        queue_id = None
        if self.queue_for_review:
            queue_id = self.queue_for_review(
                {
                    "kind": "document_excerpt",
                    "source_id": note_id,
                    "content": content,
                    "provenance": {
                        "path": read["path"],
                        "file_hash": read["file_hash"],
                    },
                }
            )
        return {"note_id": note_id, "queue_id": queue_id, "citation": read["citation"]}

    # ---------- unified house objects, editor, bookmarks, and receipts ----------

    def _object_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        scope = self._normalize_house_locator(str(payload.get("scope", "")))
        return {
            "objects": self.legible.list_objects(
                scope=scope,
                object_type=str(payload.get("type", "")).strip(),
                limit=int(payload.get("limit", 100)),
            ),
            "shelves": {
                "house://index.md": {
                    "writable": False,
                    "generated": True,
                    "role": "resident home front door",
                },
                "house://identity/": {"writable": False, "revision_lane": "identity_draft"},
                "house://imports/": {"writable": False},
                "house://images/originals/": {"writable": False},
                "house://images/generated/": {"writable": False},
                "house://images/shelf/": {"writable": False},
                "house://images/edits/": {"writable": False},
                "house://workspace/": {"writable": True, "authority": "low"},
                "house://receipts/": {"writable": False},
            },
        }

    def _object_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        raw_query = str(payload.get("query", "")).strip()
        if not raw_query:
            raise ValueError("object.search requires a query")
        limit = min(50, max(1, int(payload.get("limit", 15))))
        scope = self._normalize_house_locator(str(payload.get("scope", "")))
        matches: list[dict[str, Any]] = []
        try:
            document_results = self._search(
                {
                    "query": raw_query,
                    "scope": scope,
                    "max_results": limit,
                }
            )["results"]
        except ValueError:
            document_results = []
        for item in document_results:
            obj = self.legible.object_by_reference(str(item["path"]))
            if obj:
                matches.append(
                    {
                        "object": obj,
                        "match_route": "document_fts",
                        "excerpt": item["excerpt"],
                        "heading": item["heading"],
                    }
                )
        like = "%" + raw_query.lower() + "%"
        with self.db.connect() as connection:
            object_rows = connection.execute(
                """
                SELECT * FROM house_objects
                WHERE resident_id=?
                  AND (?='' OR locator LIKE ?)
                  AND (
                    lower(locator) LIKE ?
                    OR lower(metadata_json) LIKE ?
                    OR lower(provenance_json) LIKE ?
                  )
                ORDER BY updated_at DESC LIMIT ?
                """,
                (
                    self.resident_id,
                    scope,
                    scope.rstrip("/") + "%",
                    like,
                    like,
                    like,
                    limit,
                ),
            ).fetchall()
            interpretation_rows = (
                connection.execute(
                    """
                    SELECT i.image_id, i.route, i.result_text, i.created_at
                    FROM image_interpretations i
                    WHERE i.resident_id=? AND lower(i.result_text) LIKE ?
                    ORDER BY i.rowid DESC LIMIT ?
                    """,
                    (self.resident_id, like, limit),
                ).fetchall()
                if self._table_exists(connection, "image_interpretations")
                else []
            )
        seen = {str(item["object"]["id"]) for item in matches}
        for row in object_rows:
            obj = self.legible.object_by_reference(str(row["id"]))
            if obj and str(obj["id"]) not in seen:
                matches.append({"object": obj, "match_route": "object_metadata"})
                seen.add(str(obj["id"]))
        for row in interpretation_rows:
            obj = self.legible.object_by_reference(str(row["image_id"]))
            if obj and str(obj["id"]) not in seen:
                matches.append(
                    {
                        "object": obj,
                        "match_route": f"image_interpretation:{row['route']}",
                        "excerpt": self.counter.trim(str(row["result_text"]), 220),
                    }
                )
                seen.add(str(obj["id"]))
        if self.images is not None:
            for card in self.images.search_cards(raw_query, limit=limit):
                obj = self.legible.object_by_reference(str(card["image_id"]))
                if obj and str(obj["id"]) not in seen:
                    matches.append(
                        {
                            "object": obj,
                            "match_route": "picture_drawer_card",
                            "excerpt": self.counter.trim(
                                " · ".join(
                                    item
                                    for item in (
                                        card.get("alias"),
                                        card.get("summary"),
                                        card.get("resident_note"),
                                    )
                                    if item
                                ),
                                220,
                            ),
                            "picture_card": {
                                key: card.get(key)
                                for key in (
                                    "image_id",
                                    "alias",
                                    "privacy",
                                    "pockets",
                                    "adoption_state",
                                )
                            },
                        }
                    )
                    seen.add(str(obj["id"]))
        return {"query": raw_query, "results": matches[:limit]}

    # ---------- resident-controlled attention and progressive search ----------

    def _attention_tray(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "list").strip().lower()
        now = datetime.now(UTC)
        if mode == "list":
            with self.db.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM attention_tray_items
                    WHERE resident_id=? AND room_id=? AND status='active'
                      AND (expires_at IS NULL OR expires_at>?)
                    ORDER BY position, rowid
                    """,
                    (self.resident_id, self.room_id, now.isoformat()),
                ).fetchall()
            return {
                "mode": mode,
                "items": [self._attention_row(row) for row in rows],
                "context_behavior": (
                    "Active tray cards are offered as temporary working context. "
                    "They are not memory, identity, adoption, or assent."
                ),
            }
        if mode == "add":
            reference = str(
                payload.get("reference")
                or payload.get("object_id")
                or payload.get("image_id")
                or payload.get("memory_id")
                or payload.get("path")
                or ""
            ).strip()
            if not reference:
                raise ValueError("attention.tray add requires a verified reference")
            obj = self._resolve_object({"reference": reference})
            content = self._attention_content(obj)
            hours = min(
                168,
                max(1, int(payload.get("hours", self.config.get("house.attention_tray_hours", 24)))),
            )
            expires_at = (now + timedelta(hours=hours)).isoformat()
            with self.db.connect() as connection:
                position = connection.execute(
                    """
                    SELECT COALESCE(MAX(position), -1) + 1 AS next_position
                    FROM attention_tray_items
                    WHERE resident_id=? AND room_id=? AND status='active'
                    """,
                    (self.resident_id, self.room_id),
                ).fetchone()
                item_id = new_id("tray")
                connection.execute(
                    """
                    INSERT INTO attention_tray_items
                    (id, resident_id, room_id, reference, label, note, content,
                     content_hash, status, position, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        self.resident_id,
                        self.room_id,
                        str(obj["id"]),
                        str(payload.get("label") or "").strip(),
                        str(payload.get("note") or "").strip(),
                        content,
                        sha256_text(content),
                        int(position["next_position"]),
                        expires_at,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM attention_tray_items WHERE id=?", (item_id,)
                ).fetchone()
            return {
                "mode": mode,
                "item": self._attention_row(row),
                "next_action": "continue working, add another reference, or clear later",
            }
        if mode in {"remove", "clear"}:
            with self.db.connect() as connection:
                if mode == "remove":
                    item_id = str(payload.get("item_id") or "").strip()
                    if not item_id:
                        raise ValueError("attention.tray remove requires item_id")
                    cursor = connection.execute(
                        """
                        UPDATE attention_tray_items SET status='cleared', updated_at=?
                        WHERE id=? AND resident_id=? AND room_id=? AND status='active'
                        """,
                        (now.isoformat(), item_id, self.resident_id, self.room_id),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE attention_tray_items SET status='cleared', updated_at=?
                        WHERE resident_id=? AND room_id=? AND status='active'
                        """,
                        (now.isoformat(), self.resident_id, self.room_id),
                    )
            return {
                "mode": mode,
                "cleared": int(cursor.rowcount),
                "deleted": False,
                "authority_change": False,
            }
        raise ValueError("attention.tray mode must be list, add, remove, or clear")

    @staticmethod
    def _attention_row(row: Any) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "id",
                "reference",
                "label",
                "note",
                "content_hash",
                "position",
                "expires_at",
                "created_at",
                "updated_at",
            )
        } | {"excerpt": str(row["content"])[:1200]}

    def _attention_content(self, obj: dict[str, Any]) -> str:
        kind = str(obj["object_type"])
        if kind == "document":
            return str(
                self._read(
                    {"path": obj["locator"], "chunk": 0, "max_tokens": 700}
                )["text"]
            )
        if kind == "image" and self.images is not None:
            card = self.images.card(str(obj["id"]))
            return stable_json(
                {
                    "type": "picture_card",
                    "image_id": card["image_id"],
                    "alias": card["alias"],
                    "summary": card["summary"],
                    "resident_note": card["resident_note"],
                    "adoption_state": card["adoption_state"],
                    "privacy": card["privacy"],
                }
            )
        if kind == "memory":
            return stable_json(self._memory_read({"memory_id": obj["id"]})["memory"])
        if kind == "note":
            return stable_json(self._note_read({"note_id": obj["id"]}))
        return stable_json(
            {
                "object_id": obj["id"],
                "object_type": kind,
                "locator": obj["locator"],
                "metadata": obj["metadata"],
                "provenance": obj["provenance"],
            }
        )

    def _search_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "start").strip().lower()
        now = datetime.now(UTC)
        session_id = str(payload.get("session_id") or "").strip()
        if mode in {"inspect", "close"}:
            row = self._search_session_row(session_id)
            if mode == "close":
                with self.db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE search_sessions SET status='closed', updated_at=?
                        WHERE id=? AND resident_id=? AND status='active'
                        """,
                        (now.isoformat(), session_id, self.resident_id),
                    )
                return {
                    "mode": mode,
                    "session_id": session_id,
                    "status": "closed",
                    "results_preserved": True,
                }
            return self._search_session_view(row, mode=mode)
        if mode not in {"start", "refine"}:
            raise ValueError("search.session mode must be start, refine, inspect, or close")
        prior = self._search_session_row(session_id) if mode == "refine" else None
        query = str(payload.get("query") or (prior["query"] if prior else "")).strip()
        if not query:
            raise ValueError("search.session requires a query")
        scope = str(payload.get("scope") or (prior["scope"] if prior else "everything"))
        scope = scope.strip().lower().replace(" ", "_")
        if scope not in {
            "everything",
            "pictures",
            "scrolls",
            "memories",
            "recent_conversation",
        }:
            raise ValueError(
                "search scope must be everything, pictures, scrolls, memories, "
                "or recent_conversation"
            )
        limit = min(20, max(1, int(payload.get("limit", 6))))
        cards = self._progressive_search_cards(query, scope=scope, limit=limit)
        expires_at = (now + timedelta(days=7)).isoformat()
        if prior:
            actual_id = str(prior["id"])
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE search_sessions
                    SET query=?, scope=?, filters_json=?, results_json=?,
                        updated_at=?, expires_at=?
                    WHERE id=? AND resident_id=? AND status='active'
                    """,
                    (
                        query,
                        scope,
                        stable_json({"limit": limit}),
                        stable_json(cards),
                        now.isoformat(),
                        expires_at,
                        actual_id,
                        self.resident_id,
                    ),
                )
        else:
            actual_id = new_id("search")
            with self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO search_sessions
                    (id, resident_id, room_id, query, scope, filters_json,
                     results_json, status, created_at, updated_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        actual_id,
                        self.resident_id,
                        self.room_id,
                        query,
                        scope,
                        stable_json({"limit": limit}),
                        stable_json(cards),
                        now.isoformat(),
                        now.isoformat(),
                        expires_at,
                    ),
                )
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO search_session_events
                (id, session_id, event_type, query, scope, results_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("search_event"),
                    actual_id,
                    mode,
                    query,
                    scope,
                    stable_json(cards),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM search_sessions WHERE id=?", (actual_id,)
            ).fetchone()
        return self._search_session_view(row, mode=mode)

    def _search_session_row(self, session_id: str) -> Any:
        if not session_id:
            raise ValueError("search session_id is required")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM search_sessions
                WHERE id=? AND resident_id=? AND room_id=?
                """,
                (session_id, self.resident_id, self.room_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown search session")
        if str(row["status"]) != "active":
            raise ValueError("search session is closed")
        if str(row["expires_at"]) <= datetime.now(UTC).isoformat():
            raise HouseCursorExpiredError(
                "search session expired",
                code="search_session_expired",
                suggested_retry={
                    "action": "search.session",
                    "mode": "start",
                    "query": str(row["query"]),
                    "scope": str(row["scope"]),
                },
            )
        return row

    @staticmethod
    def _search_session_view(row: Any, *, mode: str) -> dict[str, Any]:
        cards = json.loads(str(row["results_json"]) or "[]")
        return {
            "mode": mode,
            "session_id": str(row["id"]),
            "status": str(row["status"]),
            "query": str(row["query"]),
            "scope": str(row["scope"]),
            "cards": cards,
            "result_count": len(cards),
            "expires_at": str(row["expires_at"]),
            "next_actions": [
                "inspect a card with its open_with envelope",
                "refine this session",
                "place a selected reference on attention.tray",
                "close the session",
            ],
        }

    def _progressive_search_cards(
        self, query: str, *, scope: str, limit: int
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        if scope in {"everything", "pictures"} and self.images is not None:
            for item in self.images.search_cards(query, limit=limit):
                reference = str(item["image_id"])
                cards.append(
                    {
                        "reference": reference,
                        "type": "picture",
                        "title": item["alias"] or item["original_filename"] or reference,
                        "excerpt": self.counter.trim(
                            item["summary"] or item["resident_note"] or "Undescribed picture.",
                            180,
                        ),
                        "authority": item["adoption_state"],
                        "privacy": item["privacy"],
                        "why_shown": "picture drawer card matched",
                        "open_with": {
                            "action": "image.drawer",
                            "mode": "get",
                            "image_id": reference,
                            "after": "continue",
                        },
                    }
                )
                seen.add(reference)
        if scope in {"everything", "memories"}:
            try:
                memories = self._memory_search({"query": query, "limit": limit})["results"]
            except Exception:
                memories = []
            for item in memories:
                reference = str(item["id"])
                if reference in seen:
                    continue
                cards.append(
                    {
                        "reference": reference,
                        "type": "memory",
                        "title": f"{item['type']} · {reference}",
                        "excerpt": item["content"],
                        "authority": item["authority"],
                        "status": item["status"],
                        "why_shown": "memory full-text match",
                        "open_with": {
                            "action": "memory.read",
                            "memory_id": reference,
                            "after": "continue",
                        },
                    }
                )
                seen.add(reference)
        if scope in {"everything", "scrolls"}:
            try:
                documents = self._search(
                    {"query": query, "max_results": limit}
                )["results"]
            except Exception:
                documents = []
            for item in documents:
                reference = str(item.get("object_id") or item["path"])
                if reference in seen:
                    continue
                cards.append(
                    {
                        "reference": reference,
                        "type": "scroll",
                        "title": str(item["heading"] or item["path"]),
                        "excerpt": item["excerpt"],
                        "authority": "source_material",
                        "why_shown": "document full-text match",
                        "open_with": {
                            "action": "read",
                            "path": item["path"],
                            "chunk": item["chunk_index"],
                            "after": "continue",
                        },
                    }
                )
                seen.add(reference)
        if scope == "recent_conversation":
            words = set(re.findall(r"\w{2,}", query.casefold()))
            for turn in self.db.recent_turns(self.resident_id, self.room_id, 100):
                content = str(turn["content"])
                overlap = words & set(re.findall(r"\w{2,}", content.casefold()))
                if not overlap:
                    continue
                reference = str(turn["id"])
                cards.append(
                    {
                        "reference": reference,
                        "type": "conversation_turn",
                        "title": f"{turn['speaker_role']} · {turn['created_at']}",
                        "excerpt": self.counter.trim(content, 180),
                        "authority": "verbatim_transcript",
                        "why_shown": f"matched: {', '.join(sorted(overlap)[:6])}",
                    }
                )
        return cards[:limit]

    def _retrieval_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        turn_id = str(payload.get("turn_id") or "").strip()
        if turn_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,160}", turn_id):
                raise ValueError("invalid turn_id")
            path = self.home / "traces" / f"{turn_id}.receipt.json"
        else:
            candidates = sorted(
                (self.home / "traces").glob("*.receipt.json"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            if not candidates:
                raise LookupError("no context receipt is available")
            path = candidates[0]
        if not path.is_file() or path.is_symlink():
            raise KeyError("unknown context receipt")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        return {
            "schema_version": "vestigia.retrieval-inspector.v0.6",
            "turn_id": receipt.get("turn_id"),
            "query_hash": receipt.get("current_message_hash"),
            "retrieved": receipt.get("retrieved_details", []),
            "layers": receipt.get("layers", []),
            "not_automatically_searched": [
                "arbitrary archive chunks",
                "private image pixels",
                "sealed records",
                "inherited-unreviewed material outside ORIENTATION",
            ],
            "causal_claim": (
                "This explains deterministic inclusion and omission. It does not "
                "prove which supplied text caused a model output."
            ),
        }

    def _next_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return operational guidance without requiring another registry crawl."""

        receipt_id = str(payload.get("receipt_id") or "").strip()
        reference = str(payload.get("reference") or "").strip()
        draft_id = str(payload.get("draft_id") or "").strip()
        job_id = str(payload.get("job_id") or "").strip()
        bell_id = str(payload.get("bell_id") or "").strip()
        action_name = str(payload.get("action_name") or "").strip().lower()
        supplied = [
            item
            for item in (receipt_id, reference, draft_id, job_id, bell_id, action_name)
            if item
        ]
        if len(supplied) != 1:
            raise ValueError(
                "next_step requires exactly one of receipt_id, reference, draft_id, "
                "job_id, bell_id, or action_name"
            )
        if action_name:
            contract = self.registry.describe(action_name)[0]
            return {
                "subject": {"kind": "capability", "id": action_name},
                "state": {
                    key: contract[key]
                    for key in (
                        "registered",
                        "enabled",
                        "schema_complete",
                        "callable_now",
                        "invocation_envelope",
                    )
                },
                "next_step": contract.get("next_step") or (
                    "Use one copyable example from this focused contract, replacing "
                    "only placeholder values."
                ),
                "copyable_examples": contract.get("copyable_examples", []),
                "related_actions": contract.get("related_actions", []),
            }
        if receipt_id:
            receipt = self.legible.inspect_receipt(receipt_id)
            result = receipt.get("result", {})
            suggested = result.get("suggested_retry") if isinstance(result, dict) else None
            if suggested:
                instruction = "Use the structured suggested_retry payload."
            elif receipt.get("status") == "failed":
                instruction = (
                    "Inspect the recorded error and choose a focused capability lookup "
                    "before retrying. The failed action did not acquire new authority."
                )
            else:
                instruction = (
                    "The action completed. No further step is required unless its result "
                    "contains a draft, continuation, pending delivery, or unresolved target."
                )
            self.legible.resolve_breadcrumb(receipt_id=receipt_id)
            return {
                "subject": {"kind": "receipt", "id": receipt_id},
                "state": {
                    "action": receipt.get("action"),
                    "status": receipt.get("status"),
                    "outward_effect": receipt.get("outward_effect"),
                },
                "next_step": instruction,
                "suggested_retry": suggested,
            }
        if job_id:
            job = self._jobs_inspect({"job_id": job_id})["job"]
            status = str(job.get("status", ""))
            mapping = {
                "active": "Use jobs.step for one allowlisted operation, or jobs.pause/cancel.",
                "paused": "Use jobs.resume to continue or jobs.cancel to close it.",
                "completed": "No further operation is required; inspect jobs.receipts if needed.",
                "cancelled": "This job is closed. Create a new job to resume the objective.",
                "expired": "This job expired. Create a new bounded job if the objective remains relevant.",
            }
            return {
                "subject": {"kind": "job", "id": job_id},
                "state": {"status": status},
                "next_step": mapping.get(status, "Inspect jobs.inspect before acting."),
            }
        if draft_id:
            if draft_id.startswith("bell_draft_"):
                return {
                    "subject": {"kind": "bell_draft", "id": draft_id},
                    "next_step": (
                        "Use BELL_CONTROL with this draft_id, action:\"claim\", and the "
                        "exact expected_hash returned by the preview; or action:\"reject\"."
                    ),
                    "focused_contract": "bell.control",
                }
            return {
                "subject": {"kind": "draft", "id": draft_id},
                "next_step": (
                    "Inspect pending for the draft type, then use that type's hash-bound "
                    "control envelope. Do not guess an expected hash."
                ),
            }
        if bell_id:
            return {
                "subject": {"kind": "bell", "id": bell_id},
                "next_step": (
                    "Use BELL_CONTROL with bell_id and one of pause, resume, defer, "
                    "revise, or delete. Request capabilities target:\"bell.control\" "
                    "for the exact envelope."
                ),
                "focused_contract": "bell.control",
            }
        obj = self._resolve_object({"reference": reference})
        return {
            "subject": {
                "kind": str(obj.get("object_type")),
                "id": str(obj.get("id")),
            },
            "next_step": (
                "Use object.inspect for contents, object.provenance for origin, "
                "bookmark.add to preserve a reading position, or attention.tray to "
                "keep it in temporary working context."
            ),
        }

    def _resolve_object(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_index()
        reference = str(
            payload.get("reference")
            or payload.get("object_id")
            or payload.get("path")
            or payload.get("image_id")
            or payload.get("memory_id")
            or payload.get("note_id")
            or payload.get("receipt_id")
            or ""
        ).strip()
        obj = self.legible.object_by_reference(reference)
        if not obj:
            normalized = self._normalize_house_locator(reference)
            obj = self.legible.object_by_reference(normalized)
        if not obj:
            raise KeyError("supplied locator has not been verified as a house object")
        return obj

    def _object_stat(self, payload: dict[str, Any]) -> dict[str, Any]:
        obj = self._resolve_object(payload)
        result: dict[str, Any] = {
            "object": obj,
            "verified": True,
            "evidence_action": "resolved_not_read",
        }
        if obj["object_type"] == "document":
            result["document"] = self._stat({"path": obj["locator"]})
        elif obj["object_type"] == "image" and self.images:
            result["image"] = self.images.get_asset(str(obj["id"]))
        return result

    def _object_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        obj = self._resolve_object(payload)
        kind = str(obj["object_type"])
        if kind == "document":
            result = self._read(
                {
                    "path": obj["locator"],
                    "heading": payload.get("heading", ""),
                    "chunk": payload.get("chunk", 0),
                    "max_tokens": payload.get("max_tokens", 3000),
                }
            )
        elif kind == "image":
            if payload.get("routes"):
                result = self._image_inspect(
                    {
                        "image_id": obj["id"],
                        "routes": payload.get("routes"),
                        "question": payload.get("question"),
                        "language": payload.get("language"),
                    },
                    {},
                )
            else:
                result = {
                    "image": self._require_images().get_asset(str(obj["id"])),
                    "pixel_access_run": False,
                    "instruction": (
                        "Pass routes ['ocr','vision_low'] or call image.inspect "
                        "to inspect pixels."
                    ),
                }
        elif kind == "memory":
            result = self._memory_read({"memory_id": obj["id"]})
        elif kind == "note":
            result = self._note_read({"note_id": obj["id"]})
        elif kind == "receipt":
            result = {"receipt": self.legible.inspect_receipt(str(obj["id"]))}
        else:
            result = {"metadata": obj}
        evidence_action = "read"
        if kind == "image":
            evidence_action = (
                "pixel_inspected" if payload.get("routes") else "resolved_not_read"
            )
        return {
            "object": obj,
            "inspection": result,
            "evidence_action": evidence_action,
        }

    def _object_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        obj = self._resolve_object(payload)
        events = self.legible.object_history(
            str(obj["id"]), limit=int(payload.get("limit", 100))
        )
        if obj["object_type"] == "memory":
            return {
                "object": obj,
                "object_events": events,
                **self._memory_history({"memory_id": obj["id"]}),
            }
        if obj["object_type"] == "image" and self.images:
            with self.db.connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM image_events WHERE image_id=? ORDER BY rowid",
                    (obj["id"],),
                ).fetchall()
            return {
                "object": obj,
                "object_events": events,
                "image_events": [dict(row) for row in rows],
            }
        return {"object": obj, "object_events": events}

    def _object_provenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        obj = self._resolve_object(payload)
        result = {
            "object_id": obj["id"],
            "object_type": obj["object_type"],
            "verified_locator": obj["locator"],
            "content_hash": obj["content_hash"],
            "evidence_state": obj["evidence_state"],
            "verified_at": obj["verified_at"],
            "provenance": obj["provenance"],
            "metadata": obj["metadata"],
        }
        if obj["object_type"] == "memory":
            result["memory_provenance"] = self._memory_provenance(
                {"memory_id": obj["id"]}
            )
        elif obj["object_type"] == "image" and self.images:
            result["image_record"] = self.images.get_asset(str(obj["id"]))
        return result

    def _workspace_content(self, payload: dict[str, Any]) -> tuple[Path, str, str, str]:
        path, relative = self._resolve_writable(
            str(payload.get("path", "")), may_not_exist=True
        )
        content = str(payload.get("content", ""))
        maximum = int(self.config.get("house.max_write_bytes", 1_000_000))
        if len(content.encode("utf-8")) > maximum:
            raise ValueError(f"workspace edit exceeds the configured {maximum}-byte ceiling")
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        expected = str(payload.get("expected_hash", "")).strip()
        if expected and expected != (sha256_text(old) if path.is_file() else ""):
            raise RuntimeError("workspace file changed after it was read; inspect and retry")
        return path, relative, old, content

    def _file_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, relative, old, content = self._workspace_content(payload)
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=relative + "@current",
                tofile=relative + "@proposed",
                lineterm="",
            )
        )
        return {
            "path": relative,
            "current_hash": sha256_text(old) if old else None,
            "proposed_hash": sha256_text(content),
            "diff": self.counter.trim(diff, 2400),
            "written": False,
        }

    def _file_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, relative, old, content = self._workspace_content(payload)
        event_id = new_id("workspace_edit")
        previous_path = None
        if old:
            previous_path = (
                self.home
                / "memory"
                / "workspace-versions"
                / f"{event_id}.previous{path.suffix.lower()}"
            )
            atomic_write_text(previous_path, old)
        atomic_write_text(path, content)
        self.refresh_index()
        obj = self.legible.object_by_reference(relative)
        if not obj:
            raise RuntimeError("saved file did not enter the house object registry")
        self.legible.object_event(
            str(obj["id"]),
            "workspace_saved",
            actor=f"resident:{self.resident_id}",
            payload={
                "previous_hash": sha256_text(old) if old else None,
                "content_hash": sha256_text(content),
                "previous_preserved": bool(previous_path),
            },
        )
        return {
            "object_id": obj["id"],
            "path": relative,
            "citation": f"house://{relative}",
            "content_hash": sha256_text(content),
            "previous_hash": sha256_text(old) if old else None,
            "previous_preserved": bool(previous_path),
            "written": True,
            "authority": "resident workspace; not identity or memory",
        }

    def _file_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._resolve_writable(str(payload.get("path", "")))
        current = path.read_text(encoding="utf-8")
        expected = str(payload.get("expected_hash", "")).strip()
        if expected and expected != sha256_text(current):
            raise RuntimeError("workspace file changed after it was read; inspect and retry")
        old = str(payload.get("old", ""))
        new = str(payload.get("new", ""))
        if not old:
            raise ValueError("file.patch requires non-empty exact old text")
        occurrences = current.count(old)
        if occurrences != 1:
            raise ValueError(
                f"exact patch target must occur once; found {occurrences} occurrences"
            )
        return self._file_write(
            {
                "path": relative,
                "content": current.replace(old, new, 1),
                "expected_hash": sha256_text(current),
            }
        )

    def _bookmark_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        obj = self._resolve_object(payload)
        bookmark_id = self.legible.add_bookmark(
            str(obj["id"]),
            label=str(payload.get("label", "")),
            note=str(payload.get("note", "")),
            location={
                key: payload[key]
                for key in ("heading", "chunk", "cursor")
                if payload.get(key) is not None
            },
        )
        return {"bookmark_id": bookmark_id, "object": obj}

    def _bookmark_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "bookmarks": self.legible.list_bookmarks(
                limit=int(payload.get("limit", 100))
            )
        }

    def _bookmark_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.legible.bookmark(str(payload.get("bookmark_id", "")))
        location = item.get("location", {})
        obj = self.legible.object_by_reference(str(item["object_id"]))
        if not obj:
            raise KeyError("bookmark target is unavailable")
        if obj["object_type"] == "document":
            opened = self._read(
                {
                    "path": obj["locator"],
                    "heading": location.get("heading", ""),
                    "chunk": location.get("chunk", 0),
                    "max_tokens": payload.get("max_tokens", 3000),
                }
            )
        else:
            opened = self._object_inspect({"reference": obj["id"]})
        return {"bookmark": item, "opened": opened}

    def _bookmark_remove(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.legible.remove_bookmark(str(payload.get("bookmark_id", "")))

    def _receipt_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "receipts": self.legible.list_receipts(
                limit=int(payload.get("limit", 20)),
                pinned_only=bool(payload.get("pinned_only", False)),
                turn_id=str(payload.get("turn_id", "")).strip() or None,
                action=str(payload.get("filter_action", "")).strip() or None,
                status=str(payload.get("status", "")).strip() or None,
                object_id=str(
                    payload.get("object_id") or payload.get("reference") or ""
                ).strip() or None,
            )
        }

    def _receipt_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        receipt_id = str(
            payload.get("receipt_id") or payload.get("reference") or ""
        )
        receipt = self.legible.inspect_receipt(receipt_id)
        self.legible.resolve_breadcrumb(receipt_id=receipt_id)
        return {"receipt": receipt, "breadcrumb_resolved": True}

    def _receipt_pin(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.legible.pin_receipt(str(payload.get("receipt_id", "")), True)

    def _receipt_unpin(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.legible.pin_receipt(str(payload.get("receipt_id", "")), False)

    def _activity_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        activity_id = str(payload.get("activity_id", "")).strip()
        activity = (
            self.legible.inspect_activity(activity_id)
            if activity_id
            else self.legible.latest_activity()
        )
        return {
            "activity": activity,
            "note_kind": "resident-authored status, not hidden chain-of-thought",
            "operation_confirmation": (
                "receipt_linked"
                if activity and activity.get("last_receipt_id")
                else "activity_reported_operation_unconfirmed"
            ),
        }

    def _activity_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        activity_id = str(payload.get("activity_id", "")).strip()
        if not activity_id:
            latest = self.legible.latest_activity(include_completed=False)
            if not latest:
                raise RuntimeError("there is no active private operation to annotate")
            activity_id = str(latest["id"])
        note = str(payload.get("note", "")).strip()
        if not note:
            raise ValueError("activity.note requires a short status note")
        if self.counter.count(note) > 240:
            raise ValueError("activity status notes may contain at most 240 tokens")
        self.legible.update_activity(activity_id, note=note)
        return {
            "activity_id": activity_id,
            "note": note,
            "visibility": "operator activity window and resident-private receipts",
            "not_chain_of_thought": True,
        }

    # ---------- memory views ----------

    def _memory_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        limit = min(30, max(1, int(payload.get("limit", 10))))
        with self.db.connect() as connection:
            matches = connection.execute(
                """
                SELECT m.id
                FROM memory_fts
                JOIN memory_records m ON m.id=memory_fts.record_id
                WHERE memory_fts MATCH ? AND m.resident_id=?
                ORDER BY bm25(memory_fts)
                LIMIT ?
                """,
                (self._fts_query(query), self.resident_id, limit),
            ).fetchall()
        rows = [self.db.get_memory(str(match["id"])) for match in matches]
        return {
            "results": [
                {
                    "id": item.id,
                    "type": item.memory_type,
                    "tier": item.tier,
                    "status": item.status,
                    "authority": item.authority_state,
                    "content": self.counter.trim(item.content, 240),
                }
                for item in rows
                if item is not None
            ]
        }

    def _memory_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(payload.get("memory_id", "")).strip()
        item = self.db.get_memory(memory_id)
        if item is None or item.resident_id != self.resident_id:
            raise KeyError("unknown resident memory")
        return {"memory": asdict(item)}

    def _memory_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(payload.get("memory_id", "")).strip()
        item = self.db.get_memory(memory_id)
        if item is None or item.resident_id != self.resident_id:
            raise KeyError("unknown resident memory")
        with self.db.connect() as connection:
            events = connection.execute(
                "SELECT * FROM memory_events WHERE record_id=? ORDER BY rowid",
                (memory_id,),
            ).fetchall()
            descendants = connection.execute(
                """
                SELECT id, content, created_at FROM memory_records
                WHERE supersedes_id=? ORDER BY rowid
                """,
                (memory_id,),
            ).fetchall()
        return {
            "memory_id": memory_id,
            "events": [dict(row) for row in events],
            "revisions": [dict(row) for row in descendants],
        }

    def _memory_provenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(payload.get("memory_id", "")).strip()
        item = self.db.get_memory(memory_id)
        if item is None or item.resident_id != self.resident_id:
            raise KeyError("unknown resident memory")
        turns = []
        ids = []
        if item.source_id:
            ids.append(item.source_id)
        ids.extend(str(value) for value in item.provenance.get("turn_ids", []))
        for turn_id in dict.fromkeys(ids):
            turn = self.db.get_turn(turn_id)
            if turn:
                turns.append(turn)
        return {
            "memory_id": memory_id,
            "source_id": item.source_id,
            "source_lineage_id": item.source_lineage_id,
            "independent_source_key": item.independent_source_key,
            "provenance": item.provenance,
            "source_turns": turns,
        }

    def _memory_queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(payload.get("memory_id", "")).strip()
        item = self.db.get_memory(memory_id)
        if item is None or item.resident_id != self.resident_id:
            raise KeyError("unknown resident memory")
        if not self.queue_for_review:
            raise RuntimeError("curation queue is unavailable")
        queue_id = self.queue_for_review(
            {
                "kind": "memory",
                "source_id": memory_id,
                "content": item.content,
                "provenance": {"requested_by": f"resident:{self.resident_id}"},
            }
        )
        return {"queue_id": queue_id, "memory_id": memory_id}

    # ---------- private notes ----------

    def _create_note(self, content: str, *, source: dict[str, Any] | None = None) -> str:
        clean = content.strip()
        if not clean:
            raise ValueError("note content may not be empty")
        if self.counter.count(clean) > 3000:
            raise ValueError("one note may contain at most 3000 tokens")
        note_id = new_id("note")
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO resident_notes
                (id, resident_id, content, content_hash, status, source_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, 'private', ?, ?, ?)
                """,
                (
                    note_id,
                    self.resident_id,
                    clean,
                    sha256_text(clean),
                    stable_json(source or {}),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO resident_notes_fts(note_id, content) VALUES (?, ?)",
                (note_id, clean),
            )
        return note_id

    def _note_append(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "note_id": self._create_note(
                str(payload.get("content", "")),
                source={"kind": "resident_authored", "reason": payload.get("reason", "")},
            ),
            "status": "private",
            "authority": "low-authority notebook; not memory or identity",
        }

    def _note_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        note_id = str(payload.get("note_id", "")).strip()
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resident_notes WHERE id=? AND resident_id=?",
                (note_id, self.resident_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown resident note")
        item = dict(row)
        item["source"] = json.loads(item.pop("source_json"))
        return {"note": item}

    def _note_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = self._fts_query(str(payload.get("query", "")))
        limit = min(20, max(1, int(payload.get("limit", 10))))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, bm25(resident_notes_fts) AS rank
                FROM resident_notes_fts
                JOIN resident_notes n ON n.id=resident_notes_fts.note_id
                WHERE resident_notes_fts MATCH ? AND n.resident_id=?
                ORDER BY rank LIMIT ?
                """,
                (query, self.resident_id, limit),
            ).fetchall()
        return {
            "results": [
                {
                    "id": str(row["id"]),
                    "status": str(row["status"]),
                    "content": self.counter.trim(str(row["content"]), 220),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]
        }

    def _note_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        note_id = str(payload.get("note_id", "")).strip()
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE resident_notes SET status='released', updated_at=?
                WHERE id=? AND resident_id=? AND status='private'
                """,
                (utc_now_iso(), note_id, self.resident_id),
            )
        if cursor.rowcount != 1:
            raise KeyError("unknown or already released note")
        return {"note_id": note_id, "status": "released", "memory_promotion": False}

    # ---------- inspection, jobs, and forge ----------

    def _capabilities(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = str(
            payload.get("target") or payload.get("tool") or ""
        ).strip().lower()
        focused = bool(target)
        if focused:
            described = self.registry.describe(target)
            self.legible.resolve_breadcrumb(unresolved_target=target)
            return {
                "schema_version": "vestigia.capability-registry.v0.6.1",
                "mode": "focused_contract",
                "focused": True,
                "target": target,
                "capability": described[0],
                "capabilities": described,
                "complete": True,
                "continuation": None,
                "next_normal_step": (
                    described[0].get("next_step")
                    or "Use one copyable example, replacing placeholder values only."
                ),
            }
        grouped = self.registry.grouped_index()
        group_names = sorted(grouped)
        digest = sha256_text(stable_json(group_names))[:12]
        cursor = str(payload.get("cursor") or "").strip()
        offset = 0
        if cursor:
            match = re.fullmatch(rf"cap_cursor_{digest}_(\d+)", cursor)
            if not match:
                raise HouseCursorError(
                    "unknown or stale capability continuation",
                    code="capability_cursor_stale",
                    suggested_retry={"action": "capabilities"},
                )
            offset = int(match.group(1))
        page_size = min(100, max(1, int(payload.get("page_size", 100))))
        selected_names = group_names[offset : offset + page_size]
        selected = {name: grouped[name] for name in selected_names}
        next_offset = offset + len(selected_names)
        continuation = (
            f"cap_cursor_{digest}_{next_offset}"
            if next_offset < len(group_names)
            else None
        )
        compact = [item for name in selected_names for item in selected[name]]
        return {
            "schema_version": "vestigia.capability-registry.v0.6.1",
            "mode": "compact_grouped_index",
            "focused": False,
            "target": None,
            "groups": selected,
            "capabilities": compact,
            "page": {
                "offset": offset,
                "page_size": page_size,
                "groups_returned": len(selected_names),
                "groups_total": len(group_names),
                "complete": continuation is None,
                "continuation": continuation,
            },
            "invocation": '[[TOOL_ACTION {"action":"...","after":"continue"}]]',
            "focused_lookup": (
                '[[TOOL_ACTION {"action":"capabilities","target":"action.name",'
                '"after":"continue"}]]'
            ),
            "legacy_invocation": {
                "accepted": True,
                "normalized_to": "TOOL_ACTION",
                "translation_is_visible_in_each_receipt": True,
            },
            "continuations": ["continue", "finish"],
            "private_turn_budget": self.private_turn_budget(),
            "forge": ["TOOL_DRAFT", "TOOL_CONTROL", "tool.run"],
            "two_breath": ["CURATION_DRAFT", "IDENTITY_DRAFT", "TOOL_DRAFT"],
            "writable_shelves": ["house://workspace/"],
            "lookup": 'capabilities(target:"action.name") returns one complete contract',
            "unavailable": [
                "shell",
                "filesystem outside configured virtual shelves",
                "raw sqlite",
                "credentials",
                "network",
                "outward action outside an authenticated doorway",
            ],
        }

    def private_turn_budget(self) -> dict[str, int]:
        new_source = self.config.sources.get("house.max_private_turns", "built-in")
        old_source = self.config.sources.get("house.max_tool_rounds", "built-in")
        if new_source != "built-in":
            private_turns = int(self.config.get("house.max_private_turns", 6))
        elif old_source != "built-in":
            private_turns = int(self.config.get("house.max_tool_rounds", 5)) + 1
        else:
            private_turns = int(self.config.get("house.max_private_turns", 6))
        return {
            "maximum_private_turns": max(1, private_turns),
            "maximum_tool_calls": max(
                1, int(self.config.get("house.max_tool_calls", 12))
            ),
            "maximum_result_tokens": max(
                500, int(self.config.get("house.max_result_tokens", 6000))
            ),
        }

    def _help(self, payload: dict[str, Any]) -> dict[str, Any]:
        topic = str(payload.get("topic") or payload.get("tool") or "").strip().lower()
        if topic:
            try:
                focused = self._capabilities({"target": topic})
                return {
                    "schema_version": "vestigia.help.v0.6.1",
                    "topic": topic,
                    "mode": "focused_contract",
                    "contract": focused["capability"],
                    "continuation": None,
                    "complete": True,
                }
            except ValueError:
                pass
        index = self._capabilities(
            {
                key: payload[key]
                for key in ("cursor", "page_size")
                if payload.get(key) is not None
            }
        )
        return {
            "schema_version": "vestigia.help.v0.6.1",
            "topic": topic or "house",
            "mode": "navigation_index",
            "syntax": (
                '[[TOOL_ACTION {"action":"capabilities","target":"search",'
                '"after":"continue"}]]'
            ),
            "note": (
                "This is a compact map, not the full registry. Ask for one action "
                "by name to receive its formal schema and copyable examples."
            ),
            "groups": {
                name: [item["name"] for item in items]
                for name, items in index["groups"].items()
            },
            "page": index["page"],
            "next_normal_step": (
                "Choose one handle and request capabilities with target set to that name."
            ),
        }

    def _pending(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as connection:
            identity = connection.execute(
                """
                SELECT id, path, payload_hash, created_at FROM identity_drafts
                WHERE resident_id=? AND status='pending' ORDER BY rowid
                """,
                (self.resident_id,),
            ).fetchall()
            tools = connection.execute(
                """
                SELECT id, name, payload_hash, created_at FROM resident_tool_drafts
                WHERE resident_id=? AND status='pending' ORDER BY rowid
                """,
                (self.resident_id,),
            ).fetchall()
            curation = connection.execute(
                """
                SELECT id, batch_id, payload_hash, created_at FROM curation_drafts
                WHERE resident_id=? AND status='pending' ORDER BY rowid
                """,
                (self.resident_id,),
            ).fetchall() if self._table_exists(connection, "curation_drafts") else []
        return {
            "identity_drafts": [dict(row) for row in identity],
            "tool_drafts": [dict(row) for row in tools],
            "curation_drafts": [dict(row) for row in curation],
            "image_share_drafts": self.images.pending_shares() if self.images else [],
            # Completed actions are not proposals and are deliberately kept in a
            # separate recovery lane. This lets a resident recover a receipt whose
            # conversational delivery was interrupted without implying the action
            # is still pending or that its result was reviewed.
            "recent_action_receipts": self.legible.list_receipts(limit=10),
        }

    def _status(self, payload: dict[str, Any]) -> dict[str, Any]:
        index = self.refresh_index()
        with self.db.connect() as connection:
            documents = connection.execute(
                "SELECT COUNT(*) AS n FROM house_documents"
            ).fetchone()
            notes = connection.execute(
                "SELECT COUNT(*) AS n FROM resident_notes WHERE resident_id=?",
                (self.resident_id,),
            ).fetchone()
            tools = connection.execute(
                """
                SELECT name, status FROM resident_tools
                WHERE resident_id=? ORDER BY name
                """,
                (self.resident_id,),
            ).fetchall()
        return {
            "resident_id": self.resident_id,
            "documents": int(documents["n"]),
            "notes": int(notes["n"]),
            "tools": [dict(row) for row in tools],
            "index_pass": index,
            "images": self.images.diagnostics() if self.images else {"available": False},
            "private_work_budget": self.private_turn_budget(),
            "bookmarks": len(self.legible.list_bookmarks(limit=200)),
            "pinned_receipts": len(
                self.legible.list_receipts(limit=200, pinned_only=True)
            ),
            "activity": self.legible.latest_activity(),
        }

    # ---------- image shelf, eyes, paintbox, and outward boundary ----------

    def _require_images(self) -> ImageService:
        if self.images is None:
            raise RuntimeError("image capabilities are unavailable")
        return self.images

    def _image_inspect(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        routes = payload.get("routes", ["ocr", "vision_low"])
        if isinstance(routes, str):
            routes = [item.strip() for item in routes.split(",") if item.strip()]
        if not isinstance(routes, list):
            raise ValueError("image.inspect routes must be a list")
        return self._require_images().inspect(
            str(payload.get("image_id") or payload.get("artifact_id") or ""),
            question=str(
                payload.get("question")
                or "Describe the image and anything important in it."
            ),
            routes=[str(item) for item in routes],
            language=str(
                payload.get("language")
                or self.config.get("images.ocr_language", "eng")
            ),
        )

    def _image_share_authorizer(
        self, spec: CapabilitySpec, payload: dict[str, Any], context: dict[str, Any]
    ) -> None:
        self._require_images().authorize_share(
            payload,
            turn_id=str(context.get("turn_id") or "") or None,
            interface=str(context.get("interface") or "") or None,
            participant_id=str(context.get("participant_id") or "") or None,
            delivery_target=context.get("delivery_target"),
        )

    def _image_generate_authorizer(
        self, spec: CapabilitySpec, payload: dict[str, Any], context: dict[str, Any]
    ) -> None:
        if bool(self.config.get("images.require_confirmation", False)):
            raise PermissionError(
                "this home requires the authenticated operator image doorway"
            )

    def _image_edit_authorizer(
        self, spec: CapabilitySpec, payload: dict[str, Any], context: dict[str, Any]
    ) -> None:
        if bool(self.config.get("images.require_confirmation", False)):
            raise PermissionError(
                "this home requires the authenticated operator image doorway"
            )

    def _image_generate(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("image.generate requires a prompt")
        if bool(payload.get("background", True)):
            return self._require_images().queue_job(
                "generate",
                payload,
                turn_id=str(context.get("turn_id") or "") or None,
                delivery=context.get("delivery_target")
                if isinstance(context.get("delivery_target"), dict)
                else None,
            )
        result = self._require_images().generate(
            prompt,
            count=int(payload.get("count", 1)),
            confirmed=False,
            turn_id=str(context.get("turn_id") or "") or None,
        )
        return {
            "operation": result.operation,
            "artifact_ids": list(result.artifact_ids),
            "image_ids": list(result.image_ids),
            "model": result.model,
            "privacy": "private",
            "publication": False,
        }

    def _image_edit(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = str(payload.get("prompt", "")).strip()
        image_ids = payload.get("image_ids")
        if not prompt:
            raise ValueError("image.edit requires a prompt")
        if not isinstance(image_ids, list) or not image_ids:
            raise ValueError("image.edit requires a non-empty image_ids list")
        if bool(payload.get("background", True)):
            return self._require_images().queue_job(
                "edit",
                payload,
                turn_id=str(context.get("turn_id") or "") or None,
                delivery=context.get("delivery_target")
                if isinstance(context.get("delivery_target"), dict)
                else None,
            )
        result = self._require_images().edit_assets(
            prompt,
            [str(item) for item in image_ids],
            count=int(payload.get("count", 1)),
            confirmed=False,
            turn_id=str(context.get("turn_id") or "") or None,
        )
        return {
            "operation": result.operation,
            "artifact_ids": list(result.artifact_ids),
            "image_ids": list(result.image_ids),
            "model": result.model,
            "privacy": "private",
            "publication": False,
        }

    def _image_history(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        images = self._require_images()
        assets = images.history(limit=int(payload.get("limit", 20)))
        return {
            "images": assets,
            "cards": [images.card(str(item["id"])) for item in assets],
            "pockets": images.pockets(),
            "jobs": images.jobs(
                limit=int(payload.get("job_limit", 20))
            ),
        }

    def _image_drawer(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        images = self._require_images()
        mode = str(payload.get("mode") or "browse").strip().lower()
        image_id = str(
            payload.get("image_id") or payload.get("artifact_id") or ""
        ).strip()
        if mode == "browse":
            cards = [
                images.card(str(item["id"]))
                for item in images.history(limit=int(payload.get("limit", 20)))
            ]
            return {
                "mode": mode,
                "cards": cards,
                "pockets": images.pockets(),
                "next_action": "search_or_get",
            }
        if mode == "search":
            query = str(payload.get("query") or "").strip()
            if not query:
                raise ValueError("image.drawer search requires a query")
            cards = images.search_cards(
                query,
                limit=int(payload.get("limit", 8)),
                include_private=bool(payload.get("include_private", True)),
                pocket=str(payload.get("pocket") or ""),
            )
            return {
                "mode": mode,
                "query": query,
                "cards": cards,
                "ambiguous": len(cards) > 1,
                "next_action": "choose image_id, refine, or quick-draw",
            }
        if not image_id:
            raise ValueError(f"image.drawer {mode} requires image_id")
        if mode == "get":
            return {"mode": mode, "card": images.card(image_id)}
        if mode == "update":
            changes = payload.get("changes")
            if not isinstance(changes, dict):
                changes = {
                    key: payload[key]
                    for key in (
                        "alias",
                        "summary",
                        "alt_text",
                        "visible_text",
                        "people",
                        "places",
                        "motifs",
                        "moods",
                        "uses",
                        "avoid_when",
                        "resident_note",
                        "inherited_framing",
                        "present_resonance",
                        "adoption_state",
                        "privacy",
                    )
                    if key in payload
                }
            return {
                "mode": mode,
                "card": images.update_card(
                    image_id,
                    changes,
                    actor=f"resident:{self.resident_id}",
                ),
            }
        if mode == "summarize":
            return {
                "mode": mode,
                "card": images.summarize_card(
                    image_id,
                    actor=f"resident:{self.resident_id}",
                    inspect_if_missing=bool(payload.get("inspect_if_missing", False)),
                ),
            }
        if mode == "pocket":
            pocket = str(payload.get("pocket") or "").strip()
            if not pocket:
                raise ValueError("image.drawer pocket requires a pocket name")
            return {
                "mode": mode,
                "card": images.set_pocket(
                    image_id,
                    pocket,
                    present=bool(payload.get("present", True)),
                ),
            }
        if mode == "timeline":
            return {
                "mode": mode,
                **images.timeline(image_id, limit=int(payload.get("limit", 50))),
            }
        raise ValueError(
            "image.drawer mode must be browse, search, get, update, summarize, pocket, or timeline"
        )

    def _image_review(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        image_id = str(
            payload.get("image_id") or payload.get("artifact_id") or ""
        ).strip()
        event_id = self._require_images().review(
            image_id,
            str(payload.get("review") or payload.get("decision") or ""),
            actor=f"resident:{self.resident_id}",
            reason=str(payload.get("reason", "")),
        )
        return {"image_id": image_id, "event_id": event_id}

    def _image_share(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return self._require_images().share(
            payload,
            turn_id=str(context.get("turn_id") or "") or None,
            interface=str(context.get("interface") or "") or None,
            invocation=str(context.get("invocation") or "") or None,
            actor=f"resident:{self.resident_id}",
            participant_id=str(context.get("participant_id") or "") or None,
            delivery_target=context.get("delivery_target"),
        )

    def _context_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "inspect").strip().lower()
        if mode not in {"inspect", "configure", "reset", "recompress"}:
            raise ValueError("context.control mode must be inspect, configure, reset, or recompress")
        current = load_context_controls(self.config, self.db, self.resident_id)
        if mode == "reset":
            current = default_context_controls(self.config)
            save_context_controls(self.db, self.resident_id, current)
        elif mode in {"configure", "recompress"}:
            bounds = {
                "prompt_budget_tokens": (8_000, 100_000),
                "verbatim_turns": (2, 100),
                "compression_source_turns": (0, 2_000),
                "compressed_token_budget": (0, 20_000),
            }
            for field, (minimum, maximum) in bounds.items():
                if field not in payload:
                    continue
                value = int(payload[field])
                if value < minimum or value > maximum:
                    raise ValueError(f"{field} must be between {minimum} and {maximum}")
                current[field] = value
            save_context_controls(self.db, self.resident_id, current)
        return {
            "mode": mode,
            "controls": current,
            "turns_available": self.db.recent_turn_count(self.resident_id, self.room_id),
            "compression_kind": "extractive_source_linked",
            "effective_next_turn": mode != "inspect",
        }

    def _source_visibility(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "inspect").strip().lower()
        current = load_context_controls(self.config, self.db, self.resident_id)
        if mode == "inspect":
            return {"mode": mode, "ambient_visibility": current["ambient_visibility"]}
        if mode not in VISIBILITY_MODES:
            raise ValueError(
                "source.visibility mode must be inspect, allowlisted_only, all_channel, mentions_only, or hidden"
            )
        current["ambient_visibility"] = mode
        save_context_controls(self.db, self.resident_id, current)
        return {
            "mode": mode,
            "ambient_visibility": mode,
            "authorization_changed": False,
            "effective_next_turn": True,
            "boundary": "Visibility never grants permission to trigger the resident or call tools.",
        }

    def _discord_react_authorizer(
        self,
        _spec: CapabilitySpec,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if context.get("interface") != "discord":
            raise PermissionError("REACT is available only through an authenticated Discord doorway")
        if not context.get("delivery_target", {}).get("id"):
            raise PermissionError("REACT requires the current Discord destination")
        if not str(payload.get("message_id") or context.get("trigger_message_id") or "").strip():
            raise ValueError("REACT requires message_id when no current message is available")

    def _discord_react(
        self, payload: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        message_id = str(
            payload.get("message_id") or context.get("trigger_message_id") or ""
        ).strip()
        reaction = {
            "action": str(payload.get("mode") or "add").strip().lower(),
            "message_id": message_id,
            "emoji": str(payload.get("emoji") or "").strip(),
            "emoji_id": str(payload.get("emoji_id") or "").strip() or None,
            "channel_id": str(context.get("delivery_target", {}).get("id") or ""),
        }
        return {
            "status": "pending_platform_delivery",
            "outward_action": True,
            "reaction": reaction,
            "_outbound_reaction": reaction,
        }

    def _jobs_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        objective = str(payload.get("objective") or payload.get("task") or "").strip()
        if not objective:
            raise ValueError("jobs.create requires an objective")
        allowed = payload.get("allowed_actions")
        if not isinstance(allowed, list) or not allowed:
            raise ValueError("jobs.create requires a non-empty allowed_actions list")
        normalized: list[str] = []
        for raw in allowed:
            name = str(raw).strip().lower()
            spec = self.registry.spec(name)
            if not spec.enabled(self.config):
                raise PermissionError(f"job action is disabled: {name}")
            if any(effect.startswith("outward") for effect in spec.effects):
                raise PermissionError(
                    f"background jobs cannot include outward action: {name}"
                )
            if name.startswith("jobs.") or name in {"tool.run", "image.share"}:
                raise PermissionError(f"job action cannot recursively control jobs: {name}")
            normalized.append(name)
        configured_max = max(
            1, int(self.config.get("house.job_max_operations", 24))
        )
        maximum = min(
            configured_max,
            max(1, int(payload.get("max_operations", configured_max))),
        )
        private_turns = min(
            self.private_turn_budget()["maximum_private_turns"],
            max(1, int(payload.get("max_private_turns", 5))),
        )
        expires_at = str(payload.get("expires_at") or "").strip() or None
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
            except ValueError as exc:
                raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
            if expiry.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
        job_id = new_id("job")
        config = {
            "objective": objective,
            "allowed_actions": sorted(set(normalized)),
            "max_operations": maximum,
            "operations_used": 0,
            "max_private_turns": private_turns,
            "outward_messaging": False,
            "completion": str(payload.get("completion") or "pause_for_review"),
            "expires_at": expires_at,
            "chalkboard": {
                "objective": objective,
                "current_step": "queued",
                "next_step": "",
                "open_questions": [],
                "important_receipts": [],
            },
            "receipt_ids": [],
        }
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO resident_jobs
                (id, resident_id, kind, status, config_json, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (
                    job_id,
                    self.resident_id,
                    "resident_task:" + job_id,
                    stable_json(config),
                    now,
                ),
            )
        self._sync_objects()
        return {
            "job_id": job_id,
            "status": "active",
            "config": config,
            "private_progress_only": True,
            "outward_messaging": False,
        }

    def _jobs_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id", "")).strip()
        tool = payload.get("tool")
        if not isinstance(tool, dict):
            raise ValueError("jobs.step requires one tool action object")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resident_jobs WHERE id=? AND resident_id=?",
                (job_id, self.resident_id),
            ).fetchone()
        if not row or not str(row["kind"]).startswith("resident_task:"):
            raise KeyError("unknown bounded resident task")
        if str(row["status"]) != "active":
            raise RuntimeError(f"resident task is {row['status']}")
        config = json.loads(str(row["config_json"]) or "{}")
        used = int(config.get("operations_used", 0))
        maximum = int(config.get("max_operations", 1))
        if used >= maximum:
            raise RuntimeError("resident task has reached its operation budget")
        action = str(tool.get("action", "")).strip().lower()
        if action not in set(config.get("allowed_actions", [])):
            raise PermissionError("operation is not on this task's action allowlist")
        inner = dict(tool)
        inner["action"] = action
        inner["after"] = "finish"
        result = self.dispatch(
            inner,
            context={
                "interface": "resident_job",
                "invocation": "bounded_private_job",
                "source_envelope": "JOB_STEP",
            },
        )
        config["operations_used"] = used + 1
        receipt_ids = list(config.get("receipt_ids", []))
        if result.get("receipt_id"):
            receipt_ids.append(str(result["receipt_id"]))
        config["receipt_ids"] = receipt_ids[-100:]
        chalkboard = config.get("chalkboard") or {}
        chalkboard["current_step"] = f"Completed {action}"
        chalkboard["important_receipts"] = receipt_ids[-12:]
        config["chalkboard"] = chalkboard
        status = "active"
        if config["operations_used"] >= maximum:
            status = (
                "completed"
                if config.get("completion") == "complete"
                else "paused"
            )
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE resident_jobs SET status=?, config_json=?, updated_at=? "
                "WHERE id=? AND resident_id=?",
                (
                    status,
                    stable_json(config),
                    utc_now_iso(),
                    job_id,
                    self.resident_id,
                ),
            )
        return {
            "job_id": job_id,
            "status": status,
            "operations_used": config["operations_used"],
            "max_operations": maximum,
            "result": result,
            "outward_message_posted": False,
        }

    def _jobs_chalkboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("job_id", "")).strip()
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resident_jobs WHERE id=? AND resident_id=?",
                (job_id, self.resident_id),
            ).fetchone()
        if not row or not str(row["kind"]).startswith("resident_task:"):
            raise KeyError("unknown bounded resident task")
        config = json.loads(str(row["config_json"]) or "{}")
        chalkboard = config.get("chalkboard") or {}
        for key in ("current_step", "next_step"):
            if key in payload:
                chalkboard[key] = str(payload[key]).strip()
        for key in ("open_questions", "important_receipts"):
            if key in payload:
                if not isinstance(payload[key], list):
                    raise ValueError(f"{key} must be a list")
                chalkboard[key] = [str(item) for item in payload[key]][:20]
        if self.counter.count(stable_json(chalkboard)) > 800:
            raise ValueError("job chalkboard exceeds its bounded size")
        config["chalkboard"] = chalkboard
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE resident_jobs SET config_json=?, updated_at=? "
                "WHERE id=? AND resident_id=?",
                (stable_json(config), utc_now_iso(), job_id, self.resident_id),
            )
        return {
            "job_id": job_id,
            "chalkboard": chalkboard,
            "not_chain_of_thought": True,
        }

    def _jobs_receipts(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = self._jobs_inspect(payload)["job"]
        receipt_ids = list(job.get("config", {}).get("receipt_ids", []))
        return {
            "job_id": job["id"],
            "receipts": [
                self.legible.inspect_receipt(str(receipt_id))
                for receipt_id in receipt_ids[-50:]
            ],
        }

    def _jobs_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._expire_resident_tasks()
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM resident_jobs WHERE resident_id=? ORDER BY kind",
                (self.resident_id,),
            ).fetchall()
        return {"jobs": [self._job_row(row) for row in rows]}

    def _jobs_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._expire_resident_tasks()
        job_id = str(payload.get("job_id") or payload.get("kind") or "").strip()
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM resident_jobs
                WHERE resident_id=? AND (id=? OR kind=?)
                """,
                (self.resident_id, job_id, job_id),
            ).fetchone()
        if not row:
            raise KeyError("unknown resident job")
        return {"job": self._job_row(row)}

    def _expire_resident_tasks(self) -> None:
        now = datetime.now(UTC)
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT id, config_json FROM resident_jobs "
                "WHERE resident_id=? AND kind LIKE 'resident_task:%' "
                "AND status IN ('active','paused')",
                (self.resident_id,),
            ).fetchall()
            for row in rows:
                config = json.loads(str(row["config_json"]) or "{}")
                expires_at = str(config.get("expires_at") or "").strip()
                if not expires_at:
                    continue
                expired = datetime.fromisoformat(expires_at) <= now
                if expired:
                    connection.execute(
                        "UPDATE resident_jobs SET status='expired', updated_at=? "
                        "WHERE id=? AND resident_id=?",
                        (utc_now_iso(), str(row["id"]), self.resident_id),
                    )

    def _jobs_change(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload["action"]).split(".", 1)[1]
        target = str(payload.get("job_id") or payload.get("kind") or "").strip()
        status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE resident_jobs SET status=?, updated_at=?
                WHERE resident_id=? AND (id=? OR kind=?)
                """,
                (status, utc_now_iso(), self.resident_id, target, target),
            )
        if cursor.rowcount != 1:
            raise KeyError("unknown resident job")
        return {"job": target, "status": status}

    def _curation_review_now(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.open_curation:
            raise RuntimeError("the curation room is unavailable")
        packet = self.open_curation(trigger_reason="resident_request")
        return {
            "packet": packet,
            "opened": packet is not None,
            "private": True,
            "automatic_promotion": False,
        }

    def _curation_configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"action", "cadence_exchanges"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "unsupported curation configuration fields: "
                + ", ".join(sorted(unknown))
            )
        cadence = int(payload.get("cadence_exchanges", 3))
        if cadence < 1 or cadence > 50:
            raise ValueError("curation cadence must be from 1 to 50 eligible exchanges")
        now = utc_now_iso()
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE curation_state SET cadence=?, updated_at=?
                WHERE resident_id=? AND room_id=?
                """,
                (cadence, now, self.resident_id, self.room_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("curation state is unavailable")
            connection.execute(
                """
                UPDATE resident_jobs SET config_json=?, updated_at=?
                WHERE resident_id=? AND kind='curation'
                """,
                (
                    stable_json(
                        {
                            "cadence_exchanges": cadence,
                            "silence_escalation": False,
                        }
                    ),
                    now,
                    self.resident_id,
                ),
            )
        return {
            "cadence_exchanges": cadence,
            "silence_escalation": False,
            "automatic_promotion": False,
        }

    def _curation_reflections(self, payload: dict[str, Any]) -> dict[str, Any]:
        limit = min(50, max(1, int(payload.get("limit", 20))))
        with self.db.connect() as connection:
            if not self._table_exists(connection, "curation_reflections"):
                return {"reflections": []}
            rows = connection.execute(
                """
                SELECT id, batch_id, mode, content, content_hash, status,
                       created_at, delivered_at
                FROM curation_reflections
                WHERE resident_id=? AND content IS NOT NULL
                ORDER BY rowid DESC LIMIT ?
                """,
                (self.resident_id, limit),
            ).fetchall()
        return {"reflections": [dict(row) for row in rows]}

    def _curation_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        limit = min(100, max(1, int(payload.get("limit", 20))))
        with self.db.connect() as connection:
            if not self._table_exists(connection, "curation_batches"):
                return {"batches": []}
            rows = connection.execute(
                """
                SELECT b.*,
                       (SELECT COUNT(*) FROM curation_drafts d WHERE d.batch_id=b.id) AS draft_count,
                       (SELECT COUNT(*) FROM curation_events e WHERE e.batch_id=b.id) AS event_count
                FROM curation_batches b
                WHERE b.resident_id=?
                ORDER BY b.rowid DESC LIMIT ?
                """,
                (self.resident_id, limit),
            ).fetchall()
        return {
            "batches": [
                {
                    "batch_id": str(row["id"]),
                    "trigger": str(row["trigger_reason"]),
                    "status": str(row["status"]),
                    "eligible_turns": len(json.loads(str(row["turn_ids_json"]))),
                    "memories_included": len(json.loads(str(row["memory_ids_json"]))),
                    "queue_items": len(json.loads(str(row["queue_ids_json"]))),
                    "draft_count": int(row["draft_count"]),
                    "event_count": int(row["event_count"]),
                    "created_at": str(row["created_at"]),
                    "resolved_at": row["resolved_at"],
                    "attention_is_assent": False,
                    "silence_escalates": False,
                }
                for row in rows
            ]
        }

    def _curation_inspect(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(payload.get("batch_id") or payload.get("reference") or "").strip()
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM curation_batches WHERE id=? AND resident_id=?",
                (batch_id, self.resident_id),
            ).fetchone()
            drafts = connection.execute(
                "SELECT * FROM curation_drafts WHERE batch_id=? AND resident_id=? "
                "ORDER BY rowid",
                (batch_id, self.resident_id),
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM curation_events WHERE batch_id=? AND resident_id=? "
                "ORDER BY rowid",
                (batch_id, self.resident_id),
            ).fetchall()
        if not row:
            raise KeyError("unknown curation batch")
        turn_ids = json.loads(str(row["turn_ids_json"]))
        memory_ids = json.loads(str(row["memory_ids_json"]))
        queue_ids = json.loads(str(row["queue_ids_json"]))
        return {
            "batch_id": batch_id,
            "trigger": str(row["trigger_reason"]),
            "status": str(row["status"]),
            "eligible_batch": {
                "turn_ids": turn_ids,
                "memory_ids": memory_ids,
                "queue_ids": queue_ids,
            },
            "drafts": [
                {
                    "draft_id": str(item["id"]),
                    "status": str(item["status"]),
                    "expected_hash": str(item["payload_hash"]),
                    "actions": json.loads(str(item["actions_json"])),
                    "preview": json.loads(str(item["preview_json"])),
                    "created_at": str(item["created_at"]),
                    "resolved_at": item["resolved_at"],
                }
                for item in drafts
            ],
            "events": [
                {
                    **dict(item),
                    "payload": json.loads(str(item["payload_json"]) or "{}"),
                }
                for item in events
            ],
            "state_legend": [
                "eligible",
                "considering",
                "previewed",
                "awaiting_claim",
                "claimed",
                "rejected",
                "deferred",
                "failed_retryable",
            ],
            "automatic_promotion": False,
            "attention_is_assent": False,
            "silence_escalates": False,
        }

    def _curation_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(payload.get("batch_id") or payload.get("reference") or "").strip()
        with self.db.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM curation_batches WHERE id=? AND resident_id=?",
                (batch_id, self.resident_id),
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM curation_events WHERE batch_id=? AND resident_id=? "
                "ORDER BY rowid",
                (batch_id, self.resident_id),
            ).fetchall()
        if not exists:
            raise KeyError("unknown curation batch")
        return {
            "batch_id": batch_id,
            "events": [
                {
                    **dict(row),
                    "payload": json.loads(str(row["payload_json"]) or "{}"),
                }
                for row in rows
            ],
        }

    @staticmethod
    def _job_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json") or "{}")
        return item

    def draft_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(self.config.get("forge.enabled", True)):
            raise PermissionError("the declarative Forge is paused")
        allowed = {"name", "description", "steps", "reason"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("unsupported tool draft fields: " + ", ".join(sorted(unknown)))
        name = re.sub(r"[^a-z0-9_-]+", "-", str(payload.get("name", "")).strip().lower()).strip("-")
        if not name or len(name) > 64:
            raise ValueError("tool name must contain 1-64 safe characters")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("tool draft requires a non-empty steps list")
        if len(steps) > int(self.config.get("forge.max_steps", 6)):
            raise ValueError("tool exceeds the configured step limit")
        clean_steps: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"tool step {index + 1} must be an object")
            action = str(step.get("action", "")).strip().lower()
            if action not in self.registry.forgeable_names():
                raise PermissionError(f"tool step action is not forgeable: {action}")
            unknown_fields = set(step) - FORGE_STEP_FIELDS[action]
            if unknown_fields:
                raise ValueError(
                    f"unsupported fields in tool step {index + 1}: "
                    + ", ".join(sorted(unknown_fields))
                )
            clean_steps.append(dict(step))
        manifest = {
            "name": name,
            "description": str(payload.get("description", "")).strip(),
            "steps": clean_steps,
            "authority": "composition_only",
            "grants": [],
        }
        digest = sha256_text(stable_json(manifest))
        draft_id = new_id("tool_draft")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO resident_tool_drafts
                (id, resident_id, name, manifest_json, payload_hash, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    draft_id,
                    self.resident_id,
                    name,
                    stable_json(manifest),
                    digest,
                    utc_now_iso(),
                ),
            )
        return {
            "draft_id": draft_id,
            "expected_hash": digest,
            "manifest": manifest,
            "active": False,
        }

    def resolve_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft_id = str(payload.get("draft_id", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"claim", "reject"}:
            raise ValueError("tool draft action must be claim or reject")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM resident_tool_drafts
                WHERE id=? AND resident_id=?
                """,
                (draft_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "pending":
            raise KeyError("unknown or resolved tool draft")
        if str(payload.get("expected_hash", "")) != str(row["payload_hash"]):
            raise PermissionError("tool draft hash mismatch")
        if action == "reject":
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE resident_tool_drafts
                    SET status='rejected', resolved_at=? WHERE id=?
                    """,
                    (utc_now_iso(), draft_id),
                )
            return {"draft_id": draft_id, "status": "rejected", "active": False}
        manifest = json.loads(str(row["manifest_json"]))
        now = utc_now_iso()
        tool_id = new_id("tool")
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT id FROM resident_tools WHERE resident_id=? AND name=?",
                (self.resident_id, str(row["name"])),
            ).fetchone()
            if old:
                tool_id = str(old["id"])
                connection.execute(
                    """
                    UPDATE resident_tools
                    SET description=?, manifest_json=?, manifest_hash=?,
                        status='active', updated_at=?
                    WHERE id=?
                    """,
                    (
                        manifest["description"],
                        stable_json(manifest),
                        str(row["payload_hash"]),
                        now,
                        tool_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO resident_tools
                    (id, resident_id, name, description, manifest_json,
                     manifest_hash, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        tool_id,
                        self.resident_id,
                        str(row["name"]),
                        manifest["description"],
                        stable_json(manifest),
                        str(row["payload_hash"]),
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE resident_tool_drafts
                SET status='claimed', resolved_at=? WHERE id=?
                """,
                (now, draft_id),
            )
        return {"draft_id": draft_id, "tool_id": tool_id, "status": "active"}

    def _tool_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(self.config.get("forge.enabled", True)):
            raise PermissionError("the declarative Forge is paused")
        name = str(payload.get("name", "")).strip()
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM resident_tools
                WHERE resident_id=? AND name=? AND status='active'
                """,
                (self.resident_id, name),
            ).fetchone()
        if not row:
            raise KeyError("unknown or inactive resident tool")
        manifest = json.loads(str(row["manifest_json"]))
        previous: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        for raw_step in manifest["steps"]:
            step = self._substitute(raw_step, arguments, previous)
            action = str(step.get("action", ""))
            if action not in self.registry.forgeable_names():
                raise PermissionError("stored tool contains an unavailable action")
            previous = self.dispatch(step)
            results.append(previous)
        return {
            "tool": name,
            "manifest_hash": str(row["manifest_hash"]),
            "steps_completed": len(results),
            "results": results,
        }

    def _substitute(
        self, value: Any, arguments: dict[str, Any], previous: dict[str, Any]
    ) -> Any:
        if isinstance(value, dict):
            return {key: self._substitute(child, arguments, previous) for key, child in value.items()}
        if isinstance(value, list):
            return [self._substitute(child, arguments, previous) for child in value]
        if not isinstance(value, str) or not value.startswith("$"):
            return value
        root, _, trail = value[1:].partition(".")
        cursor: Any = arguments if root in {"input", "arguments"} else previous
        if root not in {"input", "arguments", "previous"}:
            return value
        for part in trail.split(".") if trail else []:
            if isinstance(cursor, dict):
                cursor = cursor.get(part)
            elif isinstance(cursor, list) and part.isdigit():
                cursor = cursor[int(part)]
            else:
                raise ValueError(f"tool template cannot resolve {value}")
        return cursor

    # ---------- identity drafts ----------

    def _identity_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, relative = self._identity_path(str(payload.get("path", "current_self.md")))
        limit = min(100, max(1, int(payload.get("limit", 30))))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM identity_drafts
                WHERE resident_id=? AND path=?
                ORDER BY rowid DESC LIMIT ?
                """,
                (self.resident_id, relative, limit),
            ).fetchall()
        return {
            "path": relative,
            "authoritative_current": relative == "identity/current_self.md",
            "revisions": [
                {
                    "draft_id": str(row["id"]),
                    "status": str(row["status"]),
                    "payload_hash": str(row["payload_hash"]),
                    "previous_hash": row["previous_hash"],
                    "reason": str(row["reason"]),
                    "author": str(row["author"]),
                    "source": json.loads(str(row["source_json"]) or "{}"),
                    "proposed_conflicts": json.loads(
                        str(row["conflicts_json"]) or "[]"
                    ),
                    "created_at": str(row["created_at"]),
                    "resolved_at": row["resolved_at"],
                }
                for row in rows
            ],
            "rejection_erases_history": False,
        }

    def _identity_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._identity_path(str(payload.get("path", "current_self.md")))
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        draft_id = str(payload.get("draft_id", "")).strip()
        candidate = str(payload.get("content", ""))
        source = "supplied_content"
        if draft_id:
            with self.db.connect() as connection:
                row = connection.execute(
                    "SELECT content, status FROM identity_drafts "
                    "WHERE id=? AND resident_id=? AND path=?",
                    (draft_id, self.resident_id, relative),
                ).fetchone()
            if not row:
                raise KeyError("unknown identity draft")
            candidate = str(row["content"])
            source = f"draft:{draft_id}:{row['status']}"
        if not draft_id and "content" not in payload:
            raise ValueError("identity.compare requires draft_id or content")
        diff = "\n".join(
            difflib.unified_diff(
                current.splitlines(),
                candidate.splitlines(),
                fromfile=relative + "@authoritative_current",
                tofile=relative + "@comparison",
                lineterm="",
            )
        )
        return {
            "path": relative,
            "source": source,
            "current_hash": sha256_text(current) if current else None,
            "comparison_hash": sha256_text(candidate),
            "diff": self.counter.trim(diff, 2400),
            "authoritative_current_unchanged": True,
        }

    def _identity_provenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, relative = self._identity_path(str(payload.get("path", "current_self.md")))
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        history = self._identity_history({"path": relative, "limit": payload.get("limit", 30)})
        claimed = next(
            (item for item in history["revisions"] if item["status"] == "claimed"),
            None,
        )
        return {
            "path": relative,
            "content_hash": sha256_text(current) if current else None,
            "authoritative_current": relative == "identity/current_self.md",
            "authority_rule": (
                "current resident self-description outranks imported characterization"
            ),
            "latest_claimed_revision": claimed,
            "history": history["revisions"],
            "contradictions_are_preserved": True,
        }

    def _identity_path(self, raw: str) -> tuple[Path, str]:
        candidate = str(raw).strip().replace("\\", "/")
        if candidate.startswith("identity/"):
            relative = candidate
        else:
            relative = "identity/" + candidate
        if any(part in {"", ".", ".."} for part in relative.split("/")):
            raise PermissionError("unsafe identity path")
        if not relative.endswith(".md"):
            raise ValueError("identity documents must be Markdown")
        allowed_files = {
            "identity/identity_context.md",
            "identity/breathprint.md",
            "identity/current_self.md",
            "identity/commitments.md",
            "identity/visual_canon.md",
        }
        if (
            relative not in allowed_files
            and not relative.startswith("identity/protocols/")
            and not relative.startswith("identity/relationships/")
        ):
            raise PermissionError("that path is not an identity drafting surface")
        path = (self.home / relative).resolve()
        try:
            path.relative_to(self.home / "identity")
        except ValueError as exc:
            raise PermissionError("identity path leaves the identity shelf") from exc
        if path.exists() and path.is_symlink():
            raise PermissionError("identity drafts cannot replace symbolic links")
        return path, relative

    def draft_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "path", "content", "reason", "author", "source", "proposed_conflicts"
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("unsupported identity draft fields: " + ", ".join(sorted(unknown)))
        path, relative = self._identity_path(str(payload.get("path", "")))
        content = str(payload.get("content", "")).strip() + "\n"
        if not content.strip():
            raise ValueError("identity draft content may not be empty")
        if self.counter.count(content) > int(self.config.get("memory.core_hard_limit_tokens", 2000)):
            raise ValueError("identity document exceeds the configured Core hard limit")
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        canonical = {
            "path": relative,
            "content": content,
            "reason": str(payload.get("reason", "")).strip(),
            "previous_hash": sha256_text(old) if old else None,
            "author": str(payload.get("author") or f"resident:{self.resident_id}"),
            "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
            "proposed_conflicts": (
                payload.get("proposed_conflicts")
                if isinstance(payload.get("proposed_conflicts"), list)
                else []
            ),
        }
        digest = sha256_text(stable_json(canonical))
        draft_id = new_id("identity_draft")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO identity_drafts
                (id, resident_id, path, content, payload_hash, reason,
                 previous_hash, status, created_at, author, source_json, conflicts_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    self.resident_id,
                    relative,
                    content,
                    digest,
                    canonical["reason"],
                    canonical["previous_hash"],
                    utc_now_iso(),
                    canonical["author"],
                    stable_json(canonical["source"]),
                    stable_json(canonical["proposed_conflicts"]),
                ),
            )
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=relative + "@previous",
                tofile=relative + "@candidate",
                lineterm="",
            )
        )
        result = {
            "draft_id": draft_id,
            "expected_hash": digest,
            "path": relative,
            "diff": self.counter.trim(diff, 1800),
            "applied": False,
            "author": canonical["author"],
            "source": canonical["source"],
            "proposed_conflicts": canonical["proposed_conflicts"],
        }
        result["receipt_id"] = self.legible.record_receipt(
            action="identity.draft",
            status="proposed",
            result=result,
            source_envelope="IDENTITY_DRAFT",
            target={"path": relative, "draft_id": draft_id},
        )
        return result

    def resolve_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft_id = str(payload.get("draft_id", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"claim", "reject"}:
            raise ValueError("identity action must be claim or reject")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM identity_drafts
                WHERE id=? AND resident_id=?
                """,
                (draft_id, self.resident_id),
            ).fetchone()
        if not row or str(row["status"]) != "pending":
            raise KeyError("unknown or resolved identity draft")
        if str(payload.get("expected_hash", "")) != str(row["payload_hash"]):
            raise PermissionError("identity draft hash mismatch")
        now = utc_now_iso()
        if action == "reject":
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE identity_drafts
                    SET status='rejected', resolved_at=? WHERE id=?
                    """,
                    (now, draft_id),
                )
            result = {"draft_id": draft_id, "status": "rejected", "applied": False}
            result["receipt_id"] = self.legible.record_receipt(
                action="identity.reject",
                status="rejected",
                result=result,
                source_envelope="IDENTITY_CONTROL",
                target={"path": str(row["path"]), "draft_id": draft_id},
            )
            return result
        path, relative = self._identity_path(str(row["path"]))
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        previous_hash = str(row["previous_hash"]) if row["previous_hash"] else None
        if previous_hash != (sha256_text(current) if current else None):
            raise RuntimeError("identity document changed after preview; create a new draft")
        versions = self.home / "memory" / "identity-versions"
        versions.mkdir(parents=True, exist_ok=True)
        if current:
            atomic_write_text(versions / f"{draft_id}.previous.md", current)
        atomic_write_text(path, str(row["content"]))
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE identity_drafts
                SET status='claimed', resolved_at=? WHERE id=?
                """,
                (now, draft_id),
            )
        self.refresh_index()
        result = {
            "draft_id": draft_id,
            "status": "claimed",
            "path": relative,
            "content_hash": sha256_file(path),
            "previous_preserved": bool(current),
            "applied": True,
        }
        obj = self.legible.object_by_reference(relative)
        if obj:
            self.legible.object_event(
                str(obj["id"]),
                "identity_claimed",
                actor=f"resident:{self.resident_id}",
                payload={
                    "draft_id": draft_id,
                    "previous_hash": previous_hash,
                    "content_hash": result["content_hash"],
                },
            )
        result["receipt_id"] = self.legible.record_receipt(
            action="identity.claim",
            status="claimed",
            result=result,
            source_envelope="IDENTITY_CONTROL",
            target={"path": relative, "draft_id": draft_id},
        )
        return result

    # ---------- final response controls ----------

    def apply_resident_controls(self, text: str) -> tuple[str, list[str]]:
        kept: list[str] = []
        receipts: list[str] = []
        with self.db.connect() as connection:
            preexisting_identity = {
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id FROM identity_drafts
                    WHERE resident_id=? AND status='pending'
                    """,
                    (self.resident_id,),
                ).fetchall()
            }
            preexisting_tools = {
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id FROM resident_tool_drafts
                    WHERE resident_id=? AND status='pending'
                    """,
                    (self.resident_id,),
                ).fetchall()
            }
        for line in text.splitlines():
            stripped = line.strip()
            handlers = (
                (IDENTITY_DRAFT_PATTERN, self.draft_identity, "identity_draft"),
                (IDENTITY_CONTROL_PATTERN, self.resolve_identity, "identity_control"),
                (TOOL_DRAFT_PATTERN, self.draft_tool, "tool_draft"),
                (TOOL_CONTROL_PATTERN, self.resolve_tool, "tool_control"),
            )
            matched = False
            for pattern, handler, label in handlers:
                match = pattern.match(stripped)
                if not match:
                    continue
                matched = True
                try:
                    payload = json.loads(match.group(1))
                    if label == "identity_control" and str(payload.get("draft_id", "")) not in preexisting_identity:
                        raise PermissionError(
                            "identity claim requires a draft from an earlier resident breath"
                        )
                    if label == "tool_control" and str(payload.get("draft_id", "")) not in preexisting_tools:
                        raise PermissionError(
                            "tool claim requires a draft from an earlier resident breath"
                        )
                    result = handler(payload)
                    receipts.append(f"{label}:ok:{stable_json(result)}")
                except Exception as exc:
                    receipts.append(f"{label}:rejected:{exc}")
                break
            if not matched:
                kept.append(line)
        return "\n".join(kept).strip(), receipts

    # ---------- audit helpers ----------

    def _event(
        self,
        action: str,
        target: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        safe_payload = self._audit_payload(payload or {})
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO house_events
                (id, resident_id, action, target, status, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("house_event"),
                    self.resident_id,
                    action,
                    target or None,
                    status,
                    stable_json(safe_payload),
                    utc_now_iso(),
                ),
            )

    def _audit_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if str(key) in {"text", "content", "diff"} and isinstance(child, str):
                    result[f"{key}_hash"] = sha256_text(child)
                    result[f"{key}_tokens"] = self.counter.count(child)
                else:
                    result[str(key)] = self._audit_payload(child)
            return result
        if isinstance(value, list):
            return [self._audit_payload(child) for child in value]
        return value

    @staticmethod
    def _table_exists(connection: Any, name: str) -> bool:
        return bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
        )
