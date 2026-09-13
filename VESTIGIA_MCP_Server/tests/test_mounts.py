import json
from pathlib import Path

import pytest

from vestigia_mcp.adapters.archive import ArchiveError
from vestigia_mcp.mounts import MOUNTS_SCHEMA_VERSION, MountRegistry


def write_registry(path: Path, mounts: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schema_version": MOUNTS_SCHEMA_VERSION, "mounts": mounts}),
        encoding="utf-8",
    )


def test_named_mount_is_read_only_relative_and_paged(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "one.md").write_text("lantern one\n", encoding="utf-8")
    (root / "two.md").write_text("lantern two\n", encoding="utf-8")
    config = tmp_path / "mounts.json"
    write_registry(
        config,
        [{"id": "visual-hoard", "root": str(root), "access": "read"}],
    )
    registry = MountRegistry(
        config,
        default_text_max_bytes=1000,
        default_media_max_bytes=2000,
    )

    status = registry.status()
    assert status["available"] is True
    assert status["canonical_archive_semantics"] is False
    assert status["mounts"][0]["id"] == "visual-hoard"

    mount = registry.get("visual-hoard")
    first = mount.source().list_paths(limit=1)
    assert first["paths"] == ["one.md"]
    second = mount.source().list_paths(limit=1, cursor=first["next_cursor"])
    assert second["paths"] == ["two.md"]

    with pytest.raises(ArchiveError, match="Absolute paths"):
        mount.source().read_text(str(root / "one.md"), 1000)


def test_named_mount_registry_rejects_unsafe_configuration(tmp_path: Path) -> None:
    relative = tmp_path / "relative.json"
    write_registry(relative, [{"id": "outside", "root": "relative/path"}])
    registry = MountRegistry(
        relative,
        default_text_max_bytes=1000,
        default_media_max_bytes=2000,
    )
    assert registry.status()["available"] is False
    with pytest.raises(ArchiveError, match="absolute"):
        registry.get("outside")

    writable = tmp_path / "writable.json"
    root = tmp_path / "root"
    root.mkdir()
    write_registry(
        writable,
        [{"id": "outside", "root": str(root), "access": "write"}],
    )
    registry = MountRegistry(
        writable,
        default_text_max_bytes=1000,
        default_media_max_bytes=2000,
    )
    with pytest.raises(ArchiveError, match="read access only"):
        registry.get("outside")
