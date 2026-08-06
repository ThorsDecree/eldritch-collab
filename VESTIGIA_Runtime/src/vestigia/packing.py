from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .db import ContinuityDB
from .home import validate_home
from .utils import sha256_file, stable_json, utc_now_iso


EXCLUDED_NAMES = {".env", ".env.local"}
EXCLUDED_SUFFIXES = {".db-wal", ".db-shm", ".pyc"}


def _collect_pack_files(home: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    files: list[tuple[Path, str]] = []
    issues: list[str] = []
    for path in sorted(home.rglob("*")):
        if path.is_symlink():
            issues.append(f"symbolic link: {path.relative_to(home).as_posix()}")
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(home)
        if path.name in EXCLUDED_NAMES or any(
            path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES
        ):
            continue
        if "__pycache__" in relative.parts:
            continue
        files.append((path, relative.as_posix()))
    return files, issues


def inspect_home_pack(home_path: str | Path) -> dict[str, Any]:
    home = validate_home(home_path)
    files, issues = _collect_pack_files(home)
    manifest = {
        "schema_version": "vestigia.pack-inspection.v0.1",
        "home_name": home.name,
        "file_count": len(files),
        "total_size_bytes": sum(path.stat().st_size for path, _ in files),
        "exclusions": sorted(EXCLUDED_NAMES | EXCLUDED_SUFFIXES),
        "files": [
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
            }
            for path, relative in files
        ],
    }
    return {"packable": not issues, "issues": issues, "manifest": manifest}


def pack_home(home_path: str | Path, output: str | Path | None = None) -> Path:
    home = validate_home(home_path)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.checkpoint()
    target = (
        Path(output).resolve()
        if output
        else home.parent / f"{home.name}.vestigia.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    files, issues = _collect_pack_files(home)
    if issues:
        raise ValueError("Refusing to pack unsafe home entries: " + "; ".join(issues))
    manifest: dict[str, Any] = {
        "schema_version": "vestigia.pack.v0.1",
        "created_at": utc_now_iso(),
        "home_name": home.name,
        "exclusions": sorted(EXCLUDED_NAMES | EXCLUDED_SUFFIXES),
        "files": [
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path, relative in files
        ],
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.tmp-",
            suffix=".zip",
            dir=target.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "PACK_MANIFEST.json",
                json.dumps(manifest, indent=2, sort_keys=True),
            )
            for path, relative in files:
                archive.write(path, relative)
        with zipfile.ZipFile(temp_path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("Pack integrity verification failed")
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return target


def restore_home(
    archive_path: str | Path,
    target_path: str | Path,
    *,
    allow_existing_empty: bool = True,
) -> Path:
    archive_file = Path(archive_path).resolve()
    target = Path(target_path).resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Refusing to restore over non-empty directory: {target}")
    if target.exists() and not allow_existing_empty:
        raise FileExistsError(f"Target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_file, "r") as archive:
        names = archive.namelist()
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"Unsafe archive member: {name}")
        try:
            manifest = json.loads(archive.read("PACK_MANIFEST.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Archive does not contain a valid PACK_MANIFEST.json") from exc
        with tempfile.TemporaryDirectory(prefix="vestigia-restore-", dir=target.parent) as raw:
            staging = Path(raw) / "home"
            staging.mkdir()
            for member in archive.infolist():
                if member.filename == "PACK_MANIFEST.json" or member.is_dir():
                    continue
                destination = staging / PurePosixPath(member.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
            expected = {item["path"]: item["sha256"] for item in manifest.get("files", [])}
            actual = {
                path.relative_to(staging).as_posix(): sha256_file(path)
                for path in staging.rglob("*")
                if path.is_file()
            }
            if stable_json(actual) != stable_json(expected):
                raise ValueError("Archive hash verification failed")
            validate_home(staging)
            if target.exists():
                target.rmdir()
            shutil.move(str(staging), str(target))
    db = ContinuityDB(target / "memory" / "continuity.db")
    db.initialize()
    restored_config = yaml.safe_load((target / "home.yaml").read_text(encoding="utf-8"))
    resident_id = str(restored_config["resident"]["id"])
    current = db.current_state(resident_id)
    if current == "ARCHIVED":
        db.append_state(
            resident_id=resident_id,
            from_state="ARCHIVED",
            to_state="AWAKENING",
            actor="restore-home",
            reason="archived home restored from verified pack",
        )
    return validate_home(target)
