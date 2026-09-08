from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .adapters.archive import ArchiveError, TEXT_SUFFIXES, normalize_relative_path


STAGE_SCHEMA_VERSION = "vestigia.archive-stage.v0.1"
_STAGE_ID = re.compile(r"archive_stage_[0-9a-f]{32}\Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256(payload)


def _matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


class ArchiveMutationStore:
    """Durable two-phase text proposals for one unpacked live Archive.

    Staging writes only MCP-owned state. Promotion revalidates the captured base hash and
    deployment prefix grant immediately before an atomic replacement in the live Archive.
    """

    def __init__(
        self,
        live_root: Path | None,
        state_dir: Path,
        deployment_id: str,
        *,
        write_prefixes: tuple[str, ...] = (),
        max_bytes: int = 1_000_000,
    ):
        self._live_root = live_root.expanduser() if live_root is not None else None
        self._stages_dir = state_dir.expanduser() / "archive-stages"
        self._deployment_id = deployment_id
        self._write_prefixes = tuple(sorted(set(write_prefixes)))
        self._max_bytes = max_bytes
        self._lock = threading.RLock()

    @property
    def write_prefixes(self) -> tuple[str, ...]:
        return self._write_prefixes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def capabilities(self) -> dict[str, object]:
        available = False
        error: str | None = None
        try:
            self._root()
            available = True
        except ArchiveError as exc:
            error = str(exc)
        return {
            "schema_version": "vestigia.archive-write-capabilities.v0.1",
            "authority": "mcp_deployment_prefix_grant_plus_live_base_hash",
            "write_boundary_available": available,
            "promotion_configured": bool(self._write_prefixes),
            "write_prefixes": list(self._write_prefixes),
            "text_suffixes": sorted(TEXT_SUFFIXES),
            "max_bytes": self._max_bytes,
            "operations": ["create", "replace"],
            "requires_staging": True,
            "requires_proposal_digest": True,
            "optimistic_base_hash": True,
            "direct_write_available": False,
            "error": error,
        }

    def stage_text(
        self,
        path: str,
        content: str,
        *,
        expected_base_sha256: str | None = None,
        reason: str = "",
    ) -> dict[str, object]:
        encoded = content.encode("utf-8")
        if not encoded:
            raise ArchiveError("Staged Archive text must not be empty")
        if "\x00" in content:
            raise ArchiveError("Staged Archive text must not contain NUL characters")
        if len(encoded) > self._max_bytes:
            raise ArchiveError(
                f"Staged Archive text exceeds byte ceiling ({len(encoded)} > {self._max_bytes})"
            )
        if len(reason) > 1000:
            raise ArchiveError("Stage reason must be at most 1000 characters")

        with self._lock:
            normalized, target = self._target(path)
            current_sha = self._current_sha(target)
            expected = self._normalize_expected_hash(expected_base_sha256)
            if expected is not None:
                expected_sha = None if expected == "absent" else expected
                if current_sha != expected_sha:
                    raise ArchiveError(
                        "Archive base hash does not match expected_base_sha256"
                    )

            now = datetime.now(UTC).isoformat()
            stage_id = f"archive_stage_{uuid.uuid4().hex}"
            immutable = {
                "schema_version": STAGE_SCHEMA_VERSION,
                "stage_id": stage_id,
                "created_at": now,
                "deployment_id": self._deployment_id,
                "path": normalized,
                "operation": "create" if current_sha is None else "replace",
                "base_sha256": current_sha,
                "content_sha256": _sha256(encoded),
                "content_size": len(encoded),
                "reason": reason.strip(),
            }
            proposal_sha = _canonical_digest(immutable)
            record = {
                **immutable,
                "proposal_sha256": proposal_sha,
                "content": content,
                "status": "staged",
                "updated_at": now,
                "promoted_at": None,
                "discarded_at": None,
            }
            self._write_record(record)
            return {
                **self._public_record(record, include_content=False),
                "canonical_changed": False,
                "next_step": (
                    "Inspect the stage, then call archive.promote with this stage_id and "
                    "proposal_sha256 while the captured base remains unchanged."
                ),
            }

    def list_stages(self, *, status: str = "staged", limit: int = 50) -> dict[str, object]:
        if status not in {"staged", "promoted", "discarded", "all"}:
            raise ArchiveError("Stage status must be staged, promoted, discarded, or all")
        if limit <= 0 or limit > 200:
            raise ArchiveError("Stage list limit must be between 1 and 200")
        with self._lock:
            if not self._stages_dir.exists():
                records: list[dict[str, Any]] = []
            else:
                records = [
                    self._load_record(path.stem)
                    for path in self._stages_dir.glob("archive_stage_*.json")
                    if _STAGE_ID.fullmatch(path.stem)
                ]
            if status != "all":
                records = [item for item in records if item.get("status") == status]
            records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return {
                "status": status,
                "stages": [
                    self._public_record(item, include_content=False)
                    for item in records[:limit]
                ],
                "total": len(records),
                "truncated": len(records) > limit,
                "canonical_changed": False,
            }

    def inspect_stage(
        self,
        stage_id: str,
        *,
        include_content: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            record = self._load_record(stage_id)
            return {
                **self._public_record(record, include_content=include_content),
                "validation": self._validation(record),
                "canonical_changed": False,
            }

    def discard_stage(self, stage_id: str, *, reason: str = "") -> dict[str, object]:
        if len(reason) > 1000:
            raise ArchiveError("Discard reason must be at most 1000 characters")
        with self._lock:
            record = self._load_record(stage_id)
            if record.get("status") != "staged":
                raise ArchiveError("Only a staged Archive proposal can be discarded")
            now = datetime.now(UTC).isoformat()
            record["status"] = "discarded"
            record["discarded_at"] = now
            record["updated_at"] = now
            record["discard_reason"] = reason.strip()
            self._write_record(record)
            return {
                **self._public_record(record, include_content=False),
                "canonical_changed": False,
            }

    def promote(self, stage_id: str, proposal_sha256: str) -> dict[str, object]:
        with self._lock:
            record = self._load_record(stage_id)
            if proposal_sha256 != record["proposal_sha256"]:
                raise ArchiveError("Proposal digest does not match the staged Archive proposal")

            if record.get("status") == "promoted":
                validation = self._validation_against_content(record)
                if not validation["matches_content"]:
                    raise ArchiveError(
                        "Promoted Archive stage no longer matches the live target"
                    )
                return {
                    **self._public_record(record, include_content=False),
                    "canonical_changed": False,
                    "result_sha256": record["content_sha256"],
                    "atomic_replace": True,
                    "already_promoted": True,
                }
            if record.get("status") != "staged":
                raise ArchiveError("Only a staged Archive proposal can be promoted")

            validation = self._validation(record)
            if not validation["ready"]:
                if validation.get("current_sha256") == record["content_sha256"]:
                    self._mark_promoted(record, reconciled=True)
                    return {
                        **self._public_record(record, include_content=False),
                        "canonical_changed": False,
                        "result_sha256": record["content_sha256"],
                        "atomic_replace": True,
                        "already_promoted": True,
                        "promotion_reconciled": True,
                    }
                raise ArchiveError(str(validation["detail"]))
            _, target = self._target(str(record["path"]))
            data = str(record["content"]).encode("utf-8")
            self._atomic_write(target, data)
            self._mark_promoted(record, reconciled=False)
            return {
                **self._public_record(record, include_content=False),
                "canonical_changed": True,
                "result_sha256": record["promoted_sha256"],
                "atomic_replace": True,
            }

    def _mark_promoted(self, record: dict[str, Any], *, reconciled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        record["status"] = "promoted"
        record["promoted_at"] = now
        record["updated_at"] = now
        record["promoted_sha256"] = record["content_sha256"]
        record["promotion_reconciled"] = reconciled
        self._write_record(record)

    def _root(self) -> Path:
        if self._live_root is None:
            raise ArchiveError("Live Archive root is not configured")
        try:
            root = self._live_root.resolve(strict=True)
        except OSError as exc:
            raise ArchiveError("Live Archive root is unavailable") from exc
        if not root.is_dir():
            raise ArchiveError("Canonical promotion requires an unpacked live Archive directory")
        state = self._stages_dir.parent.resolve(strict=False)
        if state == root or root in state.parents:
            raise ArchiveError("MCP state directory must not be inside the live Archive")
        return root

    def _target(self, path: str) -> tuple[str, Path]:
        normalized = normalize_relative_path(path)
        for part in PurePosixPath(normalized).parts:
            if (
                ":" in part
                or part.endswith((" ", "."))
                or PureWindowsPath(part).is_reserved()
            ):
                raise ArchiveError("Archive path contains a Windows-unsafe component")
        if PurePosixPath(normalized).suffix.lower() not in TEXT_SUFFIXES:
            raise ArchiveError("Canonical Archive promotion supports text-like files only")
        if not self._write_prefixes:
            raise ArchiveError("Canonical Archive promotion has no configured write prefixes")
        if not any(_matches_prefix(normalized, prefix) for prefix in self._write_prefixes):
            raise ArchiveError("Archive path is outside configured canonical write prefixes")

        root = self._root()
        target = root.joinpath(*PurePosixPath(normalized).parts)
        cursor = root
        for part in PurePosixPath(normalized).parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ArchiveError("Symlink directories are not writable Archive parents")
        try:
            parent = target.parent.resolve(strict=True)
        except OSError as exc:
            raise ArchiveError("Archive target parent directory must already exist") from exc
        if parent != root and root not in parent.parents:
            raise ArchiveError("Resolved Archive target escaped the live root")
        if not parent.is_dir():
            raise ArchiveError("Archive target parent is not a directory")
        if target.is_symlink():
            raise ArchiveError("Symlink Archive targets are not writable")
        if target.exists() and not target.is_file():
            raise ArchiveError("Archive target is not a regular file")
        return normalized, target

    @staticmethod
    def _normalize_expected_hash(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized == "absent":
            return normalized
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ArchiveError("expected_base_sha256 must be a SHA-256 hex digest or 'absent'")
        return normalized

    def _current_sha(self, target: Path) -> str | None:
        if not target.exists():
            return None
        size = target.stat().st_size
        if size > self._max_bytes:
            raise ArchiveError(
                f"Existing Archive text exceeds write byte ceiling ({size} > {self._max_bytes})"
            )
        try:
            data = target.read_bytes()
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArchiveError("Existing Archive target is not valid UTF-8 text") from exc
        return _sha256(data)

    def _record_path(self, stage_id: str) -> Path:
        if not _STAGE_ID.fullmatch(stage_id):
            raise ArchiveError("Invalid Archive stage ID")
        return self._stages_dir / f"{stage_id}.json"

    def _write_record(self, record: dict[str, Any]) -> None:
        path = self._record_path(str(record["stage_id"]))
        self._stages_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        self._atomic_write(path, payload.encode("utf-8"))

    def _load_record(self, stage_id: str) -> dict[str, Any]:
        path = self._record_path(stage_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArchiveError(f"Archive stage not found: {stage_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ArchiveError(f"Archive stage is unreadable: {stage_id}") from exc
        if not isinstance(record, dict) or record.get("schema_version") != STAGE_SCHEMA_VERSION:
            raise ArchiveError("Archive stage has an unsupported schema")
        if record.get("deployment_id") != self._deployment_id:
            raise ArchiveError("Archive stage belongs to a different MCP deployment")
        immutable = {
            key: record.get(key)
            for key in (
                "schema_version",
                "stage_id",
                "created_at",
                "deployment_id",
                "path",
                "operation",
                "base_sha256",
                "content_sha256",
                "content_size",
                "reason",
            )
        }
        content = record.get("content")
        if not isinstance(content, str):
            raise ArchiveError("Archive stage content is invalid")
        encoded = content.encode("utf-8")
        if record.get("content_size") != len(encoded) or record.get(
            "content_sha256"
        ) != _sha256(encoded):
            raise ArchiveError("Archive stage content failed integrity verification")
        if record.get("proposal_sha256") != _canonical_digest(immutable):
            raise ArchiveError("Archive stage proposal failed integrity verification")
        return record

    def _validation(self, record: dict[str, Any]) -> dict[str, object]:
        try:
            _, target = self._target(str(record["path"]))
            current_sha = self._current_sha(target)
        except ArchiveError as exc:
            return {"ready": False, "detail": str(exc), "current_sha256": None}
        expected = record.get("base_sha256")
        if current_sha != expected:
            return {
                "ready": False,
                "detail": "Live Archive target changed after staging; promotion refused",
                "expected_base_sha256": expected,
                "current_sha256": current_sha,
            }
        return {
            "ready": True,
            "detail": "Captured base still matches and the deployment prefix grant is active",
            "expected_base_sha256": expected,
            "current_sha256": current_sha,
        }

    def _validation_against_content(self, record: dict[str, Any]) -> dict[str, object]:
        try:
            _, target = self._target(str(record["path"]))
            current_sha = self._current_sha(target)
        except ArchiveError as exc:
            return {
                "matches_content": False,
                "detail": str(exc),
                "current_sha256": None,
            }
        return {
            "matches_content": current_sha == record["content_sha256"],
            "current_sha256": current_sha,
        }

    @staticmethod
    def _public_record(record: dict[str, Any], *, include_content: bool) -> dict[str, object]:
        hidden = {"content"}
        result = {key: value for key, value in record.items() if key not in hidden}
        if include_content:
            result["content"] = record["content"]
        return result

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        temporary: str | None = None
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass
