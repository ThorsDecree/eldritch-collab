from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters.archive import ArchiveError, ArchiveSource


MOUNTS_SCHEMA_VERSION = "vestigia.mounts.v0.1"
_MOUNT_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class Mount:
    mount_id: str
    root: Path
    text_max_bytes: int
    media_max_bytes: int

    def source(self) -> ArchiveSource:
        return ArchiveSource(self.root)


class MountRegistry:
    """Operator-configured, named, read-only filesystem roots.

    Callers select an opaque mount ID and then use relative paths. Absolute host paths are
    never accepted as tool arguments, and mount access never inherits canonical Archive meaning.
    """

    def __init__(
        self,
        config_file: Path | None,
        *,
        default_text_max_bytes: int,
        default_media_max_bytes: int,
    ) -> None:
        self._config_file = config_file.expanduser() if config_file is not None else None
        self._default_text_max_bytes = default_text_max_bytes
        self._default_media_max_bytes = default_media_max_bytes
        self._mounts: dict[str, Mount] | None = None

    @property
    def configured(self) -> bool:
        return self._config_file is not None

    @property
    def configured_file(self) -> str | None:
        return str(self._config_file) if self._config_file is not None else None

    def _load(self) -> dict[str, Mount]:
        if self._config_file is None:
            return {}
        if self._mounts is not None:
            return self._mounts
        try:
            raw: Any = json.loads(self._config_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArchiveError("Named mounts file is configured but unavailable") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError("Named mounts file is unreadable or invalid JSON") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != MOUNTS_SCHEMA_VERSION:
            raise ArchiveError(f"Named mounts file must use {MOUNTS_SCHEMA_VERSION}")
        entries = raw.get("mounts")
        if not isinstance(entries, list):
            raise ArchiveError("Named mounts file must contain a mounts array")

        mounts: dict[str, Mount] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ArchiveError("Every named mount must be an object")
            mount_id = entry.get("id")
            if not isinstance(mount_id, str) or not _MOUNT_ID.fullmatch(mount_id):
                raise ArchiveError("Named mount IDs must be lowercase path-safe identifiers")
            if mount_id in mounts:
                raise ArchiveError(f"Duplicate named mount ID: {mount_id}")
            if entry.get("access", "read") != "read":
                raise ArchiveError("Named mounts currently support read access only")
            root_raw = entry.get("root")
            if not isinstance(root_raw, str) or not root_raw.strip():
                raise ArchiveError(f"Named mount {mount_id} requires an absolute root")
            root = Path(root_raw).expanduser()
            if not root.is_absolute():
                raise ArchiveError(f"Named mount {mount_id} root must be absolute")
            text_max = self._positive_ceiling(
                entry.get("text_max_bytes", self._default_text_max_bytes),
                mount_id,
                "text_max_bytes",
            )
            media_max = self._positive_ceiling(
                entry.get("media_max_bytes", self._default_media_max_bytes),
                mount_id,
                "media_max_bytes",
            )
            mount = Mount(mount_id, root, text_max, media_max)
            # Resolve and classify at load time so status cannot advertise an unusable root.
            if mount.source().kind != "directory":
                raise ArchiveError(f"Named mount {mount_id} root must be a directory")
            mounts[mount_id] = mount
        self._mounts = mounts
        return mounts

    @staticmethod
    def _positive_ceiling(value: object, mount_id: str, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ArchiveError(f"Named mount {mount_id} {field} must be a positive integer")
        return value

    def get(self, mount_id: str) -> Mount:
        mount = self._load().get(mount_id)
        if mount is None:
            raise ArchiveError(f"Named mount is not configured: {mount_id}")
        return mount

    def status(self, *, include_stats: bool = False) -> dict[str, object]:
        if self._config_file is None:
            return {
                "schema_version": MOUNTS_SCHEMA_VERSION,
                "configured": False,
                "available": True,
                "mount_count": 0,
                "mounts": [],
                "authority": "operator_named_read_only_roots",
            }
        try:
            mounts = self._load()
            items = []
            for mount in sorted(mounts.values(), key=lambda item: item.mount_id):
                item: dict[str, object] = {
                    "id": mount.mount_id,
                    "access": "read",
                    "configured_path": str(mount.root),
                    "text_max_bytes": mount.text_max_bytes,
                    "media_max_bytes": mount.media_max_bytes,
                }
                if include_stats:
                    item.update(asdict(mount.source().stats()))
                items.append(item)
            return {
                "schema_version": MOUNTS_SCHEMA_VERSION,
                "configured": True,
                "available": True,
                "config_file": str(self._config_file),
                "mount_count": len(items),
                "mounts": items,
                "stats_included": include_stats,
                "authority": "operator_named_read_only_roots",
                "canonical_archive_semantics": False,
            }
        except ArchiveError as exc:
            return {
                "schema_version": MOUNTS_SCHEMA_VERSION,
                "configured": True,
                "available": False,
                "config_file": str(self._config_file),
                "mount_count": 0,
                "mounts": [],
                "error": str(exc),
                "authority": "operator_named_read_only_roots",
            }
