from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Literal


ArchiveKind = Literal["directory", "zip"]
TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".log"}
)


class ArchiveError(RuntimeError):
    pass


def normalize_relative_path(value: str) -> str:
    candidate = value.replace("\\", "/").strip()
    if not candidate:
        raise ArchiveError("Path must not be empty")
    path = PurePosixPath(candidate)
    if path.is_absolute():
        raise ArchiveError("Absolute paths are not allowed")
    if any(part == ".." for part in path.parts):
        raise ArchiveError("Parent traversal is not allowed")
    if path.parts and ":" in path.parts[0]:
        raise ArchiveError("Drive-qualified paths are not allowed")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ArchiveError("Path must identify a file")
    return normalized


def normalize_prefix(value: str) -> str:
    candidate = value.replace("\\", "/").strip().strip("/")
    if not candidate:
        return ""
    return normalize_relative_path(candidate)


def _sha256_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArchiveStats:
    configured_path: str
    kind: ArchiveKind
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class ArchiveDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    unchanged_count: int

    def limited(self, limit: int) -> dict[str, object]:
        if limit <= 0:
            raise ArchiveError("Diff limit must be positive")
        return {
            "added": list(self.added[:limit]),
            "removed": list(self.removed[:limit]),
            "changed": list(self.changed[:limit]),
            "unchanged_count": self.unchanged_count,
            "totals": {
                "added": len(self.added),
                "removed": len(self.removed),
                "changed": len(self.changed),
            },
            "truncated": any(
                len(group) > limit
                for group in (self.added, self.removed, self.changed)
            ),
        }


class ArchiveSource:
    """Read-only view over an unpacked Archive directory or ZIP snapshot."""

    def __init__(self, root: Path):
        self.root = root.expanduser()

    @property
    def kind(self) -> ArchiveKind:
        if self.root.is_dir():
            return "directory"
        if self.root.is_file() and self.root.suffix.lower() == ".zip":
            return "zip"
        raise ArchiveError(
            f"Archive source must be a directory or .zip file: {self.root}"
        )

    def _directory_root(self) -> Path:
        if self.kind != "directory":
            raise ArchiveError("Archive source is not a directory")
        return self.root.resolve(strict=True)

    def _iter_directory_files(self) -> Iterator[tuple[str, Path, int]]:
        root = self._directory_root()
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name for name in dirnames if not (current_path / name).is_symlink()
            ]
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                yield relative, path, path.stat().st_size

    def _zip_members(self) -> list[tuple[str, zipfile.ZipInfo]]:
        if self.kind != "zip":
            raise ArchiveError("Archive source is not a ZIP")
        members: list[tuple[str, zipfile.ZipInfo]] = []
        seen: set[str] = set()
        with zipfile.ZipFile(self.root, "r") as archive:
            infos = archive.infolist()
        for info in infos:
            if info.is_dir():
                continue
            normalized = normalize_relative_path(info.filename)
            if normalized in seen:
                raise ArchiveError(
                    f"Duplicate normalized ZIP member path: {normalized}"
                )
            seen.add(normalized)
            members.append((normalized, info))
        return members

    def stats(self) -> ArchiveStats:
        if self.kind == "directory":
            files = list(self._iter_directory_files())
            return ArchiveStats(
                configured_path=str(self.root),
                kind="directory",
                file_count=len(files),
                total_bytes=sum(size for _, _, size in files),
            )
        members = self._zip_members()
        return ArchiveStats(
            configured_path=str(self.root),
            kind="zip",
            file_count=len(members),
            total_bytes=sum(info.file_size for _, info in members),
        )

    def list_paths(self, prefix: str = "", limit: int = 500) -> dict[str, object]:
        if limit <= 0:
            raise ArchiveError("List limit must be positive")
        normalized_prefix = normalize_prefix(prefix)
        if self.kind == "directory":
            paths = sorted(relative for relative, _, _ in self._iter_directory_files())
        else:
            paths = sorted(relative for relative, _ in self._zip_members())
        if normalized_prefix:
            boundary = normalized_prefix.rstrip("/") + "/"
            paths = [
                path
                for path in paths
                if path == normalized_prefix or path.startswith(boundary)
            ]
        return {
            "paths": paths[:limit],
            "total": len(paths),
            "truncated": len(paths) > limit,
        }

    def _resolve_directory_file(self, relative: str) -> Path:
        root = self._directory_root()
        parts = PurePosixPath(relative).parts
        unresolved = root.joinpath(*parts)
        if unresolved.is_symlink():
            raise ArchiveError("Symlink files are not readable")
        try:
            resolved = unresolved.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ArchiveError(f"Archive file not found: {relative}") from exc
        if resolved == root or root not in resolved.parents:
            raise ArchiveError("Resolved path escaped the Archive root")
        if not resolved.is_file():
            raise ArchiveError(f"Archive path is not a file: {relative}")
        return resolved

    def read_text(self, relative: str, max_bytes: int) -> str:
        normalized = normalize_relative_path(relative)
        if PurePosixPath(normalized).suffix.lower() not in TEXT_SUFFIXES:
            raise ArchiveError(
                "archive.read_text only exposes configured text-like suffixes"
            )
        if max_bytes <= 0:
            raise ArchiveError("Text byte ceiling must be positive")

        if self.kind == "directory":
            path = self._resolve_directory_file(normalized)
            size = path.stat().st_size
            if size > max_bytes:
                raise ArchiveError(
                    f"Archive text exceeds byte ceiling ({size} > {max_bytes})"
                )
            data = path.read_bytes()
        else:
            members = dict(self._zip_members())
            info = members.get(normalized)
            if info is None:
                raise ArchiveError(f"Archive file not found: {normalized}")
            if info.file_size > max_bytes:
                raise ArchiveError(
                    f"Archive text exceeds byte ceiling ({info.file_size} > {max_bytes})"
                )
            with zipfile.ZipFile(self.root, "r") as archive:
                data = archive.read(info)

        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArchiveError(
                f"Archive text is not valid UTF-8: {normalized}"
            ) from exc

    def fingerprints(self) -> dict[str, str]:
        fingerprints: dict[str, str] = {}
        if self.kind == "directory":
            for relative, path, _ in self._iter_directory_files():
                with path.open("rb") as handle:
                    fingerprints[relative] = _sha256_stream(handle)
            return fingerprints

        members = self._zip_members()
        with zipfile.ZipFile(self.root, "r") as archive:
            for relative, info in members:
                with archive.open(info, "r") as handle:
                    fingerprints[relative] = _sha256_stream(handle)
        return fingerprints

    def compare(self, other: "ArchiveSource") -> ArchiveDiff:
        here = self.fingerprints()
        there = other.fingerprints()
        here_paths = set(here)
        there_paths = set(there)
        shared = here_paths & there_paths
        return ArchiveDiff(
            added=tuple(sorted(here_paths - there_paths)),
            removed=tuple(sorted(there_paths - here_paths)),
            changed=tuple(sorted(path for path in shared if here[path] != there[path])),
            unchanged_count=sum(1 for path in shared if here[path] == there[path]),
        )
