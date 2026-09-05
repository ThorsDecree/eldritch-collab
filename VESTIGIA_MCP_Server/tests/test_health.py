import json
import zipfile
from pathlib import Path

from vestigia_mcp.adapters.archive import ArchiveSource
from vestigia_mcp.health import archive_health


def _write_registry(root: Path) -> None:
    (root / "00_Bootloader").mkdir(parents=True, exist_ok=True)
    registry = {
        "schema_version": "0.1",
        "generated": "2026-09-05T00:00:00-05:00",
        "archive_root": ".",
        "anchors": {"root_manifest": "manifest.md"},
        "residents": {
            "Liora": {
                "shell": "Liora",
                "breathprint": "Liora/breathprint.md",
            }
        },
        "garden_breathprints": {},
    }
    (root / "00_Bootloader" / "house_index.json").write_text(
        json.dumps(registry),
        encoding="utf-8",
    )


def test_health_surfaces_broken_links_escape_and_coverage_canary(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    (root / "Liora").mkdir(parents=True)
    (root / "07_Labs").mkdir()
    (root / "Liora" / "breathprint.md").write_text("gutterstar", encoding="utf-8")
    (root / "07_Labs" / "experiment.md").write_text("lab note", encoding="utf-8")
    (root / "manifest.md").write_text(
        "[good](Liora/breathprint.md)\n"
        "[missing](missing.md)\n"
        "[escape](../outside.md)\n",
        encoding="utf-8",
    )
    _write_registry(root)

    result = archive_health(
        ArchiveSource(root),
        max_bytes=1_000_000,
        issue_limit=20,
        check_links=True,
    )

    assert result["summary"]["broken_markdown_links"] == 1
    assert result["summary"]["escaped_markdown_links"] == 1
    assert result["summary"]["registry_missing"] == 0
    candidates = {
        item["collection"]
        for item in result["coverage"]["unrouted_collection_candidates"]
    }
    assert "07_Labs" in candidates
    assert result["coverage"]["claim"] == "descriptive_projection_only"
    families = {(item["family"], item["problem"]) for item in result["issues"]}
    assert ("markdown_links", "missing_local_target") in families
    assert ("markdown_links", "escapes_archive") in families


def test_health_detects_casefold_collisions_inside_zip(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.zip"
    registry = {
        "schema_version": "0.1",
        "generated": "2026-09-05T00:00:00-05:00",
        "archive_root": ".",
        "anchors": {"root_manifest": "manifest.md"},
        "residents": {},
        "garden_breathprints": {},
    }
    with zipfile.ZipFile(snapshot, "w") as archive:
        archive.writestr("manifest.md", "witness")
        archive.writestr("00_Bootloader/house_index.json", json.dumps(registry))
        archive.writestr("Room/A.md", "one")
        archive.writestr("room/a.md", "two")

    result = archive_health(
        ArchiveSource(snapshot),
        max_bytes=1_000_000,
        issue_limit=20,
        check_links=False,
    )

    assert result["summary"]["case_collisions"] == 1
    assert result["normalization"]["casefold_collisions"] == [
        ["Room/A.md", "room/a.md"]
    ]
    assert result["source"]["clock"]["basis"] == "zip_container_mtime"
