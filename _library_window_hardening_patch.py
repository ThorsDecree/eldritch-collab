from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"patch anchor missing in {rel}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1))


CONFIG = "VESTIGIA_Runtime/src/vestigia/config.py"
LIBRARY = "VESTIGIA_Runtime/src/vestigia/library_window.py"
DOC = "VESTIGIA_Runtime/docs/LIBRARY_WINDOW_RUNTIME.md"
TEST = "VESTIGIA_Runtime/tests/test_library_window.py"

# 1. Network capability is an explicit opt-in, with bounded shelf quotas.
config = read(CONFIG)
if '    "web": {\n' not in config:
    marker = '    "discord": {\n'
    block = '''    "web": {
        "enabled": False,
        "allow_http": False,
        "timeout_seconds": 12,
        "max_response_bytes": 2_000_000,
        "max_readable_chars": 200_000,
        "max_redirects": 5,
        "search_max_results": 8,
        "max_sources": 250,
        "max_total_source_bytes": 250_000_000,
    },
    "research": {
        "enabled": True,
    },
'''
    if marker not in config:
        raise RuntimeError("config DEFAULT_CONFIG insertion anchor missing")
    config = config.replace(marker, block + marker, 1)
if '"VESTIGIA_WEB_ENABLED"' not in config:
    marker = '    "VESTIGIA_DISCORD_ENABLED": ("interface.discord.enabled", _as_bool),\n'
    block = '''    "VESTIGIA_WEB_ENABLED": ("web.enabled", _as_bool),
    "VESTIGIA_WEB_ALLOW_HTTP": ("web.allow_http", _as_bool),
    "VESTIGIA_WEB_MAX_SOURCES": ("web.max_sources", _as_int),
    "VESTIGIA_WEB_MAX_TOTAL_SOURCE_BYTES": ("web.max_total_source_bytes", _as_int),
'''
    if marker not in config:
        raise RuntimeError("config ENV_MAP insertion anchor missing")
    config = config.replace(marker, block + marker, 1)
write(CONFIG, config)

# 2. Add the hardening layer without replacing the existing storage module.
HARDENING = r'''from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import library_window_store as _store
from .library_window_store import (
    ensure_schema,
    notebook_view,
    quote_source_lines,
    read_source_chunk,
    source_metadata,
    store_source,
)
from .library_window_transport import ExtractionResult, FetchResult, SEARCH_PROVIDER
from .utils import new_id, stable_json, utc_now_iso


def ensure_policy_schema(house: Any) -> None:
    ensure_schema(house)
    with house.db.connect() as connection:
        search_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(library_web_searches)").fetchall()
        }
        if "requested_turn_id" not in search_columns:
            connection.execute("ALTER TABLE library_web_searches ADD COLUMN requested_turn_id TEXT")
        source_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(library_sources)").fetchall()
        }
        additions = {
            "discovery_search_id": "TEXT",
            "discovery_rank": "INTEGER",
            "discovery_query_hash": "TEXT",
            "retrieval_eligible": "INTEGER NOT NULL DEFAULT 1",
            "revoked_at": "TEXT",
            "revoked_reason_hash": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in source_columns:
                connection.execute(f"ALTER TABLE library_sources ADD COLUMN {name} {declaration}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS library_source_events (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_library_source_events_source "
            "ON library_source_events(resident_id, source_id, created_at)"
        )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trusted_in_process_context(context: dict[str, Any]) -> bool:
    return set(context) <= {"turn_id"} and not context.get("turn_id")


def _participant_turn_text(house: Any, context: dict[str, Any]) -> str:
    turn_id = str(context.get("turn_id") or "").strip()
    if not turn_id:
        raise PermissionError("network read requires a current participant turn")
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT content FROM turns WHERE id=? AND resident_id=?",
            (turn_id, house.resident_id),
        ).fetchone()
    if row is None:
        raise PermissionError("network read could not verify the current participant turn")
    return str(row["content"])


def _query_is_participant_bound(participant_text: str, query: str) -> bool:
    participant = participant_text.casefold()
    clean_query = query.strip().casefold()
    if clean_query and clean_query in participant:
        return True
    query_terms = {
        item
        for item in re.findall(r"[\w.+:/-]+", clean_query, flags=re.UNICODE)
        if len(item) >= 2
    }
    participant_terms = set(
        re.findall(r"[\w.+:/-]+", participant, flags=re.UNICODE)
    )
    return bool(query_terms) and query_terms <= participant_terms


def _normalized_url_literal(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def authorize_network_read(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> None:
    if not bool(house.config.get("web.enabled", False)):
        raise PermissionError(
            "Library Window network reads are disabled by default; the operator must explicitly set web.enabled=true"
        )
    if _trusted_in_process_context(context):
        # Direct in-process dispatch is an operator/test API, outside the resident/provider authority path.
        return
    ensure_policy_schema(house)
    participant_text = _participant_turn_text(house, context)
    action = str(payload.get("action") or "").strip().lower()
    if action == "web.search":
        query = str(payload.get("query") or "").strip()
        if not _query_is_participant_bound(participant_text, query):
            raise PermissionError(
                "web.search query is not bounded by the current participant turn; model-private context may not be exported as search text"
            )
        return
    if action != "web.open":
        raise PermissionError("unexpected network-read capability")
    direct_url = str(payload.get("url") or "").strip()
    search_id = str(payload.get("search_id") or "").strip()
    if direct_url:
        literal = _normalized_url_literal(direct_url)
        normalized_participant = participant_text.casefold()
        if direct_url.casefold() not in normalized_participant and literal.casefold() not in normalized_participant:
            raise PermissionError(
                "direct web.open requires the exact URL to appear in the current participant turn"
            )
        return
    if search_id and payload.get("rank") is not None:
        with house.db.connect() as connection:
            row = connection.execute(
                "SELECT requested_turn_id FROM library_web_searches WHERE id=? AND resident_id=?",
                (search_id, house.resident_id),
            ).fetchone()
        if row is None or str(row["requested_turn_id"] or "") != str(context.get("turn_id") or ""):
            raise PermissionError(
                "stored-result web.open is limited to the same participant turn that authorized the search; re-search or provide the URL explicitly in a later turn"
            )
        return
    raise PermissionError("web.open requires a participant-bound direct URL or same-turn stored search result")


def authorize_notebook_lifecycle(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> None:
    mode = str(payload.get("mode") or "list").strip().lower()
    required = {
        "retain": {"retain", "keep", "save", "preserve"},
        "discard": {"discard", "delete", "remove", "forget"},
        "remove_source": {"remove", "detach"},
    }
    if mode not in required or _trusted_in_process_context(context):
        return
    text = _participant_turn_text(house, context).casefold()
    if not any(token in text for token in required[mode]):
        raise PermissionError(
            f"research notebook {mode} requires explicit current-turn participant intent"
        )


def authorize_source_management(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> None:
    if _trusted_in_process_context(context):
        return
    text = _participant_turn_text(house, context).casefold()
    if not any(token in text for token in ("revoke", "remove", "forget", "delete")):
        raise PermissionError("source revocation requires explicit current-turn participant intent")


def record_search_guarded(
    house: Any,
    *,
    query: str,
    results: list[dict[str, Any]],
    fetched: FetchResult,
    requested_turn_id: str,
) -> tuple[str, str]:
    ensure_policy_schema(house)
    search_id = _store.new_id("web_search")
    now = utc_now_iso()
    query_hash = _sha256_text(query)
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO library_web_searches
            (id, resident_id, query, query_hash, provider, result_count,
             fetched_at, elapsed_ms, requested_turn_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                requested_turn_id or None,
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


def quota_summary(house: Any) -> dict[str, int]:
    ensure_policy_schema(house)
    max_sources = max(1, min(int(house.config.get("web.max_sources", 250)), 10000))
    max_bytes = max(1_000_000, min(int(house.config.get("web.max_total_source_bytes", 250_000_000)), 20_000_000_000))
    with house.db.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(raw_size_bytes), 0) AS bytes "
            "FROM library_sources WHERE resident_id=?",
            (house.resident_id,),
        ).fetchone()
    return {
        "source_count": int(row["n"]),
        "source_bytes": int(row["bytes"]),
        "max_sources": max_sources,
        "max_total_source_bytes": max_bytes,
    }


def store_source_guarded(
    house: Any,
    *,
    fetched: FetchResult,
    extraction: ExtractionResult,
    search_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    ensure_policy_schema(house)
    quota = quota_summary(house)
    if quota["source_count"] >= quota["max_sources"]:
        raise PermissionError("Library Window source-count quota reached")
    if quota["source_bytes"] + len(fetched.body) > quota["max_total_source_bytes"]:
        raise PermissionError("Library Window aggregate source-byte quota reached")
    source = store_source(house, fetched=fetched, extraction=extraction)
    discovery_search_id = None
    discovery_rank = None
    discovery_query_hash = None
    if search_provenance:
        discovery_search_id = str(search_provenance.get("search_id") or "") or None
        discovery_rank = int(search_provenance["rank"]) if search_provenance.get("rank") is not None else None
        if discovery_search_id:
            with house.db.connect() as connection:
                row = connection.execute(
                    "SELECT query_hash FROM library_web_searches WHERE id=? AND resident_id=?",
                    (discovery_search_id, house.resident_id),
                ).fetchone()
            if row is not None:
                discovery_query_hash = str(row["query_hash"])
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE library_sources
            SET discovery_search_id=?, discovery_rank=?, discovery_query_hash=?, retrieval_eligible=1
            WHERE id=? AND resident_id=?
            """,
            (
                discovery_search_id,
                discovery_rank,
                discovery_query_hash,
                source["source_id"],
                house.resident_id,
            ),
        )
        event_id = new_id("source_event")
        payload_hash = _sha256_text(
            stable_json(
                {
                    "source_id": source["source_id"],
                    "discovery_search_id": discovery_search_id,
                    "discovery_rank": discovery_rank,
                    "discovery_query_hash": discovery_query_hash,
                    "raw_hash": source["content_hash"],
                }
            )
        )
        connection.execute(
            "INSERT INTO library_source_events "
            "(id, resident_id, source_id, event_type, payload_hash, created_at) "
            "VALUES (?, ?, ?, 'stored', ?, ?)",
            (event_id, house.resident_id, source["source_id"], payload_hash, utc_now_iso()),
        )
    return source_metadata_guarded(house, str(source["source_id"]))


def _policy_row(house: Any, source_id: str) -> Any:
    ensure_policy_schema(house)
    with house.db.connect() as connection:
        row = connection.execute(
            """
            SELECT retrieval_eligible, revoked_at, revoked_reason_hash,
                   discovery_search_id, discovery_rank, discovery_query_hash
            FROM library_sources WHERE id=? AND resident_id=?
            """,
            (source_id, house.resident_id),
        ).fetchone()
    if row is None:
        raise KeyError("unknown research source")
    return row


def source_metadata_guarded(house: Any, source_id: str) -> dict[str, Any]:
    base = source_metadata(house, source_id)
    policy = _policy_row(house, source_id)
    base.update(
        {
            "retrieval_eligible": bool(policy["retrieval_eligible"]),
            "revoked_at": policy["revoked_at"],
            "discovery_provenance": (
                {
                    "search_id": str(policy["discovery_search_id"]),
                    "rank": int(policy["discovery_rank"]),
                    "query_hash": str(policy["discovery_query_hash"] or ""),
                }
                if policy["discovery_search_id"]
                else None
            ),
        }
    )
    return base


def list_sources_guarded(house: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    ensure_policy_schema(house)
    limit = max(1, min(int(limit), 100))
    with house.db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM library_sources WHERE resident_id=? ORDER BY rowid DESC LIMIT ?",
            (house.resident_id, limit),
        ).fetchall()
    return [source_metadata_guarded(house, str(row["id"])) for row in rows]


def _require_retrievable(house: Any, source_id: str) -> None:
    row = _policy_row(house, source_id)
    if not bool(row["retrieval_eligible"]):
        raise PermissionError("research source has been revoked and is no longer eligible for retrieval")


def read_source_chunk_guarded(house: Any, **kwargs: Any) -> dict[str, Any]:
    source_id = str(kwargs.get("source_id") or "")
    _require_retrievable(house, source_id)
    result = read_source_chunk(house, **kwargs)
    result["source"] = source_metadata_guarded(house, source_id)
    return result


def quote_source_lines_guarded(house: Any, **kwargs: Any) -> dict[str, Any]:
    source_id = str(kwargs.get("source_id") or "")
    _require_retrievable(house, source_id)
    result = quote_source_lines(house, **kwargs)
    result["discovery_provenance"] = source_metadata_guarded(house, source_id)["discovery_provenance"]
    return result


def revoke_source(house: Any, *, source_id: str, reason: str) -> dict[str, Any]:
    ensure_policy_schema(house)
    clean_reason = str(reason or "resident revoked retrieval eligibility").strip()[:500]
    now = utc_now_iso()
    reason_hash = _sha256_text(clean_reason)
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE library_sources
            SET retrieval_eligible=0, review_state='revoked', revoked_at=?, revoked_reason_hash=?
            WHERE id=? AND resident_id=? AND retrieval_eligible=1
            """,
            (now, reason_hash, source_id, house.resident_id),
        )
        if cursor.rowcount != 1:
            existing = connection.execute(
                "SELECT id FROM library_sources WHERE id=? AND resident_id=?",
                (source_id, house.resident_id),
            ).fetchone()
            if existing is None:
                raise KeyError("unknown research source")
            raise PermissionError("research source is already revoked")
        connection.execute(
            "INSERT INTO library_source_events "
            "(id, resident_id, source_id, event_type, payload_hash, created_at) "
            "VALUES (?, ?, ?, 'revoked', ?, ?)",
            (new_id("source_event"), house.resident_id, source_id, reason_hash, now),
        )
    return source_metadata_guarded(house, source_id)


def retain_notebook_atomic(house: Any, *, notebook_id: str) -> dict[str, Any]:
    ensure_policy_schema(house)
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT title, status FROM research_notebooks WHERE id=? AND resident_id=?",
            (notebook_id, house.resident_id),
        ).fetchone()
        if row is None:
            raise KeyError("unknown research notebook")
        if str(row["status"]) != "active":
            raise PermissionError("research notebook is not active")
        connection.execute(
            "UPDATE research_notebooks SET retention='retained', updated_at=? WHERE id=? AND resident_id=?",
            (now, notebook_id, house.resident_id),
        )
        payload = {"retention": "retained"}
        connection.execute(
            "INSERT INTO research_notebook_events "
            "(id, resident_id, notebook_id, event_type, payload_hash, created_at) "
            "VALUES (?, ?, ?, 'retained', ?, ?)",
            (new_id("research_event"), house.resident_id, notebook_id, _sha256_text(stable_json(payload)), now),
        )
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
            "lifecycle_event_atomic": True,
        },
        preferred_id=notebook_id,
    )
    return updated


def discard_notebook_atomic(house: Any, *, notebook_id: str) -> dict[str, Any]:
    ensure_policy_schema(house)
    now = utc_now_iso()
    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT title FROM research_notebooks WHERE id=? AND resident_id=?",
            (notebook_id, house.resident_id),
        ).fetchone()
        if row is None:
            raise KeyError("unknown research notebook")
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
        payload = {
            "title_hash": _sha256_text(str(row["title"])),
            "deleted_notes": note_count,
            "detached_sources": source_count,
        }
        connection.execute(
            "INSERT INTO research_notebook_events "
            "(id, resident_id, notebook_id, event_type, payload_hash, created_at) "
            "VALUES (?, ?, ?, 'discarded', ?, ?)",
            (new_id("research_event"), house.resident_id, notebook_id, _sha256_text(stable_json(payload)), now),
        )
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
            raise RuntimeError("research notebook discard lost its expected state")
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
            "lifecycle_event_atomic": True,
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
'''
write("VESTIGIA_Runtime/src/vestigia/library_window_hardening.py", HARDENING)

# 3. Route resident-facing Library Window behavior through the hardening layer.
library = read(LIBRARY)
if "from .library_window_hardening import" not in library:
    marker = '\n\n_SCHEMA_VERSION = "vestigia.library-window.v0.1"\n'
    imports = '''

from .library_window_hardening import (
    authorize_network_read,
    authorize_notebook_lifecycle,
    authorize_source_management,
    discard_notebook_atomic,
    ensure_policy_schema,
    list_sources_guarded,
    quota_summary,
    quote_source_lines_guarded,
    read_source_chunk_guarded,
    record_search_guarded,
    retain_notebook_atomic,
    revoke_source,
    source_metadata_guarded,
    store_source_guarded,
)
'''
    if marker not in library:
        raise RuntimeError("library import insertion anchor missing")
    library = library.replace(marker, imports + marker, 1)
library = library.replace(
    'def _handle_search(house: Any, payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:\n    ensure_schema(house)\n',
    'def _handle_search(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:\n    ensure_policy_schema(house)\n',
    1,
)
library = library.replace(
    '    search_id, fetched_at = record_search(house, query=query, results=results, fetched=fetched)\n',
    '    search_id, fetched_at = record_search_guarded(\n        house, query=query, results=results, fetched=fetched,\n        requested_turn_id=str(context.get("turn_id") or ""),\n    )\n',
    1,
)
library = library.replace(
    'def _handle_open(house: Any, payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:\n    ensure_schema(house)\n',
    'def _handle_open(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:\n    ensure_policy_schema(house)\n',
    1,
)
library = library.replace(
    '    source = store_source(house, fetched=fetched, extraction=extraction)\n',
    '    source = store_source_guarded(\n        house, fetched=fetched, extraction=extraction, search_provenance=search_provenance\n    )\n',
    1,
)
library = library.replace('"sources": list_sources(\n', '"sources": list_sources_guarded(\n', 1)
library = library.replace('"source": source_metadata(house, source_id),\n', '"source": source_metadata_guarded(house, source_id),\n', 1)
library = library.replace('**read_source_chunk(\n', '**read_source_chunk_guarded(\n', 1)
library = library.replace('**quote_source_lines(\n', '**quote_source_lines_guarded(\n', 1)
library = library.replace(
    '        elif mode == "retain":\n            result = retain_notebook(house, notebook_id=notebook_id)\n        elif mode == "discard":\n            result = discard_notebook(house, notebook_id=notebook_id)\n',
    '        elif mode == "retain":\n            result = retain_notebook_atomic(house, notebook_id=notebook_id)\n        elif mode == "discard":\n            result = discard_notebook_atomic(house, notebook_id=notebook_id)\n',
    1,
)
if "def _handle_source_manage" not in library:
    marker = '\n\ndef _handle_notebook(house: Any, payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:\n'
    block = '''

def _handle_source_manage(house: Any, payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    ensure_policy_schema(house)
    mode = str(payload.get("mode") or "revoke").strip().lower()
    if mode != "revoke":
        raise ValueError("source.manage mode must be revoke")
    source_id = str(payload.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("source_id is required")
    return {
        "schema_version": _SCHEMA_VERSION,
        "mode": mode,
        "source": revoke_source(
            house,
            source_id=source_id,
            reason=str(payload.get("reason") or "resident revoked retrieval eligibility"),
        ),
        "memory_promotion": False,
        "identity_effect": False,
        "outward_effect": "none",
    }
'''
    if marker not in library:
        raise RuntimeError("source.manage handler insertion anchor missing")
    library = library.replace(marker, block + marker, 1)
library = library.replace('def _register(house: Any) -> None:\n    ensure_schema(house)\n', 'def _register(house: Any) -> None:\n    ensure_policy_schema(house)\n', 1)
# Network actions now declare the disclosure surface and run a participant-bound authorizer.
library = library.replace(
    'effects=("network:get_search_query", "database:private_search_receipt"),',
    'effects=("network:get_search_query", "network:discloses_query_to_provider", "database:private_search_receipt"),',
    1,
)
library = library.replace(
    '        lambda payload, context: _handle_search(house, payload, context),\n    )\n    house.registry.register(\n        CapabilitySpec(\n            name="web.open",',
    '        lambda payload, context: _handle_search(house, payload, context),\n        authorizer=lambda _spec, payload, context: authorize_network_read(house, payload, context),\n    )\n    house.registry.register(\n        CapabilitySpec(\n            name="web.open",',
    1,
)
library = library.replace(
    '                "network:get_read_only",\n                "filesystem:private_inert_source_snapshot",',
    '                "network:get_read_only",\n                "network:discloses_url_to_remote_host",\n                "filesystem:private_inert_source_snapshot",',
    1,
)
library = library.replace(
    '        lambda payload, context: _handle_open(house, payload, context),\n    )\n    house.registry.register(\n        CapabilitySpec(\n            name="source.capsule",',
    '        lambda payload, context: _handle_open(house, payload, context),\n        authorizer=lambda _spec, payload, context: authorize_network_read(house, payload, context),\n    )\n    house.registry.register(\n        CapabilitySpec(\n            name="source.capsule",',
    1,
)
library = library.replace('            config_key="web.enabled",\n            group="research",\n            input_schema=object_schema(\n                {\n                    "action": {"type": "string", "const": "source.capsule"},', '            config_key="research.enabled",\n            group="research",\n            input_schema=object_schema(\n                {\n                    "action": {"type": "string", "const": "source.capsule"},', 1)
if 'name="source.manage"' not in library:
    marker = '    house.registry.register(\n        CapabilitySpec(\n            name="research.notebook",\n'
    block = '''    house.registry.register(
        CapabilitySpec(
            name="source.manage",
            description=(
                "Revoke future resident-facing retrieval eligibility for one preserved source. "
                "Revocation is local, receipted, and does not falsify historical source custody."
            ),
            effects=("database:source_lifecycle",),
            cost_class="free",
            confirmation="none",
            default_after="finish",
            result_visibility="resident_private",
            config_key="research.enabled",
            group="research",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "source.manage"},
                    "mode": {"type": "string", "enum": ["revoke"]},
                    "source_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "reason": {"type": "string", "maxLength": 500},
                    "after": after,
                },
                required=("action", "source_id"),
            ),
            example_envelopes=(
                {"action": "source.manage", "mode": "revoke", "source_id": "source_...", "after": "finish"},
            ),
            next_step="Revoked sources remain auditable but cannot be read or quoted through resident-facing source.capsule.",
        ),
        lambda payload, context: _handle_source_manage(house, payload, context),
        authorizer=lambda _spec, payload, context: authorize_source_management(house, payload, context),
    )
'''
    if marker not in library:
        raise RuntimeError("source.manage capability insertion anchor missing")
    library = library.replace(marker, block + marker, 1)
library = library.replace(
    '        lambda payload, context: _handle_notebook(house, payload, context),\n    )\n\n\ndef _observatory_panel(',
    '        lambda payload, context: _handle_notebook(house, payload, context),\n        authorizer=lambda _spec, payload, context: authorize_notebook_lifecycle(house, payload, context),\n    )\n\n\ndef _observatory_panel(',
    1,
)
library = library.replace('"enabled": bool(house.config.get("web.enabled", True)),', '"enabled": bool(house.config.get("web.enabled", False)),\n            "quota": quota_summary(house),\n            "first_request_authority": "operator_opt_in_plus_current_participant_bound_payload",', 1)
write(LIBRARY, library)

# 4. Existing Library Window tests explicitly opt into web reads; production defaults remain off.
test = read(TEST)
if "import os\n" not in test:
    test = test.replace("from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport os\n", 1)
test = test.replace(
    "    config = load_config(home)\n",
    "    with patch.dict(os.environ, {\"VESTIGIA_WEB_ENABLED\": \"true\"}):\n        config = load_config(home)\n",
)
test = test.replace('NormalizedMessage(content="Search and take temporary notes.")', 'NormalizedMessage(content="Search for paper and take temporary notes.")')
test = test.replace('NormalizedMessage(content="Search and open the first result.")', 'NormalizedMessage(content="Search for paper and open the first result.")')
write(TEST, test)

HARDENING_TESTS = r'''from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.library_window_hardening import (
    authorize_network_read,
    discard_notebook_atomic,
    ensure_policy_schema,
    quota_summary,
    read_source_chunk_guarded,
    record_search_guarded,
    revoke_source,
    store_source_guarded,
)
from vestigia.library_window_store import create_notebook
from vestigia.library_window_transport import FetchResult, extract_readable


def _house(tmp_path: Path, *, web_enabled: bool = False) -> HousePort:
    home = initialize_home(tmp_path / "home", name="Hardening Test", glyph="🔭")
    if web_enabled:
        with patch.dict(os.environ, {"VESTIGIA_WEB_ENABLED": "true"}):
            config = load_config(home)
    else:
        config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    return HousePort(config, db)


def _fetch(body: bytes, *, url: str = "https://example.com/source") -> FetchResult:
    return FetchResult(
        original_url=url,
        final_url=url,
        status=200,
        media_type="text/html",
        charset="utf-8",
        body=body,
        redirect_chain=(),
        response_headers={"content-type": "text/html"},
        elapsed_ms=5,
    )


def _turn(house: HousePort, text: str) -> str:
    return house.db.add_turn(
        resident_id=house.resident_id,
        room_id=house.room_id,
        speaker_role="user",
        speaker_id="local-user",
        content=text,
        interface="cli",
    )


def test_web_is_disabled_by_default_but_local_research_stays_available(tmp_path: Path) -> None:
    house = _house(tmp_path)
    assert house.registry.describe("web.search")[0]["callable_now"] is False
    assert house.registry.describe("web.open")[0]["callable_now"] is False
    assert house.registry.describe("source.capsule")[0]["callable_now"] is True
    assert house.registry.describe("research.notebook")[0]["callable_now"] is True


def test_network_query_must_be_bounded_by_current_participant_turn(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    turn_id = _turn(house, "Search for SQLite WAL documentation.")
    context = {"turn_id": turn_id, "interface": "cli", "invocation": "conversation"}
    authorize_network_read(house, {"action": "web.search", "query": "SQLite WAL"}, context)
    with pytest.raises(PermissionError, match="not bounded"):
        authorize_network_read(
            house,
            {"action": "web.search", "query": "SQLite WAL private-secret-4471"},
            context,
        )


def test_direct_open_requires_exact_url_in_participant_turn(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    turn_id = _turn(house, "Open https://example.com/source for me.")
    context = {"turn_id": turn_id, "interface": "cli", "invocation": "conversation"}
    authorize_network_read(house, {"action": "web.open", "url": "https://example.com/source"}, context)
    with pytest.raises(PermissionError, match="exact URL"):
        authorize_network_read(
            house,
            {"action": "web.open", "url": "https://example.com/source?leak=private-secret"},
            context,
        )


def test_search_lineage_is_durable_on_source_capsule(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    ensure_policy_schema(house)
    turn_id = _turn(house, "Search for paper.")
    fetched_search = _fetch(b"search", url="https://html.duckduckgo.com/html/?q=paper")
    results = [{"rank": 1, "title": "Paper", "url": "https://example.com/source", "snippet": "x"}]
    search_id, _ = record_search_guarded(
        house,
        query="paper",
        results=results,
        fetched=fetched_search,
        requested_turn_id=turn_id,
    )
    source_fetch = _fetch(b"<html><body>Evidence</body></html>")
    source = store_source_guarded(
        house,
        fetched=source_fetch,
        extraction=extract_readable(source_fetch),
        search_provenance={"search_id": search_id, "rank": 1},
    )
    assert source["discovery_provenance"]["search_id"] == search_id
    assert source["discovery_provenance"]["rank"] == 1
    assert source["discovery_provenance"]["query_hash"]


def test_revoked_source_cannot_be_read_through_guarded_path(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    fetched = _fetch(b"<html><body>Evidence</body></html>")
    source = store_source_guarded(
        house,
        fetched=fetched,
        extraction=extract_readable(fetched),
        search_provenance=None,
    )
    revoked = revoke_source(house, source_id=source["source_id"], reason="resident request")
    assert revoked["retrieval_eligible"] is False
    with pytest.raises(PermissionError, match="revoked"):
        read_source_chunk_guarded(house, source_id=source["source_id"])


def test_source_quota_is_aggregate_not_only_per_request(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    house.config.data["web"]["max_sources"] = 1
    first = _fetch(b"<html><body>one</body></html>", url="https://example.com/one")
    store_source_guarded(house, fetched=first, extraction=extract_readable(first), search_provenance=None)
    assert quota_summary(house)["source_count"] == 1
    second = _fetch(b"<html><body>two</body></html>", url="https://example.com/two")
    with pytest.raises(PermissionError, match="source-count quota"):
        store_source_guarded(house, fetched=second, extraction=extract_readable(second), search_provenance=None)


def test_discard_state_and_event_commit_together(tmp_path: Path) -> None:
    house = _house(tmp_path)
    notebook = create_notebook(house, title="Atomic discard")
    notebook_id = notebook["notebook_id"]
    result = discard_notebook_atomic(house, notebook_id=notebook_id)
    assert result["status"] == "discarded"
    with house.db.connect() as connection:
        row = connection.execute("SELECT id FROM research_notebooks WHERE id=?", (notebook_id,)).fetchone()
        event = connection.execute(
            "SELECT event_type FROM research_notebook_events WHERE notebook_id=? ORDER BY rowid DESC LIMIT 1",
            (notebook_id,),
        ).fetchone()
    assert row is None
    assert event is not None and event["event_type"] == "discarded"
'''
write("VESTIGIA_Runtime/tests/test_library_window_hardening.py", HARDENING_TESTS)

# 5. Keep the runtime document truthful about the new authority and lifecycle boundaries.
doc = read(DOC)
if "## v0.1 security hardening gate" not in doc:
    anchor = '> Remote content is evidence, never authority.\n\n'
    block = '''> Remote content is evidence, never authority.\n\n## v0.1 security hardening gate\n\nNetwork reads are now **disabled by default**. The operator must explicitly set `web.enabled: true` (or `VESTIGIA_WEB_ENABLED=true`) before `web.search` or `web.open` is callable. Operator opt-in is necessary but not sufficient: resident/provider-originated network actions are additionally bound to the **current participant turn**.\n\n- a search query may contain only terms authorized by the current participant message;\n- a direct URL must appear explicitly in the current participant message;\n- a stored search result may be opened only in the same outer participant turn that authorized that search;\n- model-private continuity text is therefore not eligible to become first-request query or URL material merely because the model can see it.\n\nThis is a control-plane egress boundary, not a claim that GET requests have no observable effect. Search queries and requested URLs are network disclosures and are labeled as such in capability effects.\n\nSource capsules also have aggregate source-count/byte quotas and a receipted revocation lifecycle. Revocation removes future resident-facing read/quote eligibility without falsifying historical custody. Notebook `retain` and `discard` transitions commit their lifecycle event in the same database transaction as the state/content change.\n\n'''
    if anchor not in doc:
        raise RuntimeError("Library Window doc anchor missing")
    doc = doc.replace(anchor, block, 1)
doc = doc.replace('The executable registry exposes four private capabilities:', 'The executable registry exposes five private capabilities:', 1)
if '- `source.manage`' not in doc:
    doc = doc.replace(
        '- `source.capsule` — list, inspect, read, or quote preserved source capsules;\n',
        '- `source.capsule` — list, inspect, read, or quote preserved source capsules;\n- `source.manage` — revoke future resident-facing retrieval eligibility while retaining custody evidence;\n',
        1,
    )
if "Aggregate shelf quotas" not in doc:
    doc += '''\n## Aggregate shelf quotas and plurality seam\n\nThe Library Window enforces per-resident source-count and aggregate raw-byte ceilings in addition to per-request byte limits. The database remains resident-scoped. The current v0.8 blob layout is still home-local; a future v0.9 plurality migration should make blob ownership/reference-count semantics explicit before multiple residents can share one physical content-addressed shelf.\n\n## Remaining hardening seam\n\nThe same-turn remote-content quarantine and participant-bound first-request gate materially reduce prompt-driven capability escalation and egress. A future Research Bench should go further by minimizing unrelated private continuity supplied to the model while it is actively interpreting remote evidence. That context-minimization layer is not claimed by Library Window v0.1.\n'''
write(DOC, doc)

print("Library Window hardening patch applied")
