from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .tasking import TaskSupervisor


class SourceOpError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PatchProposalStore:
    """Durable proposal storage; proposal behavior is added in the next slice."""

    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)


class TaskSourceWorkspace:
    def __init__(
        self,
        *,
        tasks: TaskSupervisor,
        proposal_store: PatchProposalStore,
        max_file_bytes: int = 1_048_576,
        max_patch_bytes: int = 4_194_304,
        max_files: int = 32,
    ):
        self.tasks = tasks
        self.proposal_store = proposal_store
        self.max_file_bytes = int(max_file_bytes)
        self.max_patch_bytes = int(max_patch_bytes)
        self.max_files = int(max_files)

    @staticmethod
    def _relative_parts(raw: str) -> tuple[str, ...]:
        if not isinstance(raw, str):
            raise SourceOpError("unsafe_path", "source path must be text")
        value = raw.strip().replace("\\", "/")
        if (
            not value
            or "\x00" in value
            or value.startswith("/")
            or value.startswith("//")
            or re.match(r"^[A-Za-z]:", value)
        ):
            raise SourceOpError("unsafe_path", "source path must be a normalized relative path")
        parts = PurePosixPath(value).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise SourceOpError("unsafe_path", "source path must stay beneath the task worktree")
        return tuple(parts)

    @staticmethod
    def _component_is_link(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())

    def _resolve_existing_text(self, worktree: Path, raw: str) -> tuple[str, Path]:
        parts = self._relative_parts(raw)
        root = worktree.resolve()
        current = root
        for part in parts:
            current = current / part
            if self._component_is_link(current):
                raise SourceOpError("symlink_refused", "source paths may not traverse symlinks or junctions")
        try:
            current.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise SourceOpError("unsafe_path", "source path escaped the task worktree") from exc
        if not current.is_file():
            raise SourceOpError("file_missing", "source file was not found")
        return "/".join(parts), current

    def read(
        self,
        *,
        task_id: str,
        holder_id: str,
        authority_generation: int,
        paths: list[str],
    ) -> dict[str, Any]:
        if not isinstance(paths, list) or not paths:
            raise SourceOpError("invalid_paths", "paths must be a non-empty list")
        if len(paths) > self.max_files:
            raise SourceOpError("too_many_files", "source read exceeds the configured file-count ceiling")
        record, worktree = self.tasks.authorized_worktree(
            task_id=task_id,
            holder_id=holder_id,
            authority_generation=authority_generation,
        )
        items: list[dict[str, Any]] = []
        for raw in paths:
            relative, path = self._resolve_existing_text(worktree, raw)
            size = path.stat().st_size
            if size > self.max_file_bytes:
                raise SourceOpError("file_too_large", "source file exceeds the configured byte ceiling")
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceOpError("non_utf8", "source file is not UTF-8 text") from exc
            items.append(
                {
                    "path": relative,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "encoding": "utf-8",
                    "text": text,
                    "truncated": False,
                }
            )
        return {
            "task_id": record.task_id,
            "authority_generation": record.authority_generation,
            "items": items,
        }
