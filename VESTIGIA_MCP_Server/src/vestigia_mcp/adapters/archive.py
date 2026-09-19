from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Literal

from ..browse import BrowseCursorError, BrowseSession, BrowseSessionStore
from ..pagination import (
    CursorError,
    canonical_sha256,
    decode_cursor,
    encode_cursor,
    page_metadata,
)


ArchiveKind = Literal["directory", "zip"]
TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".log"}
)
IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


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


def _utf8_safe_prefix(data: bytes) -> bytes:
    """Trim only an incomplete final UTF-8 sequence from a raw page."""
    end = len(data)
    while end:
        try:
            data[:end].decode("utf-8", errors="strict")
            return data[:end]
        except UnicodeDecodeError as exc:
            if exc.reason != "unexpected end of data":
                raise ArchiveError("Archive text is not valid UTF-8") from exc
            end = exc.start
    return b""


def _image_mime_from_signature(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
class ArchiveMedia:
    path: str
    size: int
    sha256: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class SnapshotSlice:
    raw: bytes
    size: int
    sha256: str
    source_revision: str
    line_start: int | None = None
    line_end: int | None = None


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

    def _cursor_source_sha256(self) -> str:
        return canonical_sha256(
            {
                "kind": self.kind,
                "root": str(self.root.resolve(strict=False)),
                "excluded_paths": self.excluded_paths,
            }
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

    def list_paths(
        self,
        prefix: str = "",
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if limit <= 0 or limit > 1000:
            raise ArchiveError("List limit must be between 1 and 1000")
        normalized_prefix = normalize_prefix(prefix)
        paths = list(self.all_paths())
        if normalized_prefix:
            paths = [path for path in paths if _matches_prefix(path, normalized_prefix)]
        source_sha256 = self._cursor_source_sha256()
        view_sha256 = canonical_sha256(
            {"source_sha256": source_sha256, "paths": paths}
        )
        offset = 0
        if cursor is not None:
            try:
                state = decode_cursor(cursor, "archive.list")
            except CursorError as exc:
                raise ArchiveError(str(exc)) from exc
            if state.get("prefix") != normalized_prefix:
                raise ArchiveError("Cursor prefix does not match this request")
            if state.get("source_sha256") != source_sha256:
                raise ArchiveError("Cursor belongs to a different Archive source")
            if state.get("view_sha256") != view_sha256:
                raise ArchiveError("Cursor is stale because the Archive path view changed")
            offset = state.get("offset", -1)
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                raise ArchiveError("Cursor offset is invalid")
            if offset > len(paths):
                raise ArchiveError("Cursor offset exceeds the current Archive path view")
        page_paths = paths[offset : offset + limit]
        next_offset = offset + len(page_paths)
        next_cursor = None
        if next_offset < len(paths):
            next_cursor = encode_cursor(
                "archive.list",
                {
                    "prefix": normalized_prefix,
                    "offset": next_offset,
                    "source_sha256": source_sha256,
                    "view_sha256": view_sha256,
                },
            )
        return {
            "paths": page_paths,
            "total": len(paths),
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
            "page": page_metadata(
                limit=limit,
                returned=len(page_paths),
                offset=offset,
                total=len(paths),
                next_cursor=next_cursor,
                view_sha256=view_sha256,
            ),
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

    @contextmanager
    def _stream_member(self, relative: str) -> Iterator[tuple[BinaryIO, int, str]]:
        """Open one regular source member without admitting its full content."""
        if self._is_excluded(relative):
            raise ArchiveError(f"Archive file is excluded from this source: {relative}")
        if self.kind == "directory":
            path = self._resolve_directory_file(relative)
            stat = path.stat()
            revision = f"directory:{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"
            with path.open("rb") as handle:
                yield handle, stat.st_size, revision
            return

        members = dict(self._zip_members())
        info = members.get(relative)
        if info is None:
            raise ArchiveError(f"Archive file not found: {relative}")
        revision = (
            f"zip:{info.header_offset}:{info.CRC}:{info.file_size}:"
            f"{info.date_time!r}"
        )
        with zipfile.ZipFile(self.root, "r") as archive:
            with archive.open(info, "r") as handle:
                yield handle, info.file_size, revision

    def _snapshot_and_slice(
        self,
        relative: str,
        *,
        offset: int,
        page_bytes: int,
        text: bool,
    ) -> SnapshotSlice:
        if offset < 0 or page_bytes <= 0:
            raise ArchiveError("Page offset and page size must be positive")
        requested_end = offset + page_bytes
        digest = hashlib.sha256()
        page = bytearray()
        seen = 0
        lines_before = 0

        with self._stream_member(relative) as (handle, declared_size, revision):
            while chunk := handle.read(1024 * 1024):
                chunk_start = seen
                chunk_end = seen + len(chunk)
                digest.update(chunk)
                if chunk_start < offset:
                    prefix_end = min(len(chunk), offset - chunk_start)
                    lines_before += chunk[:prefix_end].count(b"\n")
                overlap_start = max(offset, chunk_start)
                overlap_end = min(requested_end, chunk_end)
                if overlap_start < overlap_end:
                    page.extend(
                        chunk[overlap_start - chunk_start : overlap_end - chunk_start]
                    )
                seen = chunk_end

        if seen != declared_size:
            raise ArchiveError("Archive file size changed while it was being read")
        if offset > seen:
            raise ArchiveError("Cursor offset exceeds the current Archive file")

        raw = bytes(page)
        if text:
            raw = _utf8_safe_prefix(raw)
            if not raw and offset < seen:
                raise ArchiveError(
                    "Text page is too small to contain the next UTF-8 character"
                )
            try:
                raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ArchiveError(f"Archive text is not valid UTF-8: {relative}") from exc
            line_start = lines_before + 1
            line_end = line_start + raw.count(b"\n")
        else:
            line_start = None
            line_end = None
        return SnapshotSlice(
            raw=raw,
            size=seen,
            sha256=digest.hexdigest(),
            source_revision=revision,
            line_start=line_start,
            line_end=line_end,
        )

    def _read_text_bytes(self, relative: str, max_bytes: int) -> tuple[str, bytes]:
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
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArchiveError(
                f"Archive text is not valid UTF-8: {normalized}"
            ) from exc
        return normalized, data

    def read_text(self, relative: str, max_bytes: int) -> str:
        _, data = self._read_text_bytes(relative, max_bytes)
        return data.decode("utf-8")

    def _read_text_page_legacy(
        self,
        relative: str,
        max_bytes: int,
        *,
        page_bytes: int = 64_000,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if page_bytes < 256 or page_bytes > 256_000:
            raise ArchiveError("Text page_bytes must be between 256 and 256000")
        normalized, data = self._read_text_bytes(relative, max_bytes)
        digest = hashlib.sha256(data).hexdigest()
        source_sha256 = self._cursor_source_sha256()
        offset = 0
        if cursor is not None:
            try:
                state = decode_cursor(cursor, "archive.read_text")
            except CursorError as exc:
                raise ArchiveError(str(exc)) from exc
            if state.get("path") != normalized:
                raise ArchiveError("Cursor path does not match this request")
            if state.get("source_sha256") != source_sha256:
                raise ArchiveError("Cursor belongs to a different Archive source")
            if state.get("sha256") != digest:
                raise ArchiveError("Cursor is stale because the Archive file changed")
            offset = state.get("offset", -1)
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                raise ArchiveError("Cursor offset is invalid")
            if offset > len(data):
                raise ArchiveError("Cursor offset exceeds the current Archive file")

        end = min(len(data), offset + page_bytes)
        while end > offset:
            try:
                content = data[offset:end].decode("utf-8", errors="strict")
                break
            except UnicodeDecodeError:
                end -= 1
        else:
            if offset == len(data):
                content = ""
            else:
                raise ArchiveError("Text page is too small to contain the next UTF-8 character")

        next_cursor = None
        if end < len(data):
            next_cursor = encode_cursor(
                "archive.read_text",
                {
                    "path": normalized,
                    "offset": end,
                    "sha256": digest,
                    "source_sha256": source_sha256,
                },
            )
        return {
            "path": normalized,
            "content": content,
            "size": len(data),
            "sha256": digest,
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
            "page": {
                **page_metadata(
                    limit=page_bytes,
                    returned=end - offset,
                    offset=offset,
                    total=len(data),
                    next_cursor=next_cursor,
                    view_sha256=digest,
                ),
                "unit": "utf8_bytes",
                "byte_start": offset,
                "byte_end": end,
            },
        }

    def read_text_page(
        self,
        relative: str,
        max_bytes: int | None = None,
        *,
        page_bytes: int = 64_000,
        cursor: str | None = None,
        browse_store: BrowseSessionStore | None = None,
        policy_scope: str | None = None,
    ) -> dict[str, object]:
        """Read either legacy bounded text pages or snapshot-bound browse pages."""
        if browse_store is None:
            if max_bytes is None:
                raise ArchiveError("Legacy text pages require a total byte ceiling")
            return self._read_text_page_legacy(
                relative, max_bytes, page_bytes=page_bytes, cursor=cursor
            )
        if not policy_scope:
            raise ArchiveError("Snapshot text pages require an authorization scope")
        normalized = normalize_relative_path(relative)
        if PurePosixPath(normalized).suffix.lower() not in TEXT_SUFFIXES:
            raise ArchiveError(
                "archive.read_text only exposes configured text-like suffixes"
            )
        return self._read_browse_page(
            kind="archive.read_text",
            relative=normalized,
            page_bytes=page_bytes,
            cursor=cursor,
            browse_store=browse_store,
            policy_scope=policy_scope,
            text=True,
        )

    def read_bytes_page(
        self,
        relative: str,
        *,
        page_bytes: int = 48_000,
        cursor: str | None = None,
        browse_store: BrowseSessionStore,
        policy_scope: str,
    ) -> dict[str, object]:
        """Read one snapshot-bound base64 page from any regular Archive file."""
        if not policy_scope:
            raise ArchiveError("Snapshot byte pages require an authorization scope")
        return self._read_browse_page(
            kind="archive.read_bytes",
            relative=normalize_relative_path(relative),
            page_bytes=page_bytes,
            cursor=cursor,
            browse_store=browse_store,
            policy_scope=policy_scope,
            text=False,
        )

    def _read_browse_page(
        self,
        *,
        kind: str,
        relative: str,
        page_bytes: int,
        cursor: str | None,
        browse_store: BrowseSessionStore,
        policy_scope: str,
        text: bool,
    ) -> dict[str, object]:
        if page_bytes <= 0:
            raise ArchiveError("Page byte budget must be positive")
        source = self._cursor_source_sha256()
        offset = 0
        session = None
        if cursor is not None:
            try:
                claims = browse_store.decode(cursor, kind)
                session = browse_store.validate_continuation(
                    claims, policy_scope=policy_scope, page_bytes=page_bytes
                )
            except BrowseCursorError as exc:
                raise ArchiveError(str(exc)) from exc
            if session.path != relative:
                raise ArchiveError("Browse cursor path does not match this request")
            if session.source != source:
                raise ArchiveError("Browse cursor belongs to a different Archive source")
            offset = claims["offset"]
            if not isinstance(offset, int) or isinstance(offset, bool):
                raise ArchiveError("Browse cursor offset is invalid")

        try:
            snapshot = self._snapshot_and_slice(
                relative, offset=offset, page_bytes=page_bytes, text=text
            )
        except ArchiveError as exc:
            if session is not None and str(exc).startswith("Archive file not found:"):
                return self._file_changed_result(
                    relative, session, offset, page_bytes, current_content_sha256=None
                )
            raise
        if session is None:
            session = browse_store.create(
                kind=kind,
                source=source,
                path=relative,
                policy_scope=policy_scope,
                page_bytes=page_bytes,
                snapshot_sha256=snapshot.sha256,
                size=snapshot.size,
                source_revision=snapshot.source_revision,
            )
        elif (
            session.snapshot_sha256 != snapshot.sha256
            or session.size != snapshot.size
            or session.source_revision != snapshot.source_revision
        ):
            return self._file_changed_result(
                relative, session, offset, page_bytes, current_content_sha256=snapshot.sha256
            )

        end = offset + len(snapshot.raw)
        next_cursor = (
            browse_store.cursor_for(session, offset=end)
            if end < snapshot.size
            else None
        )
        result: dict[str, object] = {
            "path": relative,
            "size": snapshot.size,
            "byte_start": offset,
            "byte_end": end,
            "content_sha256": snapshot.sha256,
            "snapshot_status": "same_snapshot",
            "next_cursor": next_cursor,
            "budget": {
                "requested_bytes": page_bytes,
                "returned_bytes": len(snapshot.raw),
                "truncated": end < snapshot.size,
                "remaining_bytes": snapshot.size - end,
            },
        }
        if text:
            result["content"] = snapshot.raw.decode("utf-8")
            result["line_start"] = snapshot.line_start
            result["line_end"] = snapshot.line_end
        else:
            result["data"] = base64.b64encode(snapshot.raw).decode("ascii")
        return result

    @staticmethod
    def _file_changed_result(
        relative: str,
        session: BrowseSession,
        offset: int,
        page_bytes: int,
        *,
        current_content_sha256: str | None,
    ) -> dict[str, object]:
        return {
            "path": relative,
            "content_sha256": session.snapshot_sha256,
            "current_content_sha256": current_content_sha256,
            "snapshot_status": "file_changed_during_browse",
            "next_cursor": None,
            "budget": {
                "requested_bytes": page_bytes,
                "returned_bytes": 0,
                "truncated": True,
                "remaining_bytes": max(session.size - offset, 0),
            },
        }

    def read_media(self, relative: str, max_bytes: int) -> ArchiveMedia:
        """Read one bounded, signature-checked raster image.

        Media has a separate surface from text so binary bytes cannot be smuggled through
        archive.read_text. SVG is intentionally excluded because it is executable-ish text and
        may contain external references; add new formats only with an explicit validation rule.
        """
        normalized = normalize_relative_path(relative)
        suffix = PurePosixPath(normalized).suffix.lower()
        declared_mime = IMAGE_MIME_TYPES.get(suffix)
        if declared_mime is None:
            raise ArchiveError(
                "archive.read_media only exposes PNG, JPEG, GIF, and WebP images"
            )
        if max_bytes <= 0:
            raise ArchiveError("Media byte ceiling must be positive")

        if self.kind == "directory":
            path = self._resolve_directory_file(normalized)
            size = path.stat().st_size
            if size > max_bytes:
                raise ArchiveError(
                    f"Archive media exceeds byte ceiling ({size} > {max_bytes})"
                )
            data = path.read_bytes()
        else:
            members = dict(self._zip_members())
            info = members.get(normalized)
            if info is None:
                raise ArchiveError(f"Archive file not found: {normalized}")
            if info.file_size > max_bytes:
                raise ArchiveError(
                    f"Archive media exceeds byte ceiling ({info.file_size} > {max_bytes})"
                )
            with zipfile.ZipFile(self.root, "r") as archive:
                data = archive.read(info)
            size = info.file_size

        detected_mime = _image_mime_from_signature(data)
        if detected_mime is None:
            raise ArchiveError("Archive media does not have a supported image signature")
        if detected_mime != declared_mime:
            raise ArchiveError(
                "Archive media extension does not match its image signature "
                f"({declared_mime} != {detected_mime})"
            )
        return ArchiveMedia(
            path=normalized,
            size=size,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type=declared_mime,
            data=data,
        )

    def search_text(
        self,
        query: str,
        *,
        prefix: str = "",
        limit: int = 50,
        max_bytes: int = 1_000_000,
        case_sensitive: bool = False,
        excerpt_chars: int = 240,
        cursor: str | None = None,
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
        query_sha256 = canonical_sha256(
            {
                "source_sha256": self._cursor_source_sha256(),
                "query": needle,
                "prefix": normalized_prefix,
                "case_sensitive": case_sensitive,
                "max_bytes": max_bytes,
                "excerpt_chars": excerpt_chars,
            }
        )
        offset = 0
        expected_view_sha256: str | None = None
        if cursor is not None:
            try:
                state = decode_cursor(cursor, "archive.search_text")
            except CursorError as exc:
                raise ArchiveError(str(exc)) from exc
            if state.get("query_sha256") != query_sha256:
                raise ArchiveError("Cursor search parameters do not match this request")
            offset = state.get("offset", -1)
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                raise ArchiveError("Cursor offset is invalid")
            expected_view_sha256 = state.get("view_sha256")
            if not isinstance(expected_view_sha256, str):
                raise ArchiveError("Cursor search view digest is invalid")
        hits: list[dict[str, object]] = []
        view_digest = hashlib.sha256()
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
                excerpt = line.strip()
                if len(excerpt) > excerpt_chars:
                    excerpt = excerpt[: excerpt_chars - 1] + "…"
                hit = {"path": relative, "line": line_number, "excerpt": excerpt}
                view_digest.update(
                    json.dumps(
                        hit,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                view_digest.update(b"\n")
                if match_count <= offset or len(hits) >= limit:
                    continue
                hits.append(hit)

        if self.kind == "directory":
            for relative, path, size in sorted(
                self._iter_directory_files(), key=lambda item: item[0]
            ):
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
                for relative, info in sorted(members, key=lambda item: item[0]):
                    if not _matches_prefix(relative, normalized_prefix):
                        continue
                    if PurePosixPath(relative).suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    candidate_files += 1
                    if info.file_size > max_bytes:
                        skipped_oversize += 1
                        continue
                    scan(relative, archive.read(info))

        if offset > match_count:
            raise ArchiveError("Cursor offset exceeds the current search result view")
        view_sha256 = view_digest.hexdigest()
        if expected_view_sha256 is not None and expected_view_sha256 != view_sha256:
            raise ArchiveError("Cursor is stale because the search result view changed")
        next_offset = offset + len(hits)
        next_cursor = None
        if next_offset < match_count:
            next_cursor = encode_cursor(
                "archive.search_text",
                {
                    "query_sha256": query_sha256,
                    "offset": next_offset,
                    "view_sha256": view_sha256,
                },
            )
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
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
            "page": page_metadata(
                limit=limit,
                returned=len(hits),
                offset=offset,
                total=match_count,
                next_cursor=next_cursor,
                view_sha256=view_sha256,
            ),
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
