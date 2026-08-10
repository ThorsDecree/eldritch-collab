from __future__ import annotations

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
    """Persist one source capsule with lineage and custody event atomically.

    Content-addressed bytes are written before the SQLite transaction. They are inert
    and may be safely orphaned by an interrupted transaction, but no resident-visible
    source row is committed unless its discovery lineage and `stored` custody event
    commit in the same transaction.
    """

    ensure_policy_schema(house)

    # Fast preflight avoids unnecessary CAS writes in the common quota-exhausted case.
    # The quota is checked again under BEGIN IMMEDIATE below, which is authoritative.
    preflight = quota_summary(house)
    if preflight["source_count"] >= preflight["max_sources"]:
        raise PermissionError("Library Window source-count quota reached")
    if preflight["source_bytes"] + len(fetched.body) > preflight["max_total_source_bytes"]:
        raise PermissionError("Library Window aggregate source-byte quota reached")

    raw_hash = _store._sha256_bytes(fetched.body)
    raw_path = _store._store_content_addressed(
        house.home, raw_hash, ".raw", fetched.body
    )
    readable_hash: str | None = None
    readable_path: str | None = None
    readable_size = 0
    if extraction.text:
        readable = extraction.text.encode("utf-8")
        readable_hash = _store._sha256_bytes(readable)
        readable_path = _store._store_content_addressed(
            house.home, readable_hash, ".txt", readable
        )
        readable_size = len(readable)

    source_id = _store.new_id("source")
    event_id = new_id("source_event")
    now = utc_now_iso()
    discovery_search_id: str | None = None
    discovery_rank: int | None = None
    discovery_query_hash: str | None = None
    if search_provenance:
        discovery_search_id = str(search_provenance.get("search_id") or "").strip() or None
        if search_provenance.get("rank") is not None:
            discovery_rank = int(search_provenance["rank"])
        if discovery_search_id and discovery_rank is None:
            raise ValueError("search provenance requires rank when search_id is present")

    with house.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        # Re-check quota under the write lock so concurrent source creation cannot race
        # two individually-valid writes past the aggregate resident ceiling.
        quota_row = connection.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(raw_size_bytes), 0) AS bytes "
            "FROM library_sources WHERE resident_id=?",
            (house.resident_id,),
        ).fetchone()
        max_sources = max(1, min(int(house.config.get("web.max_sources", 250)), 10000))
        max_bytes = max(
            1_000_000,
            min(
                int(house.config.get("web.max_total_source_bytes", 250_000_000)),
                20_000_000_000,
            ),
        )
        if int(quota_row["n"]) >= max_sources:
            raise PermissionError("Library Window source-count quota reached")
        if int(quota_row["bytes"]) + len(fetched.body) > max_bytes:
            raise PermissionError("Library Window aggregate source-byte quota reached")

        if discovery_search_id is not None:
            provenance_row = connection.execute(
                """
                SELECT s.query_hash
                FROM library_web_searches s
                JOIN library_web_search_results r
                  ON r.search_id=s.id AND r.resident_id=s.resident_id
                WHERE s.id=? AND s.resident_id=? AND r.rank=?
                """,
                (discovery_search_id, house.resident_id, discovery_rank),
            ).fetchone()
            if provenance_row is None:
                raise KeyError("unknown discovery search result")
            discovery_query_hash = str(provenance_row["query_hash"])

        connection.execute(
            """
            INSERT INTO library_sources
            (id, resident_id, original_url, final_url, title, media_type,
             http_status, raw_hash, raw_size_bytes, raw_path, readable_hash,
             readable_size_bytes, readable_path, extraction_method,
             redirect_chain_json, response_headers_json, warnings_json,
             risk_signals_json, trust_class, authority_state, review_state,
             fetched_at, elapsed_ms, discovery_search_id, discovery_rank,
             discovery_query_hash, retrieval_eligible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
                discovery_search_id,
                discovery_rank,
                discovery_query_hash,
            ),
        )

        payload_hash = _sha256_text(
            stable_json(
                {
                    "source_id": source_id,
                    "discovery_search_id": discovery_search_id,
                    "discovery_rank": discovery_rank,
                    "discovery_query_hash": discovery_query_hash,
                    "raw_hash": raw_hash,
                }
            )
        )
        connection.execute(
            """
            INSERT INTO library_source_events
            (id, resident_id, source_id, event_type, payload_hash, created_at)
            VALUES (?, ?, ?, 'stored', ?, ?)
            """,
            (event_id, house.resident_id, source_id, payload_hash, now),
        )

    # Legible-object indexing happens only after the custody transaction commits. A
    # failed index update cannot create a provenance-bearing object for an uncommitted
    # source record.
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
            "discovery_search_id": discovery_search_id,
            "discovery_rank": discovery_rank,
            "discovery_query_hash": discovery_query_hash,
            "memory_promotion": False,
            "identity_effect": False,
        },
        preferred_id=source_id,
    )
    return source_metadata_guarded(house, source_id)

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
