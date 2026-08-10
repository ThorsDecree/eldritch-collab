from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .library_window_transport import ExtractionResult, FetchResult, SEARCH_PROVIDER
from .utils import new_id, stable_json, utc_now_iso


_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS library_web_searches (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    query TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_library_web_searches_resident
ON library_web_searches(resident_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS library_web_search_results (
    search_id TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    snippet TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (search_id, rank),
    FOREIGN KEY (search_id) REFERENCES library_web_searches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS library_sources (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    original_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    raw_hash TEXT NOT NULL,
    raw_size_bytes INTEGER NOT NULL,
    raw_path TEXT NOT NULL,
    readable_hash TEXT,
    readable_size_bytes INTEGER NOT NULL DEFAULT 0,
    readable_path TEXT,
    extraction_method TEXT NOT NULL,
    redirect_chain_json TEXT NOT NULL DEFAULT '[]',
    response_headers_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    risk_signals_json TEXT NOT NULL DEFAULT '[]',
    trust_class TEXT NOT NULL,
    authority_state TEXT NOT NULL,
    review_state TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_library_sources_resident
ON library_sources(resident_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_sources_raw_hash
ON library_sources(resident_id, raw_hash);

CREATE TABLE IF NOT EXISTS research_notebooks (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    retention TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_notebooks_resident
ON research_notebooks(resident_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS research_notebook_sources (
    notebook_id TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (notebook_id, source_id),
    FOREIGN KEY (notebook_id) REFERENCES research_notebooks(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES library_sources(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS research_notebook_notes (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    authority_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (notebook_id) REFERENCES research_notebooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_research_notebook_notes_notebook
ON research_notebook_notes(notebook_id, created_at);

CREATE TABLE IF NOT EXISTS research_notebook_events (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    notebook_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_NOTE_KINDS = {"observation", "question", "contradiction", "summary", "inference", "uncertainty"}


def ensure_schema(house: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(_SCHEMA)
    (house.home / "research" / "sources").mkdir(parents=True, exist_ok=True)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _store_content_addressed(root: Path, digest: str, suffix: str, data: bytes) -> str:
    relative = Path("research") / "sources" / f"{digest}{suffix}"
    path = root / relative
    if path.exists():
        existing = path.read_bytes()
        if _sha256_bytes(existing) != digest or existing != data:
            raise RuntimeError("content-addressed research source integrity mismatch")
    else:
        _atomic_write(path, data)
    return relative.as_posix()


def _decode_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return default


def record_search(
    house: Any,
    *,
    query: str,
    results: list[dict[str, Any]],
    fetched: FetchResult,
) -> tuple[str, str]:
    ensure_schema(house)
    search_id = new_id("web_search")
    now = utc_now_iso()
    query_hash = _sha256_bytes(query.encode("utf-8"))
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO library_web_searches
            (id, resident_id, query, query_hash, provider, result_count,
             fetched_at, elapsed_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                search_id,
                house.resident_id,
                query,
                query_hash,
                SEARCH_PROVIDER,
                len(results),
                now,
                int(fetched.elapsed_ms),
            ),
        )
        for item in results:
            connection.execute(
                """
                INSERT INTO library_web_search_results
                (search_id, resident_id, rank, title, url, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    search_id,
                    house.resident_id,
                    int(item["rank"]),
                    str(item.get("title") or "")[:500],
                    str(item.get("url") or "")[:4096],
                    str(item.get("snippet") or "")[:1200],
                    now,
                ),
            )
    return search_id, now


def resolve_search_result(house: Any, search_id: str, rank: int) -> dict[str, Any]:
    ensure_schema(house)
    clean = str(search_id or "").strip()
    if not clean:
        raise ValueError("search_id is required")
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT r.*, s.query_hash, s.provider, s.fetched_at
            FROM library_web_search_results r
            JOIN library_web_searches s ON s.id=r.search_id
            WHERE r.search_id=? AND r.rank=? AND r.resident_id=?
            """,
            (clean, int(rank), house.resident_id),
        ).fetchone()
    if row is None:
        raise KeyError("unknown search result")
    return dict(row)


def store_source(
    house: Any,
    *,
    fetched: FetchResult,
    extraction: ExtractionResult,
) -> dict[str, Any]:
    ensure_schema(house)
    raw_hash = _sha256_bytes(fetched.body)
    raw_path = _store_content_addressed(house.home, raw_hash, ".raw", fetched.body)
    readable_hash: str | None = None
    readable_path: str | None = None
    readable_size = 0
    if extraction.text:
        readable = extraction.text.encode("utf-8")
        readable_hash = _sha256_bytes(readable)
        readable_path = _store_content_addressed(house.home, readable_hash, ".txt", readable)
        readable_size = len(readable)
    source_id = new_id("source")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO library_sources
            (id, resident_id, original_url, final_url, title, media_type,
             http_status, raw_hash, raw_size_bytes, raw_path, readable_hash,
             readable_size_bytes, readable_path, extraction_method,
             redirect_chain_json, response_headers_json, warnings_json,
             risk_signals_json, trust_class, authority_state, review_state,
             fetched_at, elapsed_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                house.resident_id,
                fetched.original_url,
                fetched.final_url,
                extraction.title,
                fetched.media_type,
                int(fetched.status),
                raw_hash,
                len(fetched.body),
                raw_path,
                readable_hash,
                readable_size,
                readable_path,
                extraction.method,
                stable_json(list(fetched.redirect_chain)),
                stable_json(fetched.response_headers),
                stable_json(list(extraction.warnings)),
                stable_json(list(extraction.risk_signals)),
                "remote_untrusted",
                "evidence_only_not_authority",
                "unreviewed",
                now,
                int(fetched.elapsed_ms),
            ),
        )
    house.legible.register_object(
        object_type="web_source",
        locator=f"research/sources/{source_id}",
        content_hash=raw_hash,
        evidence_state="verified_snapshot",
        metadata={
            "source_id": source_id,
            "title": extraction.title,
            "media_type": fetched.media_type,
            "fetched_at": now,
            "review_state": "unreviewed",
            "readable": bool(extraction.text),
        },
        provenance={
            "source_class": "direct_remote_snapshot",
            "original_url": fetched.original_url,
            "final_url": fetched.final_url,
            "retrieved_at": now,
            "trust_class": "remote_untrusted",
            "authority_state": "evidence_only_not_authority",
            "memory_promotion": False,
            "identity_effect": False,
        },
        preferred_id=source_id,
    )
    return source_metadata(house, source_id)


def _source_row(house: Any, source_id: str) -> Any:
    ensure_schema(house)
    clean = str(source_id or "").strip()
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM library_sources WHERE id=? AND resident_id=?",
            (clean, house.resident_id),
        ).fetchone()
    if row is None:
        raise KeyError("unknown research source")
    return row


def source_metadata(house: Any, source_id: str) -> dict[str, Any]:
    row = _source_row(house, source_id)
    return {
        "source_id": str(row["id"]),
        "original_url": str(row["original_url"]),
        "final_url": str(row["final_url"]),
        "title": str(row["title"]),
        "media_type": str(row["media_type"]),
        "http_status": int(row["http_status"]),
        "content_hash": str(row["raw_hash"]),
        "raw_size_bytes": int(row["raw_size_bytes"]),
        "readable_hash": row["readable_hash"],
        "readable_size_bytes": int(row["readable_size_bytes"]),
        "readable": bool(row["readable_path"]),
        "extraction_method": str(row["extraction_method"]),
        "redirect_chain": _decode_json(row["redirect_chain_json"], []),
        "response_headers": _decode_json(row["response_headers_json"], {}),
        "warnings": _decode_json(row["warnings_json"], []),
        "risk_signals": _decode_json(row["risk_signals_json"], []),
        "trust_class": str(row["trust_class"]),
        "authority_state": str(row["authority_state"]),
        "review_state": str(row["review_state"]),
        "fetched_at": str(row["fetched_at"]),
        "elapsed_ms": int(row["elapsed_ms"]),
        "memory_promotion": False,
        "identity_effect": False,
    }


def list_sources(house: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema(house)
    limit = max(1, min(int(limit), 100))
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id FROM library_sources
            WHERE resident_id=? ORDER BY rowid DESC LIMIT ?
            """,
            (house.resident_id, limit),
        ).fetchall()
    return [source_metadata(house, str(row["id"])) for row in rows]


def _read_verified_text(house: Any, row: Any) -> str:
    relative_value = row["readable_path"]
    if not relative_value:
        raise ValueError("this source has no readable text extraction")
    relative = Path(str(relative_value))
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("stored research text path is invalid")
    path = (house.home / relative).resolve()
    root = (house.home / "research" / "sources").resolve()
    if root not in path.parents:
        raise RuntimeError("stored research text escaped the private source shelf")
    data = path.read_bytes()
    digest = _sha256_bytes(data)
    if digest != str(row["readable_hash"]):
        raise RuntimeError("stored research text hash no longer matches its source capsule")
    return data.decode("utf-8")


def read_source_chunk(
    house: Any,
    *,
    source_id: str,
    chunk: int = 0,
    max_chars: int = 6000,
) -> dict[str, Any]:
    row = _source_row(house, source_id)
    text = _read_verified_text(house, row)
    max_chars = max(1000, min(int(max_chars), 12000))
    chunk = max(0, int(chunk))
    start = chunk * max_chars
    if start >= len(text) and text:
        raise ValueError("source chunk is past the end of readable text")
    end = min(len(text), start + max_chars)
    excerpt = text[start:end]
    prefix = text[:start]
    line_start = prefix.count("\n") + 1
    line_end = line_start + excerpt.count("\n")
    return {
        "source": source_metadata(house, source_id),
        "chunk": chunk,
        "max_chars": max_chars,
        "text": excerpt,
        "more": end < len(text),
        "next_chunk": chunk + 1 if end < len(text) else None,
        "line_start": line_start,
        "line_end": line_end,
        "provenance_class": "direct_source_extraction",
        "quotation_status": "extracted_remote_text_not_resident_authorship",
        "remote_content_quarantine": {
            "active": True,
            "kind": "source_content",
            "trust_class": "remote_untrusted",
            "authority": "none",
            "instructions_executable": False,
            "memory_promotion": False,
            "allowed_followups": ["source.capsule", "research.notebook:working_only"],
        },
    }


def quote_source_lines(
    house: Any,
    *,
    source_id: str,
    start_line: int,
    end_line: int,
    max_chars: int = 6000,
) -> dict[str, Any]:
    row = _source_row(house, source_id)
    text = _read_verified_text(house, row)
    lines = text.splitlines()
    start_line = int(start_line)
    end_line = int(end_line)
    if start_line < 1 or end_line < start_line:
        raise ValueError("quote line range is invalid")
    if end_line - start_line + 1 > 40:
        raise ValueError("one quote may contain at most 40 extracted lines")
    if start_line > len(lines):
        raise ValueError("quote starts past the end of readable text")
    end_line = min(end_line, len(lines))
    quote = "\n".join(lines[start_line - 1 : end_line])
    max_chars = max(500, min(int(max_chars), 6000))
    if len(quote) > max_chars:
        raise ValueError("quoted line range exceeds the configured quote character ceiling")
    quote_hash = _sha256_bytes(quote.encode("utf-8"))
    metadata = source_metadata(house, source_id)
    return {
        "source_id": source_id,
        "title": metadata["title"],
        "url": metadata["final_url"],
        "retrieved_at": metadata["fetched_at"],
        "line_start": start_line,
        "line_end": end_line,
        "text": quote,
        "quote_hash": quote_hash,
        "provenance_class": "direct_source_quote",
        "quotation_status": "extracted_remote_text_not_resident_authorship",
        "remote_content_quarantine": {
            "active": True,
            "kind": "source_content",
            "trust_class": "remote_untrusted",
            "authority": "none",
            "instructions_executable": False,
            "memory_promotion": False,
            "allowed_followups": ["source.capsule", "research.notebook:working_only"],
        },
    }


def _notebook_row(house: Any, notebook_id: str) -> Any:
    ensure_schema(house)
    clean = str(notebook_id or "").strip()
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM research_notebooks WHERE id=? AND resident_id=?",
            (clean, house.resident_id),
        ).fetchone()
    if row is None:
        raise KeyError("unknown research notebook")
    return row


def _event(house: Any, notebook_id: str, event_type: str, payload: dict[str, Any]) -> str:
    event_id = new_id("research_event")
    payload_hash = _sha256_bytes(stable_json(payload).encode("utf-8"))
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO research_notebook_events
            (id, resident_id, notebook_id, event_type, payload_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, house.resident_id, notebook_id, event_type, payload_hash, utc_now_iso()),
        )
    return event_id


def create_notebook(house: Any, *, title: str) -> dict[str, Any]:
    ensure_schema(house)
    clean = str(title or "Research bench").strip()
    if not clean or len(clean) > 200:
        raise ValueError("research notebook title must be between 1 and 200 characters")
    notebook_id = new_id("notebook")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO research_notebooks
            (id, resident_id, title, status, retention, created_at, updated_at)
            VALUES (?, ?, ?, 'active', 'temporary', ?, ?)
            """,
            (notebook_id, house.resident_id, clean, now, now),
        )
    _event(house, notebook_id, "created", {"title": clean, "retention": "temporary"})
    house.legible.register_object(
        object_type="research_notebook",
        locator=f"research/notebooks/{notebook_id}",
        evidence_state="verified_now",
        metadata={"notebook_id": notebook_id, "title": clean, "status": "active", "retention": "temporary"},
        provenance={
            "source": "resident_research_workbench",
            "memory_promotion": False,
            "identity_effect": False,
            "disposable_by_default": True,
        },
        preferred_id=notebook_id,
    )
    return notebook_view(house, notebook_id)


def list_notebooks(house: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema(house)
    limit = max(1, min(int(limit), 100))
    with house.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT n.*,
                   (SELECT COUNT(*) FROM research_notebook_sources s WHERE s.notebook_id=n.id) AS source_count,
                   (SELECT COUNT(*) FROM research_notebook_notes x WHERE x.notebook_id=n.id) AS note_count
            FROM research_notebooks n
            WHERE n.resident_id=?
            ORDER BY n.rowid DESC LIMIT ?
            """,
            (house.resident_id, limit),
        ).fetchall()
    return [
        {
            "notebook_id": str(row["id"]),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "retention": str(row["retention"]),
            "source_count": int(row["source_count"]),
            "note_count": int(row["note_count"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "memory_promotion": False,
        }
        for row in rows
    ]


def notebook_view(house: Any, notebook_id: str) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    with house.db.connect() as connection:
        source_rows = connection.execute(
            """
            SELECT s.id, s.title, s.final_url, s.media_type, s.fetched_at, s.review_state
            FROM research_notebook_sources ns
            JOIN library_sources s ON s.id=ns.source_id
            WHERE ns.notebook_id=? AND ns.resident_id=?
            ORDER BY ns.rowid LIMIT 50
            """,
            (notebook_id, house.resident_id),
        ).fetchall()
        note_rows = connection.execute(
            """
            SELECT id, kind, content, content_hash, source_ids_json, authority_state, created_at
            FROM research_notebook_notes
            WHERE notebook_id=? AND resident_id=?
            ORDER BY rowid LIMIT 50
            """,
            (notebook_id, house.resident_id),
        ).fetchall()
    return {
        "notebook_id": str(row["id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "retention": str(row["retention"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "sources": [
            {
                "source_id": str(item["id"]),
                "title": str(item["title"]),
                "url": str(item["final_url"]),
                "media_type": str(item["media_type"]),
                "fetched_at": str(item["fetched_at"]),
                "review_state": str(item["review_state"]),
            }
            for item in source_rows
        ],
        "notes": [
            {
                "note_id": str(item["id"]),
                "kind": str(item["kind"]),
                "excerpt": str(item["content"])[:1200],
                "content_hash": str(item["content_hash"]),
                "source_ids": _decode_json(item["source_ids_json"], []),
                "authority_state": str(item["authority_state"]),
                "created_at": str(item["created_at"]),
            }
            for item in note_rows
        ],
        "authority": "working_research_only_not_memory_or_identity",
        "memory_promotion": False,
        "outward_effect": "none",
    }


def add_notebook_source(house: Any, *, notebook_id: str, source_id: str) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    if str(row["status"]) != "active":
        raise PermissionError("research notebook is not active")
    _source_row(house, source_id)
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO research_notebook_sources
            (notebook_id, resident_id, source_id, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (notebook_id, house.resident_id, source_id, now),
        )
        connection.execute(
            "UPDATE research_notebooks SET updated_at=? WHERE id=? AND resident_id=?",
            (now, notebook_id, house.resident_id),
        )
    _event(house, notebook_id, "source_added", {"source_id": source_id})
    return notebook_view(house, notebook_id)


def remove_notebook_source(house: Any, *, notebook_id: str, source_id: str) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    if str(row["status"]) != "active":
        raise PermissionError("research notebook is not active")
    with house.db.connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM research_notebook_sources
            WHERE notebook_id=? AND resident_id=? AND source_id=?
            """,
            (notebook_id, house.resident_id, source_id),
        )
        connection.execute(
            "UPDATE research_notebooks SET updated_at=? WHERE id=? AND resident_id=?",
            (utc_now_iso(), notebook_id, house.resident_id),
        )
    if cursor.rowcount != 1:
        raise KeyError("source is not attached to this notebook")
    _event(house, notebook_id, "source_removed", {"source_id": source_id})
    return notebook_view(house, notebook_id)


def add_notebook_note(
    house: Any,
    *,
    notebook_id: str,
    kind: str,
    content: str,
    source_ids: Iterable[str] = (),
) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    if str(row["status"]) != "active":
        raise PermissionError("research notebook is not active")
    normalized_kind = str(kind or "observation").strip().lower()
    if normalized_kind not in _NOTE_KINDS:
        raise ValueError(f"note kind must be one of {sorted(_NOTE_KINDS)}")
    clean = str(content or "").strip()
    if not clean or len(clean) > 12_000:
        raise ValueError("research note must be between 1 and 12000 characters")
    normalized_sources: list[str] = []
    for source_id in source_ids:
        clean_id = str(source_id).strip()
        if clean_id and clean_id not in normalized_sources:
            _source_row(house, clean_id)
            normalized_sources.append(clean_id)
        if len(normalized_sources) >= 20:
            break
    note_id = new_id("research_note")
    now = utc_now_iso()
    digest = _sha256_bytes(clean.encode("utf-8"))
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO research_notebook_notes
            (id, notebook_id, resident_id, kind, content, content_hash,
             source_ids_json, authority_state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'resident_working_note_unendorsed', ?)
            """,
            (
                note_id,
                notebook_id,
                house.resident_id,
                normalized_kind,
                clean,
                digest,
                stable_json(normalized_sources),
                now,
            ),
        )
        connection.execute(
            "UPDATE research_notebooks SET updated_at=? WHERE id=? AND resident_id=?",
            (now, notebook_id, house.resident_id),
        )
    _event(
        house,
        notebook_id,
        "note_added",
        {"note_id": note_id, "kind": normalized_kind, "content_hash": digest, "source_ids": normalized_sources},
    )
    return {
        "note_id": note_id,
        "notebook_id": notebook_id,
        "kind": normalized_kind,
        "content_hash": digest,
        "source_ids": normalized_sources,
        "provenance_class": "resident_working_note",
        "authority_state": "resident_working_note_unendorsed",
        "memory_promotion": False,
        "identity_effect": False,
    }


def read_notebook_note(house: Any, *, notebook_id: str, note_id: str) -> dict[str, Any]:
    _notebook_row(house, notebook_id)
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM research_notebook_notes
            WHERE id=? AND notebook_id=? AND resident_id=?
            """,
            (note_id, notebook_id, house.resident_id),
        ).fetchone()
    if row is None:
        raise KeyError("unknown research notebook note")
    return {
        "note_id": str(row["id"]),
        "notebook_id": str(row["notebook_id"]),
        "kind": str(row["kind"]),
        "content": str(row["content"]),
        "content_hash": str(row["content_hash"]),
        "source_ids": _decode_json(row["source_ids_json"], []),
        "provenance_class": "resident_working_note",
        "authority_state": str(row["authority_state"]),
        "memory_promotion": False,
        "identity_effect": False,
    }


def retain_notebook(house: Any, *, notebook_id: str) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    if str(row["status"]) != "active":
        raise PermissionError("research notebook is not active")
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute(
            "UPDATE research_notebooks SET retention='retained', updated_at=? WHERE id=? AND resident_id=?",
            (now, notebook_id, house.resident_id),
        )
    _event(house, notebook_id, "retained", {"retention": "retained"})
    updated = notebook_view(house, notebook_id)
    house.legible.register_object(
        object_type="research_notebook",
        locator=f"research/notebooks/{notebook_id}",
        evidence_state="verified_now",
        metadata={
            "notebook_id": notebook_id,
            "title": updated["title"],
            "status": "active",
            "retention": "retained",
        },
        provenance={
            "source": "resident_research_workbench",
            "memory_promotion": False,
            "identity_effect": False,
            "disposable_by_default": False,
        },
        preferred_id=notebook_id,
    )
    return updated


def discard_notebook(house: Any, *, notebook_id: str) -> dict[str, Any]:
    row = _notebook_row(house, notebook_id)
    title_hash = _sha256_bytes(str(row["title"]).encode("utf-8"))
    with house.db.connect() as connection:
        note_count = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM research_notebook_notes WHERE notebook_id=? AND resident_id=?",
                (notebook_id, house.resident_id),
            ).fetchone()["n"]
        )
        source_count = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM research_notebook_sources WHERE notebook_id=? AND resident_id=?",
                (notebook_id, house.resident_id),
            ).fetchone()["n"]
        )
    _event(
        house,
        notebook_id,
        "discarded",
        {"title_hash": title_hash, "deleted_notes": note_count, "detached_sources": source_count},
    )
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM research_notebook_notes WHERE notebook_id=? AND resident_id=?",
            (notebook_id, house.resident_id),
        )
        connection.execute(
            "DELETE FROM research_notebook_sources WHERE notebook_id=? AND resident_id=?",
            (notebook_id, house.resident_id),
        )
        cursor = connection.execute(
            "DELETE FROM research_notebooks WHERE id=? AND resident_id=?",
            (notebook_id, house.resident_id),
        )
    if cursor.rowcount != 1:
        raise KeyError("unknown research notebook")
    house.legible.register_object(
        object_type="research_notebook",
        locator=f"research/notebooks/{notebook_id}",
        evidence_state="discarded",
        metadata={
            "notebook_id": notebook_id,
            "status": "discarded",
            "content_retained": False,
            "deleted_notes": note_count,
            "detached_sources": source_count,
        },
        provenance={
            "source": "resident_research_workbench",
            "discard_receipt": "content removed; minimal hashed event retained",
            "memory_promotion": False,
            "identity_effect": False,
        },
        preferred_id=notebook_id,
    )
    return {
        "notebook_id": notebook_id,
        "status": "discarded",
        "content_retained": False,
        "deleted_notes": note_count,
        "detached_sources": source_count,
        "source_capsules_deleted": False,
        "memory_promotion": False,
    }


def observatory_summary(house: Any) -> dict[str, Any]:
    ensure_schema(house)
    with house.db.connect() as connection:
        sources = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM library_sources WHERE resident_id=?",
                (house.resident_id,),
            ).fetchone()["n"]
        )
        searches = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM library_web_searches WHERE resident_id=?",
                (house.resident_id,),
            ).fetchone()["n"]
        )
        temporary = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM research_notebooks WHERE resident_id=? AND retention='temporary'",
                (house.resident_id,),
            ).fetchone()["n"]
        )
        retained = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM research_notebooks WHERE resident_id=? AND retention='retained'",
                (house.resident_id,),
            ).fetchone()["n"]
        )
        latest = connection.execute(
            """
            SELECT id, title, final_url, media_type, fetched_at, review_state
            FROM library_sources WHERE resident_id=? ORDER BY rowid DESC LIMIT 1
            """,
            (house.resident_id,),
        ).fetchone()
    return {
        "sources": sources,
        "searches": searches,
        "temporary_notebooks": temporary,
        "retained_notebooks": retained,
        "latest_source": dict(latest) if latest else None,
        "remote_content_authority": "none",
        "automatic_memory_promotion": False,
        "automatic_identity_effect": False,
        "outward_mutation": False,
    }
