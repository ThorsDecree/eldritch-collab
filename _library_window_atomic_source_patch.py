# Trigger the one-shot atomic source validation runner.
from pathlib import Path

ROOT = Path(__file__).parent
HARDENING = ROOT / "VESTIGIA_Runtime/src/vestigia/library_window_hardening.py"
TESTS = ROOT / "VESTIGIA_Runtime/tests/test_library_window_hardening.py"
DOC = ROOT / "VESTIGIA_Runtime/docs/LIBRARY_WINDOW_RUNTIME.md"

text = HARDENING.read_text(encoding="utf-8")
start = text.index("def store_source_guarded(\n")
end = text.index("\ndef _policy_row(", start)
replacement = r'''def store_source_guarded(
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
'''
text = text[:start] + replacement + text[end:]
HARDENING.write_text(text, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
if "import sqlite3\n" not in tests:
    tests = tests.replace("import os\n", "import os\nimport sqlite3\n", 1)
marker = "\ndef test_revoked_source_cannot_be_read_through_guarded_path"
atomic_test = r'''

def test_source_row_lineage_and_stored_event_are_one_transaction(tmp_path: Path) -> None:
    house = _house(tmp_path, web_enabled=True)
    ensure_policy_schema(house)
    turn_id = _turn(house, "Search for paper.")
    fetched_search = _fetch(b"search", url="https://html.duckduckgo.com/html/?q=paper")
    search_id, _ = record_search_guarded(
        house,
        query="paper",
        results=[{"rank": 1, "title": "Paper", "url": "https://example.com/source", "snippet": "x"}],
        fetched=fetched_search,
        requested_turn_id=turn_id,
    )

    # Force the custody-event insert to fail after the source INSERT has executed.
    # If source creation and lineage are not in the same transaction, a partial source
    # row would survive this uniqueness failure.
    with house.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO library_source_events
            (id, resident_id, source_id, event_type, payload_hash, created_at)
            VALUES ('source_event_collision', ?, 'sentinel', 'sentinel', 'sentinel', '2000-01-01T00:00:00Z')
            """,
            (house.resident_id,),
        )

    fetched = _fetch(b"<html><body>Atomic evidence</body></html>")
    with patch("vestigia.library_window_hardening.new_id", return_value="source_event_collision"):
        with pytest.raises(sqlite3.IntegrityError):
            store_source_guarded(
                house,
                fetched=fetched,
                extraction=extract_readable(fetched),
                search_provenance={"search_id": search_id, "rank": 1},
            )

    with house.db.connect() as connection:
        source_rows = connection.execute(
            "SELECT id FROM library_sources WHERE resident_id=?",
            (house.resident_id,),
        ).fetchall()
        stored_events = connection.execute(
            "SELECT source_id FROM library_source_events WHERE resident_id=? AND event_type='stored'",
            (house.resident_id,),
        ).fetchall()
    assert source_rows == []
    assert stored_events == []
'''
if "test_source_row_lineage_and_stored_event_are_one_transaction" not in tests:
    tests = tests.replace(marker, atomic_test + marker, 1)
TESTS.write_text(tests, encoding="utf-8")

doc = DOC.read_text(encoding="utf-8")
needle = "Notebook `retain` and `discard` transitions commit their lifecycle event in the same database transaction as the state/content change."
replacement_doc = needle + " Source creation now likewise commits the source row, discovery-search lineage, retrieval eligibility, and `stored` custody event in one SQLite transaction; interrupted writes cannot expose a partially-provenanced source capsule."
if replacement_doc not in doc:
    doc = doc.replace(needle, replacement_doc, 1)
DOC.write_text(doc, encoding="utf-8")
