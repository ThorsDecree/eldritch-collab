from __future__ import annotations

import hashlib
import io
import json
import os
import random
import sqlite3
import tempfile
import threading
import uuid
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from .adapters.archive import ArchiveError, ArchiveSource, normalize_prefix, normalize_relative_path


CATALOG_SCHEMA_VERSION = "vestigia.lanternslide.catalog.v0.1"
_SUPPORTED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_CONTACT_SHEET_MAX_IMAGES = 16
_CONTACT_SHEET_MAX_PIXELS = 50_000_000
_TILE_SIZE = 256


class LanternslideError(ArchiveError):
    """Raised when a bounded Lanternslide operation cannot proceed safely."""


@dataclass(frozen=True)
class CatalogEntry:
    image_id: str
    path: str
    size: int
    sha256: str
    mime_type: str
    width: int
    height: int


@dataclass(frozen=True)
class ScanResult:
    scan_id: str
    complete: bool
    candidate_total: int
    next_offset: int
    indexed_total: int
    omitted_total: int
    candidate_manifest_sha256: str
    catalog_sha256: str
    entries: tuple[dict[str, Any], ...]
    omissions: tuple[dict[str, Any], ...]

    def public(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Deal:
    seed: str
    unique_only: bool
    image_ids: tuple[str, ...]
    entries: tuple[dict[str, Any], ...]

    def public(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ContactSheet:
    image_ids: tuple[str, ...]
    width: int
    height: int
    png_bytes: bytes

    def public(self) -> dict[str, object]:
        return {
            "image_ids": list(self.image_ids),
            "width": self.width,
            "height": self.height,
            "mime_type": "image/png",
            "data": self.png_bytes,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: object) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _is_under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix.rstrip("/") + "/")


class LanternslideService:
    """Resumable, metadata-only cataloger for explicitly configured Archive images."""

    def __init__(
        self,
        source: ArchiveSource,
        state_dir: Path,
        *,
        source_prefix: str = "pics",
        source_prefixes: tuple[str, ...] | None = None,
        catalog_path: str = "pics/Lanternslide/catalog.json",
        scan_batch_max: int = 50,
        image_max_bytes: int = 25_000_000,
        contact_sheet_max_bytes: int = 4_000_000,
    ):
        self._source = source
        self._state_dir = state_dir.expanduser()
        self._state_path = self._state_dir / "lanternslide" / "catalog-state.json"
        self._source_prefix = normalize_prefix(source_prefix)
        self._source_prefixes = tuple(dict.fromkeys(normalize_prefix(p) for p in (source_prefixes if source_prefixes is not None else (self._source_prefix,))))
        if not self._source_prefixes:
            raise LanternslideError("At least one source prefix is required")
        if len(self._source_prefixes) > 32 or sum(len(p) for p in self._source_prefixes) > 1000:
            raise LanternslideError("Lanternslide source roots exceed the configured bounds")
        self._annotations_path = self._state_dir / "lanternslide" / "annotations.sqlite3"
        self._catalog_path = normalize_relative_path(catalog_path)
        if not _is_under(self._catalog_path, self._source_prefix):
            raise LanternslideError("Lanternslide catalog path must be inside source prefix")
        if PurePosixPath(self._catalog_path).suffix.lower() != ".json":
            raise LanternslideError("Lanternslide catalog path must end in .json")
        self._catalog_parent = PurePosixPath(self._catalog_path).parent.as_posix()
        self._scan_batch_max = self._positive_bound(scan_batch_max, "scan batch maximum")
        self._image_max_bytes = self._positive_bound(image_max_bytes, "image byte maximum")
        self._contact_sheet_max_bytes = self._positive_bound(
            contact_sheet_max_bytes, "contact-sheet byte maximum"
        )
        self._lock = threading.RLock()

    @staticmethod
    def _positive_bound(value: int, label: str) -> int:
        if isinstance(value, bool) or value <= 0:
            raise LanternslideError(f"Lanternslide {label} must be positive")
        return int(value)

    def _load_state(self) -> dict[str, Any] | None:
        if not self._state_path.exists():
            return None
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LanternslideError("Lanternslide state is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise LanternslideError("Lanternslide state schema is unsupported")
        return value

    def _write_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=self._state_path.parent, delete=False
        )
        try:
            with temporary:
                temporary.write(payload)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary.name, self._state_path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

    def _candidate_paths(self) -> tuple[str, ...]:
        paths = []
        if self._source.kind == "directory":
            for prefix in self._source_prefixes:
                root = self._source.root
                for part in PurePosixPath(prefix).parts:
                    try:
                        if part not in os.listdir(root):
                            raise LanternslideError(f"Lanternslide source root unavailable or case mismatch: {prefix}")
                    except OSError as exc:
                        raise LanternslideError(f"Lanternslide source root unavailable: {prefix}") from exc
                    root = root / part
                    if root.is_symlink():
                        raise LanternslideError("Lanternslide source root cannot be a symlink")
                if not root.is_dir():
                    raise LanternslideError(f"Lanternslide source root unavailable: {prefix or '.'}")
        try:
            candidates = self._source.all_paths(strict=True, prefixes=self._source_prefixes)
        except ArchiveError as exc:
            raise LanternslideError("Cannot enumerate Lanternslide source roots") from exc
        if self._source.kind == "zip":
            for prefix in self._source_prefixes:
                if prefix and not any(_is_under(path, prefix) for path in candidates):
                    raise LanternslideError(f"Lanternslide source root unavailable: {prefix}")
        for path in candidates:
            normalized = normalize_relative_path(path)
            if not any(_is_under(normalized, prefix) for prefix in self._source_prefixes):
                continue
            if _is_under(normalized, self._catalog_parent):
                continue
            if PurePosixPath(normalized).suffix.lower() not in _SUPPORTED_SUFFIXES:
                continue
            paths.append(normalized)
        return tuple(sorted(paths))

    def _candidate_manifest(self, paths: tuple[str, ...]) -> str:
        manifest = {"source_prefix": self._source_prefix, "paths": paths}
        if self._source_prefixes != (self._source_prefix,):
            manifest["source_prefixes"] = self._source_prefixes
        return _canonical_digest(manifest)

    def _catalog_digest(self, state: dict[str, Any]) -> str:
        catalog = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "source_prefix": self._source_prefix,
                "catalog_path": self._catalog_path,
                "scan_id": state["scan_id"],
                "candidate_manifest_sha256": state["candidate_manifest_sha256"],
                "entries": state.get("entries", []),
                "omissions": state.get("omissions", []),
                "complete": bool(state.get("complete")),
            }
        if self._source_prefixes != (self._source_prefix,):
            catalog["source_prefixes"] = self._source_prefixes
        return _canonical_digest(catalog)

    def _new_state(self, paths: tuple[str, ...]) -> dict[str, Any]:
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "scan_id": f"lanternslide_scan_{uuid.uuid4().hex}",
            "source_prefix": self._source_prefix,
            "source_prefixes": list(self._source_prefixes),
            "catalog_path": self._catalog_path,
            "candidate_paths": list(paths),
            "candidate_manifest_sha256": self._candidate_manifest(paths),
            "progress_offset": 0,
            "entries": [],
            "omissions": [],
            "complete": False,
            "catalog_sha256": None,
        }

    @staticmethod
    def _entry_sort_key(item: dict[str, Any]) -> str:
        return str(item.get("path", ""))

    def _reconcile_candidates(
        self,
        state: dict[str, Any],
        current_paths: tuple[str, ...],
    ) -> None:
        """Keep resumable work aligned with the current source path set."""
        previous_paths = tuple(str(path) for path in state.get("candidate_paths", []))
        current_set = set(current_paths)
        previous_set = set(previous_paths)
        offset = min(max(0, int(state.get("progress_offset", 0))), len(previous_paths))

        processed = [path for path in previous_paths[:offset] if path in current_set]
        pending = [path for path in previous_paths[offset:] if path in current_set]
        additions = [path for path in current_paths if path not in previous_set]
        state["candidate_paths"] = [*processed, *pending, *additions]
        state["progress_offset"] = len(processed)
        state["entries"] = [
            item for item in state.get("entries", [])
            if str(item.get("path", "")) in current_set
        ]
        state["omissions"] = [
            item for item in state.get("omissions", [])
            if str(item.get("path", "")) in current_set
        ]
        state["candidate_manifest_sha256"] = self._candidate_manifest(current_paths)
        state["source_prefix"] = self._source_prefix
        state["source_prefixes"] = list(self._source_prefixes)
        state["complete"] = state["progress_offset"] >= len(state["candidate_paths"])
        state["catalog_sha256"] = self._catalog_digest(state)
        self._write_state(state)

    def _public_scan(self, state: dict[str, Any]) -> ScanResult:
        entries = tuple(sorted(state.get("entries", []), key=self._entry_sort_key))
        omissions = tuple(sorted(state.get("omissions", []), key=self._entry_sort_key))
        return ScanResult(
            scan_id=str(state["scan_id"]),
            complete=bool(state.get("complete")),
            candidate_total=len(state.get("candidate_paths", [])),
            next_offset=int(state.get("progress_offset", 0)),
            indexed_total=len(entries),
            omitted_total=len(omissions),
            candidate_manifest_sha256=str(state["candidate_manifest_sha256"]),
            catalog_sha256=str(state.get("catalog_sha256") or self._catalog_digest(state)),
            entries=entries,
            omissions=omissions,
        )

    @contextmanager
    def _scan_lock(self):
        # Serialize catalog read/reconcile/write across MCP processes sharing a state dir.
        lock_path = self._state_dir / "lanternslide" / "scan-lock.sqlite3"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(lock_path, timeout=30)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield

    def scan(self, *, scan_id: str | None = None) -> dict[str, object]:
        with self._lock, self._scan_lock():
            current_paths = self._candidate_paths()
            current_manifest = self._candidate_manifest(current_paths)
            state = self._load_state()
            if state is None:
                state = self._new_state(current_paths)
            elif scan_id is None and not state.get("complete", False):
                raise LanternslideError(
                    "An incomplete Lanternslide scan is active; resume it with its scan ID"
                )
            elif scan_id is not None and str(state.get("scan_id")) != scan_id:
                raise LanternslideError("Lanternslide scan ID does not match the active scan")

            if state.get("candidate_manifest_sha256") != current_manifest:
                self._reconcile_candidates(state, current_paths)
            if state.get("complete"):
                return self._public_scan(state).public()

            paths = state["candidate_paths"]
            offset = int(state.get("progress_offset", 0))
            end = min(len(paths), offset + self._scan_batch_max)
            existing_paths = {str(item["path"]) for item in state.get("entries", [])}
            omitted_paths = {str(item["path"]) for item in state.get("omissions", [])}

            for path in paths[offset:end]:
                metadata = self._source.entry(path)
                if metadata is None:
                    state.setdefault("omissions", []).append(
                        {"path": path, "reason": "disappeared"}
                    )
                    omitted_paths.add(path)
                    continue
                if metadata.size > self._image_max_bytes:
                    state.setdefault("omissions", []).append(
                        {"path": path, "reason": "oversize", "size": metadata.size}
                    )
                    omitted_paths.add(path)
                    continue
                try:
                    media = self._source.read_media(path, self._image_max_bytes)
                    with Image.open(io.BytesIO(media.data)) as image:
                        image.verify()
                    with Image.open(io.BytesIO(media.data)) as image:
                        width, height = image.size
                except (ArchiveError, OSError, SyntaxError, UnidentifiedImageError):
                    state.setdefault("omissions", []).append(
                        {"path": path, "reason": "invalid_image"}
                    )
                    omitted_paths.add(path)
                    continue
                if path in existing_paths or path in omitted_paths:
                    raise LanternslideError("Lanternslide state contains a duplicate source path")
                state.setdefault("entries", []).append(
                    asdict(
                        CatalogEntry(
                            image_id=media.sha256,
                            path=media.path,
                            size=media.size,
                            sha256=media.sha256,
                            mime_type=media.mime_type,
                            width=width,
                            height=height,
                        )
                    )
                )
                existing_paths.add(path)

            state["progress_offset"] = end
            state["complete"] = end >= len(paths)
            state["entries"] = sorted(state.get("entries", []), key=self._entry_sort_key)
            state["omissions"] = sorted(state.get("omissions", []), key=self._entry_sort_key)
            state["catalog_sha256"] = self._catalog_digest(state)
            self._write_state(state)
            return self._public_scan(state).public()

    def status(self) -> dict[str, object]:
        with self._lock:
            state = self._load_state()
            if state is None:
                return {
                    "schema_version": CATALOG_SCHEMA_VERSION,
                    "source_prefix": self._source_prefix,
                    "source_prefixes": list(self._source_prefixes),
                    "catalog_path": self._catalog_path,
                    "scan_id": None,
                    "complete": False,
                    "candidate_total": 0,
                    "next_offset": 0,
                    "indexed_total": 0,
                    "omitted_total": 0,
                    "candidate_manifest_sha256": None,
                    "catalog_sha256": None,
                }
            result = self._public_scan(state).public()
            result.pop("entries", None)
            result.pop("omissions", None)
            result["schema_version"] = CATALOG_SCHEMA_VERSION
            result["source_prefix"] = self._source_prefix
            result["source_prefixes"] = list(self._source_prefixes)
            result["catalog_source_prefixes"] = state.get("source_prefixes", [state.get("source_prefix", "")])
            result["configuration_changed"] = set(result["catalog_source_prefixes"]) != set(self._source_prefixes)
            result["catalog_path"] = self._catalog_path
            return result

    def _complete_state(self) -> dict[str, Any]:
        state = self._load_state()
        if state is None or not state.get("complete"):
            raise LanternslideError("Lanternslide catalog is not complete")
        if set(state.get("source_prefixes", [state.get("source_prefix", "")])) != set(self._source_prefixes):
            raise LanternslideError("Lanternslide roots changed; scan before using the catalog")
        return state

    def find(self, query: str, *, unique_only: bool = False) -> dict[str, object]:
        wanted = str(query).strip().casefold()
        if not wanted:
            raise LanternslideError("Lanternslide find query must not be blank")
        state = self._complete_state()
        entries = [
            item
            for item in state.get("entries", [])
            if wanted in str(item.get("path", "")).casefold()
        ]
        if unique_only:
            entries = self._unique_entries(entries)
        return {"query": query, "unique_only": unique_only, "entries": entries}

    def _annotation_db(self) -> sqlite3.Connection:
        self._annotations_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._annotations_path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS annotations (image_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, tags TEXT NOT NULL)")
        return db

    @staticmethod
    def _validate_tags(tags: list[str]) -> list[str]:
        if not isinstance(tags, list) or len(tags) > 50:
            raise LanternslideError("Invalid annotation tags")
        unique: dict[str, str] = {}
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip() or len(tag) > 100:
                raise LanternslideError("Invalid annotation tags")
            unique.setdefault(tag.strip().casefold(), tag.strip())
        return list(unique.values())

    def annotate(self, image_id: str, *, title: str, description: str, tags: list[str], expected_revision: int) -> dict[str, Any]:
        if not isinstance(image_id, str) or len(image_id) != 64 or any(c not in "0123456789abcdef" for c in image_id):
            raise LanternslideError("Invalid image ID")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise LanternslideError("Invalid annotation revision")
        if not isinstance(title, str) or not isinstance(description, str) or len(title) > 300 or len(description) > 8000:
            raise LanternslideError("Invalid annotation text")
        normalized_tags = self._validate_tags(tags)
        if not any(entry["image_id"] == image_id for entry in self._complete_state().get("entries", [])):
            raise LanternslideError("Unknown catalog image ID")
        with closing(self._annotation_db()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT revision FROM annotations WHERE image_id = ?", (image_id,)).fetchone()
                revision = row[0] if row else 0
                if revision != expected_revision:
                    raise LanternslideError("Annotation revision conflict")
                revision += 1
                db.execute("INSERT OR REPLACE INTO annotations VALUES (?, ?, ?, ?, ?)", (image_id, revision, title, description, json.dumps(normalized_tags, ensure_ascii=False)))
        return {"image_id": image_id, "revision": revision, "title": title, "description": description, "tags": normalized_tags}

    def search(self, *, query: str = "", tags: list[str] | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > 300 or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise LanternslideError("Invalid search bounds")
        wanted_tags = {t.casefold() for t in self._validate_tags(tags if tags is not None else [])}
        state = self._complete_state()
        annotations: dict[str, dict[str, Any]] = {}
        if self._annotations_path.exists():
            with closing(self._annotation_db()) as db:
                for image_id, revision, title, description, raw_tags in db.execute("SELECT image_id, revision, title, description, tags FROM annotations"):
                    annotations[image_id] = {"revision": revision, "title": title, "description": description, "tags": json.loads(raw_tags)}
        wanted = query.strip().casefold()
        matches = []
        for entry in state.get("entries", []):
            annotation = annotations.get(entry["image_id"])
            fields = [entry["path"]]
            if annotation:
                fields.extend([annotation["title"], annotation["description"], *annotation["tags"]])
            if wanted and not any(wanted in field.casefold() for field in fields):
                continue
            if wanted_tags and not wanted_tags.issubset({t.casefold() for t in annotation["tags"]} if annotation else set()):
                continue
            matches.append({**entry, "annotation": annotation})
        page: list[dict[str, Any]] = []
        used = 1024
        for entry in matches[offset:offset + limit]:
            size = len(json.dumps(entry, ensure_ascii=True).encode("utf-8")) + 2
            if used + size > 128_000:
                if not page:
                    raise LanternslideError("One search result exceeds the response byte budget")
                break
            page.append(entry)
            used += size
        next_offset = offset + len(page)
        return {"entries": page, "total": len(matches), "next_offset": next_offset if next_offset < len(matches) else None}

    @staticmethod
    def _unique_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for entry in sorted(entries, key=lambda item: str(item.get("path", ""))):
            image_id = str(entry.get("image_id", ""))
            if image_id in seen:
                continue
            seen.add(image_id)
            result.append(entry)
        return result

    def deal(self, count: int, seed: str, *, unique_only: bool = True) -> dict[str, object]:
        if isinstance(count, bool) or count <= 0 or count > _CONTACT_SHEET_MAX_IMAGES:
            raise LanternslideError("Lanternslide deal count must be between 1 and 16")
        normalized_seed = str(seed).strip()
        if not normalized_seed:
            raise LanternslideError("Lanternslide deal seed must not be blank")
        state = self._complete_state()
        entries = list(state.get("entries", []))
        if unique_only:
            entries = self._unique_entries(entries)
        if count > len(entries):
            raise LanternslideError("Lanternslide deal count exceeds available images")
        chosen = random.Random(normalized_seed).sample(entries, count)
        deal = Deal(
            seed=normalized_seed,
            unique_only=unique_only,
            image_ids=tuple(str(item["image_id"]) for item in chosen),
            entries=tuple(chosen),
        )
        return deal.public()

    def contact_sheet(self, image_ids: list[str] | tuple[str, ...]) -> dict[str, object]:
        if not image_ids or len(image_ids) > _CONTACT_SHEET_MAX_IMAGES:
            raise LanternslideError("Lanternslide contact sheet must contain 1 to 16 images")
        normalized_ids = [str(item).strip() for item in image_ids]
        if any(not item for item in normalized_ids):
            raise LanternslideError("Lanternslide contact-sheet image IDs must not be blank")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise LanternslideError("Lanternslide contact sheet contains a duplicate image ID")
        state = self._complete_state()
        by_id: dict[str, dict[str, Any]] = {}
        for entry in state.get("entries", []):
            by_id.setdefault(str(entry["image_id"]), entry)
        missing = [image_id for image_id in normalized_ids if image_id not in by_id]
        if missing:
            raise LanternslideError("Lanternslide contact sheet contains an unknown image ID")

        images: list[Image.Image] = []
        total_pixels = 0
        try:
            for image_id in normalized_ids:
                entry = by_id[image_id]
                media = self._source.read_media(str(entry["path"]), self._image_max_bytes)
                if media.sha256 != image_id:
                    raise LanternslideError(
                        f"Lanternslide source changed since catalog scan: {entry['path']}"
                    )
                with Image.open(io.BytesIO(media.data)) as image:
                    image.load()
                    width, height = image.size
                    total_pixels += width * height
                    if total_pixels > _CONTACT_SHEET_MAX_PIXELS:
                        raise LanternslideError("Lanternslide contact sheet exceeds pixel ceiling")
                    rendered = image.convert("RGBA")
                    rendered.thumbnail((_TILE_SIZE, _TILE_SIZE), Image.Resampling.LANCZOS)
                    images.append(rendered.copy())
        except (ArchiveError, OSError, SyntaxError, UnidentifiedImageError):
            for image in images:
                image.close()
            raise LanternslideError("Lanternslide contact sheet image is invalid") from None

        columns = min(4, len(images))
        rows = (len(images) + columns - 1) // columns
        canvas = Image.new("RGBA", (columns * _TILE_SIZE, rows * _TILE_SIZE), "white")
        for index, image in enumerate(images):
            left = (index % columns) * _TILE_SIZE + (_TILE_SIZE - image.width) // 2
            top = (index // columns) * _TILE_SIZE + (_TILE_SIZE - image.height) // 2
            canvas.alpha_composite(image, (left, top))
            image.close()
        output = io.BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        canvas.close()
        encoded = output.getvalue()
        if len(encoded) > self._contact_sheet_max_bytes:
            raise LanternslideError(
                f"Lanternslide contact sheet exceeds byte ceiling ({len(encoded)} > {self._contact_sheet_max_bytes})"
            )
        return ContactSheet(
            image_ids=tuple(normalized_ids),
            width=columns * _TILE_SIZE,
            height=rows * _TILE_SIZE,
            png_bytes=encoded,
        ).public()

    def catalog_export(self, *, max_bytes: int = 1_000_000) -> str:
        if isinstance(max_bytes, bool) or max_bytes <= 0:
            raise LanternslideError("Lanternslide catalog export ceiling must be positive")
        state = self._complete_state()
        entries = sorted(state.get("entries", []), key=self._entry_sort_key)
        lines = [
            {
                "type": "catalog",
                "schema_version": CATALOG_SCHEMA_VERSION,
                "scan_id": state["scan_id"],
                "source_prefix": self._source_prefix,
                "catalog_path": self._catalog_path,
                "candidate_manifest_sha256": state["candidate_manifest_sha256"],
                "catalog_sha256": state.get("catalog_sha256") or self._catalog_digest(state),
                "complete": True,
                "candidate_total": len(state.get("candidate_paths", [])),
                "indexed_total": len(entries),
                "omitted_total": len(state.get("omissions", [])),
            }
        ]
        lines[0]["source_prefixes"] = list(self._source_prefixes)
        lines.extend({"type": "entry", **entry} for entry in entries)
        by_hash: dict[str, list[str]] = defaultdict(list)
        for entry in entries:
            by_hash[str(entry["image_id"])].append(str(entry["path"]))
        for image_id, paths in sorted(by_hash.items()):
            if len(paths) > 1:
                lines.append(
                    {
                        "type": "duplicate_group",
                        "image_id": image_id,
                        "path_count": len(paths),
                        "paths": paths,
                    }
                )
        lines.extend({"type": "omission", **item} for item in state.get("omissions", []))
        exported = "".join(
            json.dumps(line, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for line in lines
        )
        size = len(exported.encode("utf-8"))
        if size > max_bytes:
            raise LanternslideError(
                f"Lanternslide catalog export exceeds byte ceiling ({size} > {max_bytes})"
            )
        return exported
