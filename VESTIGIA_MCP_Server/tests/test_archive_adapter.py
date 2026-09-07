from pathlib import Path
from zipfile import ZipFile

import pytest

from vestigia_mcp.adapters.archive import ArchiveError, ArchiveSource


PNG = b"\x89PNG\r\n\x1a\n" + b"bounded-fixture"


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
