import base64
from pathlib import Path
from zipfile import ZipFile

import pytest

from vestigia_mcp.adapters.archive import ArchiveError, ArchiveSource
from vestigia_mcp.browse import BrowseSessionStore
from vestigia_mcp.pagination import encode_cursor


PNG = b"\x89PNG\r\n\x1a\n" + b"bounded-fixture"


def _store(tmp_path: Path) -> BrowseSessionStore:
    return BrowseSessionStore(
        tmp_path / "state", ttl_seconds=300, secret=b"b" * 32
    )


def write_zip(path: Path, files: dict[str, str]) -> None:
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_reads_directory_and_zip_text(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "manifest.md").write_text("live", encoding="utf-8")
    snapshot = tmp_path / "snapshot.zip"
    write_zip(snapshot, {"manifest.md": "snapshot"})

    assert ArchiveSource(live).read_text("manifest.md", 100) == "live"
    assert ArchiveSource(snapshot).read_text("manifest.md", 100) == "snapshot"


def test_rejects_traversal_and_binary_text_read(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "manifest.md").write_text("safe", encoding="utf-8")
    (live / "image.png").write_bytes(b"not really an image")
    source = ArchiveSource(live)

    with pytest.raises(ArchiveError):
        source.read_text("../manifest.md", 100)
    with pytest.raises(ArchiveError):
        source.read_text("image.png", 100)


def test_reads_bounded_signature_checked_media_from_directory_and_zip(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "image.png").write_bytes(PNG)
    media = ArchiveSource(live).read_media("image.png", 100)
    assert media.data == PNG
    assert media.mime_type == "image/png"
    assert media.size == len(PNG)
    assert len(media.sha256) == 64

    snapshot = tmp_path / "snapshot.zip"
    with ZipFile(snapshot, "w") as archive:
        archive.writestr("art/image.png", PNG)
    zipped = ArchiveSource(snapshot).read_media("art/image.png", 100)
    assert zipped.data == PNG
    assert zipped.mime_type == "image/png"


def test_media_rejects_spoofing_unsupported_types_and_byte_overflow(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "spoof.png").write_bytes(b"not-an-image")
    (live / "wrong.jpg").write_bytes(PNG)
    (live / "active.svg").write_text("<svg/>", encoding="utf-8")
    source = ArchiveSource(live)

    with pytest.raises(ArchiveError, match="signature"):
        source.read_media("spoof.png", 100)
    with pytest.raises(ArchiveError, match="does not match"):
        source.read_media("wrong.jpg", 100)
    with pytest.raises(ArchiveError, match="only exposes"):
        source.read_media("active.svg", 100)
    with pytest.raises(ArchiveError, match="byte ceiling"):
        source.read_media("wrong.jpg", 4)


def test_diff_reports_added_removed_changed_and_unchanged(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "same.md").write_text("same", encoding="utf-8")
    (live / "changed.md").write_text("new", encoding="utf-8")
    (live / "added.md").write_text("added", encoding="utf-8")

    snapshot = tmp_path / "snapshot.zip"
    write_zip(
        snapshot,
        {
            "same.md": "same",
            "changed.md": "old",
            "removed.md": "removed",
        },
    )

    diff = ArchiveSource(live).compare(ArchiveSource(snapshot))
    assert diff.added == ("added.md",)
    assert diff.removed == ("removed.md",)
    assert diff.changed == ("changed.md",)
    assert diff.unchanged_count == 1


def test_exclusion_hides_snapshot_witness_from_live_view(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "manifest.md").write_text("same", encoding="utf-8")
    snapshot = live / "Anima.zip"
    write_zip(snapshot, {"manifest.md": "same"})

    live_source = ArchiveSource(live, exclude_paths=("Anima.zip",))
    snapshot_source = ArchiveSource(snapshot)

    stats = live_source.stats()
    assert stats.file_count == 1
    assert stats.excluded_paths == ("Anima.zip",)
    assert live_source.list_paths()["paths"] == ["manifest.md"]
    assert live_source.entry("Anima.zip") is None

    diff = live_source.compare(snapshot_source)
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.changed == ()
    assert diff.unchanged_count == 1


def test_entry_hashes_only_requested_path(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "note.md").write_text("red thread", encoding="utf-8")
    source = ArchiveSource(live)

    entry = source.entry("note.md")
    assert entry is not None
    assert entry.path == "note.md"
    assert entry.size == len("red thread".encode("utf-8"))
    assert len(entry.sha256) == 64
    assert source.entry("missing.md") is None


def test_literal_search_reports_evidence_and_skips_unreadable_files(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "notes.md").write_text(
        "Lantern first\nsecond lantern\n",
        encoding="utf-8",
    )
    (live / "other.txt").write_text("nothing here", encoding="utf-8")
    (live / "legacy.txt").write_bytes(b"\xff\xfe\x00")
    (live / "huge.md").write_text("lantern " * 100, encoding="utf-8")
    (live / "image.png").write_bytes(b"lantern but binary")

    result = ArchiveSource(live).search_text(
        "LANTERN",
        limit=1,
        max_bytes=100,
    )

    assert result["match_count"] == 2
    assert result["truncated"] is True
    assert result["candidate_files"] == 4
    assert result["scanned_files"] == 2
    assert result["skipped_non_utf8"] == 1
    assert result["skipped_oversize"] == 1
    assert result["hits"] == [
        {"path": "notes.md", "line": 1, "excerpt": "Lantern first"}
    ]


def test_literal_search_honors_prefix_and_case_in_zip(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.zip"
    write_zip(
        snapshot,
        {
            "notes/a.md": "lower needle",
            "notes/b.md": "Upper Needle",
            "elsewhere/c.md": "Upper Needle",
        },
    )

    result = ArchiveSource(snapshot).search_text(
        "Needle",
        prefix="notes",
        case_sensitive=True,
    )

    assert result["match_count"] == 1
    assert result["hits"] == [
        {"path": "notes/b.md", "line": 1, "excerpt": "Upper Needle"}
    ]


def test_zip_rejects_unsafe_member_path(tmp_path: Path) -> None:
    snapshot = tmp_path / "unsafe.zip"
    write_zip(snapshot, {"../escape.md": "nope"})
    with pytest.raises(ArchiveError):
        ArchiveSource(snapshot).list_paths()


def test_list_paths_pages_with_stale_view_detection(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (live / name).write_text(name, encoding="utf-8")
    source = ArchiveSource(live)

    first = source.list_paths(limit=2)
    assert first["paths"] == ["a.md", "b.md"]
    assert first["total"] == 3
    assert first["page"]["offset"] == 0
    assert first["page"]["returned"] == 2
    assert first["page"]["has_more"] is True
    assert first["next_cursor"]

    second = source.list_paths(limit=2, cursor=str(first["next_cursor"]))
    assert second["paths"] == ["c.md"]
    assert second["page"]["offset"] == 2
    assert second["next_cursor"] is None

    (live / "d.md").write_text("new", encoding="utf-8")
    with pytest.raises(ArchiveError, match="stale"):
        source.list_paths(limit=2, cursor=str(first["next_cursor"]))


def test_read_text_pages_are_utf8_safe_and_hash_bound(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    original = ("a" * 255) + "🏮" + ("b" * 20)
    path = live / "long.md"
    path.write_text(original, encoding="utf-8")
    source = ArchiveSource(live)

    first = source.read_text_page("long.md", 1000, page_bytes=256)
    assert first["content"] == "a" * 255
    assert first["page"]["byte_end"] == 255
    assert first["next_cursor"]

    second = source.read_text_page(
        "long.md",
        1000,
        page_bytes=256,
        cursor=str(first["next_cursor"]),
    )
    assert first["content"] + second["content"] == original
    assert second["sha256"] == first["sha256"]
    assert second["next_cursor"] is None

    path.write_text(original + "changed", encoding="utf-8")
    with pytest.raises(ArchiveError, match="stale"):
        source.read_text_page(
            "long.md",
            1000,
            page_bytes=256,
            cursor=str(first["next_cursor"]),
        )


def test_large_utf8_text_pages_ignore_total_admission_ceiling_and_preserve_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "logs" / "huge.md"
    path.parent.mkdir()
    path.write_text(("α\n" * 600_000) + "tail\n", encoding="utf-8")
    source = ArchiveSource(tmp_path)

    first = source.read_text_page(
        "logs/huge.md",
        page_bytes=257,
        browse_store=_store(tmp_path),
        policy_scope="archive.read_text:test",
    )
    assert first["budget"]["returned_bytes"] <= 257
    assert first["line_start"] == 1
    assert first["snapshot_status"] == "same_snapshot"

    second = source.read_text_page(
        "logs/huge.md",
        page_bytes=257,
        cursor=str(first["next_cursor"]),
        browse_store=_store(tmp_path),
        policy_scope="archive.read_text:test",
    )
    assert second["byte_start"] == first["byte_end"]
    assert str(second["content"]).encode("utf-8")
    assert second["line_start"] >= first["line_start"]


def test_changed_artifact_returns_condition_without_mixed_page(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "changing.md"
    path.parent.mkdir()
    path.write_text("first page\nsecond page\n", encoding="utf-8")
    source = ArchiveSource(tmp_path)
    store = _store(tmp_path)

    first = source.read_text_page(
        "logs/changing.md",
        page_bytes=12,
        browse_store=store,
        policy_scope="archive.read_text:test",
    )
    path.write_text("FIRST page\nsecond page\n", encoding="utf-8")

    changed = source.read_text_page(
        "logs/changing.md",
        page_bytes=12,
        cursor=str(first["next_cursor"]),
        browse_store=store,
        policy_scope="archive.read_text:test",
    )
    assert changed["snapshot_status"] == "file_changed_during_browse"
    assert "data" not in changed
    assert "content" not in changed


def test_removed_artifact_returns_changed_browse_condition(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "removed.md"
    path.parent.mkdir()
    path.write_text("first page\nsecond page\n", encoding="utf-8")
    source = ArchiveSource(tmp_path)
    store = _store(tmp_path)
    first = source.read_text_page(
        "logs/removed.md",
        page_bytes=12,
        browse_store=store,
        policy_scope="archive.read_text:test",
    )
    path.unlink()

    changed = source.read_text_page(
        "logs/removed.md",
        page_bytes=12,
        cursor=str(first["next_cursor"]),
        browse_store=store,
        policy_scope="archive.read_text:test",
    )
    assert changed["snapshot_status"] == "file_changed_during_browse"
    assert changed["current_content_sha256"] is None
    assert "content" not in changed


def test_read_bytes_pages_return_bounded_base64_slices(tmp_path: Path) -> None:
    path = tmp_path / "db" / "runtime.sqlite"
    path.parent.mkdir()
    fixture = b"SQLite format 3\x00" + bytes(range(256))
    path.write_bytes(fixture)
    source = ArchiveSource(tmp_path)
    store = _store(tmp_path)

    first = source.read_bytes_page(
        "db/runtime.sqlite",
        page_bytes=48,
        browse_store=store,
        policy_scope="archive.read_bytes:test",
    )
    assert base64.b64decode(str(first["data"])) == fixture[:48]
    assert len(str(first["data"])) <= 64

    second = source.read_bytes_page(
        "db/runtime.sqlite",
        page_bytes=48,
        cursor=str(first["next_cursor"]),
        browse_store=store,
        policy_scope="archive.read_bytes:test",
    )
    assert base64.b64decode(str(second["data"])) == fixture[48:96]


def test_legacy_read_text_cursor_is_rejected_by_snapshot_browse(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "legacy.md"
    path.parent.mkdir()
    path.write_text("first\nsecond\n", encoding="utf-8")
    legacy_cursor = encode_cursor(
        "archive.read_text",
        {
            "path": "logs/legacy.md",
            "offset": 6,
            "sha256": "a" * 64,
            "source_sha256": "b" * 64,
        },
    )

    with pytest.raises(ArchiveError, match="unsupported legacy"):
        ArchiveSource(tmp_path).read_text_page(
            "logs/legacy.md",
            page_bytes=6,
            cursor=legacy_cursor,
            browse_store=_store(tmp_path),
            policy_scope="archive.read_text:test",
        )


def test_search_text_pages_bind_query_and_result_view(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    path = live / "notes.md"
    path.write_text("needle one\nneedle two\nneedle three\n", encoding="utf-8")
    source = ArchiveSource(live)

    first = source.search_text("needle", limit=2)
    assert [hit["line"] for hit in first["hits"]] == [1, 2]
    assert first["page"]["total"] == 3
    assert first["next_cursor"]

    second = source.search_text(
        "needle",
        limit=2,
        cursor=str(first["next_cursor"]),
    )
    assert [hit["line"] for hit in second["hits"]] == [3]
    assert second["next_cursor"] is None

    with pytest.raises(ArchiveError, match="parameters"):
        source.search_text(
            "different",
            limit=2,
            cursor=str(first["next_cursor"]),
        )

    path.write_text("needle one\nneedle changed\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="stale"):
        source.search_text(
            "needle",
            limit=2,
            cursor=str(first["next_cursor"]),
        )
