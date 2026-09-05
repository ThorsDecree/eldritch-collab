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


def _matches_prefix(path: str, prefix: str) -> bool:
    if not prefix:
        return True
    boundary = prefix.rstrip("/") + "/"
    return path == prefix or path.startswith(boundary)


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
    excluded_paths: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    size: int
    sha256: str


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

    def __init__(self, root: Path, *, exclude_paths: tuple[str, ...] = ()):
        self.root = root.expanduser()
        self._exclude_paths = frozenset(
            normalize_relative_path(path) for path in exclude_paths
        )

    @property
    def excluded_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._exclude_paths))

    def _is_excluded(self, relative: str) -> bool:
        return any(
            relative == excluded or relative.startswith(excluded.rstrip("/") + "/")
            for excluded in self._exclude_paths
        )

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
                name
                for name in dirnames
                if not (current_path / name).is_symlink()
                and not self._is_excluded(
                    (current_path / name).relative_to(root).as_posix()
                )
            ]
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                if self._is_excluded(relative):
                    continue
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
            if self._is_excluded(normalized):
                continue
            if normalized in seen:
                raise ArchiveError(
                    f"Duplicate normalized ZIP member path: {normalized}"
                )
            seen.add(normalized)
            members.append((normalized, info))
        return members

    def all_paths(self) -> tuple[str, ...]:
        if self.kind == "directory":
            return tuple(sorted(relative for relative, _, _ in self._iter_directory_files()))
        return tuple(sorted(relative for relative, _ in self._zip_members()))

    def stats(self) -> ArchiveStats:
        if self.kind == "directory":
            files = list(self._iter_directory_files())
            return ArchiveStats(
                configured_path=str(self.root),
                kind="directory",
                file_count=len(files),
                total_bytes=sum(size for _, _, size in files),
                excluded_paths=self.excluded_paths,
            )
        members = self._zip_members()
        return ArchiveStats(
            configured_path=str(self.root),
            kind="zip",
            file_count=len(members),
            total_bytes=sum(info.file_size for _, info in members),
            excluded_paths=self.excluded_paths,
        )

    def list_paths(self, prefix: str = "", limit: int = 500) -> dict[str, object]:
        if limit <= 0:
            raise ArchiveError("List limit must be positive")
        normalized_prefix = normalize_prefix(prefix)
        paths = list(self.all_paths())
        if normalized_prefix:
            paths = [path for path in paths if _matches_prefix(path, normalized_prefix)]
        return {
            "paths": paths[:limit],
            "total": len(paths),
            "truncated": len(paths) > limit,
        }

    def _resolve_directory_file(self, relative: str) -> Path:
        if self._is_excluded(relative):
            raise ArchiveError(f"Archive file is excluded from this source: {relative}")
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

    def search_text(
        self,
        query: str,
        *,
        prefix: str = "",
        limit: int = 50,
        max_bytes: int = 1_000_000,
        case_sensitive: bool = False,
        excerpt_chars: int = 240,
    ) -> dict[str, object]:
        """Literal line-oriented search over bounded UTF-8 text-like files."""
        needle = query.strip()
        if not needle:
            raise ArchiveError("Search query must not be empty")
        if len(needle) > 500:
            raise ArchiveError("Search query must be at most 500 characters")
        if limit <= 0 or limit > 500:
            raise ArchiveError("Search limit must be between 1 and 500")
        if max_bytes <= 0:
            raise ArchiveError("Search byte ceiling must be positive")
        if excerpt_chars < 40 or excerpt_chars > 1000:
            raise ArchiveError("Search excerpt size must be between 40 and 1000 characters")

        normalized_prefix = normalize_prefix(prefix)
        comparable_needle = needle if case_sensitive else needle.casefold()
        hits: list[dict[str, object]] = []
        match_count = 0
        candidate_files = 0
        scanned_files = 0
        skipped_oversize = 0
        skipped_non_utf8 = 0

        def scan(relative: str, data: bytes) -> None:
            nonlocal match_count, scanned_files, skipped_non_utf8
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                skipped_non_utf8 += 1
                return
            scanned_files += 1
            for line_number, line in enumerate(text.splitlines(), start=1):
                comparable_line = line if case_sensitive else line.casefold()
                if comparable_needle not in comparable_line:
                    continue
                match_count += 1
                if len(hits) >= limit:
                    continue
                excerpt = line.strip()
                if len(excerpt) > excerpt_chars:
                    excerpt = excerpt[: excerpt_chars - 1] + "…"
                hits.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "excerpt": excerpt,
                    }
                )

        if self.kind == "directory":
            for relative, path, size in self._iter_directory_files():
                if not _matches_prefix(relative, normalized_prefix):
                    continue
                if PurePosixPath(relative).suffix.lower() not in TEXT_SUFFIXES:
                    continue
                candidate_files += 1
                if size > max_bytes:
                    skipped_oversize += 1
                    continue
                scan(relative, path.read_bytes())
        else:
            members = self._zip_members()
            with zipfile.ZipFile(self.root, "r") as archive:
                for relative, info in members:
                    if not _matches_prefix(relative, normalized_prefix):
                        continue
                    if PurePosixPath(relative).suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    candidate_files += 1
                    if info.file_size > max_bytes:
                        skipped_oversize += 1
                        continue
                    scan(relative, archive.read(info))

        return {
            "query": needle,
            "prefix": normalized_prefix,
            "case_sensitive": case_sensitive,
            "hits": hits,
            "match_count": match_count,
            "candidate_files": candidate_files,
            "scanned_files": scanned_files,
            "skipped_oversize": skipped_oversize,
            "skipped_non_utf8": skipped_non_utf8,
            "truncated": match_count > len(hits),
        }

    def entry(self, relative: str) -> ArchiveEntry | None:
        """Return size/hash metadata for one path without reading unrelated files."""
        normalized = normalize_relative_path(relative)
        if self._is_excluded(normalized):
            return None

        if self.kind == "directory":
            root = self._directory_root()
            unresolved = root.joinpath(*PurePosixPath(normalized).parts)
            if unresolved.is_symlink():
                raise ArchiveError("Symlink files are not readable")
            try:
                resolved = unresolved.resolve(strict=True)
            except FileNotFoundError:
                return None
            if resolved == root or root not in resolved.parents:
                raise ArchiveError("Resolved path escaped the Archive root")
            if not resolved.is_file():
                return None
            size = resolved.stat().st_size
            with resolved.open("rb") as handle:
                digest = _sha256_stream(handle)
            return ArchiveEntry(path=normalized, size=size, sha256=digest)

        members = dict(self._zip_members())
        info = members.get(normalized)
        if info is None:
            return None
        with zipfile.ZipFile(self.root, "r") as archive:
            with archive.open(info, "r") as handle:
                digest = _sha256_stream(handle)
        return ArchiveEntry(path=normalized, size=info.file_size, sha256=digest)

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
