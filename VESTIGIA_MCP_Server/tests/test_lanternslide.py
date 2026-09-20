import json
from pathlib import Path

import pytest
from PIL import Image

from vestigia_mcp.adapters.archive import ArchiveSource
from vestigia_mcp.lanternslide import LanternslideError, LanternslideService


def _write_image(path: Path, color: tuple[int, int, int], *, size=(24, 16)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG")


def _service(tmp_path: Path, *, batch=50, image_max=25_000_000, sheet_max=4_000_000):
    root = tmp_path / "archive"
    root.mkdir()
    return root, LanternslideService(
        ArchiveSource(root),
        tmp_path / "state",
        source_prefix="pics",
        catalog_path="pics/Lanternslide/catalog.json",
        scan_batch_max=batch,
        image_max_bytes=image_max,
        contact_sheet_max_bytes=sheet_max,
    )


def test_scan_resumes_and_content_ids_are_stable(tmp_path: Path) -> None:
    root, service = _service(tmp_path, batch=2)
    _write_image(root / "pics/a.png", (255, 0, 0))
    _write_image(root / "pics/b.png", (0, 255, 0))
    _write_image(root / "pics/c.png", (0, 0, 255))

    first = service.scan()
    assert first["complete"] is False
    assert first["candidate_total"] == 3
    assert first["next_offset"] == 2
    second = service.scan(scan_id=first["scan_id"])
    assert second["complete"] is True
    assert second["indexed_total"] == 3
    ids = {entry["path"]: entry["image_id"] for entry in second["entries"]}

    again = service.status()
    assert again["catalog_sha256"] == second["catalog_sha256"]
    assert service.scan(scan_id=first["scan_id"])["catalog_sha256"] == second["catalog_sha256"]
    assert ids["pics/a.png"] != ids["pics/b.png"]


def test_scan_excludes_derived_catalog_and_records_omissions(tmp_path: Path) -> None:
    root, service = _service(tmp_path, image_max=10)
    _write_image(root / "pics/valid.png", (1, 2, 3))
    (root / "pics/broken.png").parent.mkdir(parents=True, exist_ok=True)
    (root / "pics/broken.png").write_bytes(b"nope")
    (root / "pics/Lanternslide/catalog.json").parent.mkdir(parents=True)
    (root / "pics/Lanternslide/catalog.json").write_text("{}\n", encoding="utf-8")

    result = service.scan()
    assert result["candidate_total"] == 2
    assert result["indexed_total"] == 0
    assert {item["reason"] for item in result["omissions"]} == {
        "invalid_image",
        "oversize",
    }


def test_scan_rejects_wrong_active_id_and_source_change(tmp_path: Path) -> None:
    root, service = _service(tmp_path, batch=1)
    _write_image(root / "pics/a.png", (1, 2, 3))
    _write_image(root / "pics/b.png", (4, 5, 6))
    first = service.scan()
    with pytest.raises(LanternslideError, match="scan ID"):
        service.scan(scan_id="lanternslide_scan_wrong")
    _write_image(root / "pics/new.png", (4, 5, 6))
    with pytest.raises(LanternslideError, match="candidate manifest"):
        service.scan(scan_id=first["scan_id"])


def test_find_is_literal_and_can_collapse_duplicate_content(tmp_path: Path) -> None:
    root, service = _service(tmp_path)
    _write_image(root / "pics/alpha/one.png", (8, 8, 8))
    (root / "pics/alpha/two.png").parent.mkdir(parents=True, exist_ok=True)
    (root / "pics/alpha/two.png").write_bytes((root / "pics/alpha/one.png").read_bytes())
    _write_image(root / "pics/beta.png", (9, 9, 9))
    service.scan()

    found = service.find("ALPHA")
    assert [entry["path"] for entry in found["entries"]] == [
        "pics/alpha/one.png",
        "pics/alpha/two.png",
    ]
    unique = service.find("alpha", unique_only=True)
    assert len(unique["entries"]) == 1


def test_incomplete_catalog_cannot_export_or_deal(tmp_path: Path) -> None:
    root, service = _service(tmp_path, batch=1)
    _write_image(root / "pics/a.png", (1, 2, 3))
    _write_image(root / "pics/b.png", (4, 5, 6))
    result = service.scan()
    with pytest.raises(LanternslideError, match="complete"):
        service.catalog_export()
    with pytest.raises(LanternslideError, match="complete"):
        service.deal(1, "seed")
    assert service.status()["scan_id"] == result["scan_id"]


def test_contact_sheet_is_png_and_rejects_unknown_or_repeated_ids(tmp_path: Path) -> None:
    root, service = _service(tmp_path)
    _write_image(root / "pics/a.png", (255, 0, 0))
    _write_image(root / "pics/b.png", (0, 0, 255))
    result = service.scan()
    ids = [entry["image_id"] for entry in result["entries"]]

    sheet = service.contact_sheet(ids)
    assert sheet["mime_type"] == "image/png"
    assert sheet["data"].startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(LanternslideError, match="unknown"):
        service.contact_sheet(["0" * 64])
    with pytest.raises(LanternslideError, match="duplicate"):
        service.contact_sheet([ids[0], ids[0]])


def test_catalog_export_is_line_oriented_and_reports_duplicate_groups(tmp_path: Path) -> None:
    root, service = _service(tmp_path)
    _write_image(root / "pics/one.png", (10, 10, 10))
    (root / "pics/two.png").parent.mkdir(parents=True, exist_ok=True)
    (root / "pics/two.png").write_bytes((root / "pics/one.png").read_bytes())
    service.scan()

    exported = service.catalog_export(max_bytes=100_000)
    lines = [json.loads(line) for line in exported.splitlines()]
    assert lines[0]["type"] == "catalog"
    assert sum(line.get("type") == "entry" for line in lines) == 2
    groups = [line for line in lines if line.get("type") == "duplicate_group"]
    assert len(groups) == 1
    assert groups[0]["path_count"] == 2
    assert all("Lanternslide/catalog.json" not in line.get("path", "") for line in lines)
    with pytest.raises(LanternslideError, match="ceiling"):
        service.catalog_export(max_bytes=20)
