from pathlib import Path
from zipfile import ZipFile

import pytest

from vestigia_mcp.adapters.archive import ArchiveError, ArchiveSource


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


def test_zip_rejects_unsafe_member_path(tmp_path: Path) -> None:
    snapshot = tmp_path / "unsafe.zip"
    write_zip(snapshot, {"../escape.md": "nope"})
    with pytest.raises(ArchiveError):
        ArchiveSource(snapshot).list_paths()
