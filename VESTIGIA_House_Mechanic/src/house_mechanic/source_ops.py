from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
from typing import Any
import uuid

from .tasking import TaskSupervisor


PATCH_PROPOSAL_SCHEMA = "vestigia.house-mechanic-patch-proposal.v0.1"
_PATCH_ID = re.compile(r"^hm_patch_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceOpError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PatchProposal:
    proposal_id: str
    proposal_digest: str
    task_id: str
    holder_id: str
    authority_generation: int
    iteration_id: str
    mutations: list[dict[str, Any]]
    unified_diff: str
    created_at: str
    state: str = "ready"
    consumed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": PATCH_PROPOSAL_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatchProposal":
        if data.get("schema_version") != PATCH_PROPOSAL_SCHEMA:
            raise ValueError("unsupported patch proposal schema")
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        payload = {name: data[name] for name in fields if name in data}
        return cls(**payload)


class PatchProposalStore:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, proposal_id: str) -> Path:
        if not _PATCH_ID.fullmatch(proposal_id):
            raise SourceOpError("invalid_proposal_id", "proposal_id is not an issued patch proposal id")
        return self.directory / f"{proposal_id}.json"

    def _write(self, proposal: PatchProposal) -> None:
        path = self._path(proposal.proposal_id)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        raw = json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def create(self, proposal: PatchProposal) -> PatchProposal:
        with self._lock:
            if self._path(proposal.proposal_id).exists():
                raise SourceOpError("proposal_exists", "patch proposal already exists")
            self._write(proposal)
            return proposal

    def get(self, proposal_id: str) -> PatchProposal:
        with self._lock:
            path = self._path(proposal_id)
            if not path.is_file():
                raise SourceOpError("proposal_not_found", "patch proposal was not found")
            return PatchProposal.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, proposal: PatchProposal) -> PatchProposal:
        with self._lock:
            if not self._path(proposal.proposal_id).is_file():
                raise SourceOpError("proposal_not_found", "patch proposal was not found")
            self._write(proposal)
            return proposal


class TaskSourceWorkspace:
    def __init__(
        self,
        *,
        tasks: TaskSupervisor,
        proposal_store: PatchProposalStore,
        max_file_bytes: int = 1_048_576,
        max_patch_bytes: int = 4_194_304,
        max_diff_bytes: int = 5 * 1024 * 1024,
        max_files: int = 32,
    ):
        self.tasks = tasks
        self.proposal_store = proposal_store
        self.max_file_bytes = int(max_file_bytes)
        self.max_patch_bytes = int(max_patch_bytes)
        self.max_diff_bytes = int(max_diff_bytes)
        self.max_files = int(max_files)
        self._lock = threading.RLock()

    @staticmethod
    def _relative_parts(raw: str) -> tuple[str, ...]:
        if not isinstance(raw, str):
            raise SourceOpError("unsafe_path", "source path must be text")
        value = raw.replace("\\", "/")
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

        reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
        reserved.update(f"com{index}" for index in range(1, 10))
        reserved.update(f"lpt{index}" for index in range(1, 10))
        for part in parts:
            if (
                part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.split(".", 1)[0].casefold() in reserved
            ):
                raise SourceOpError(
                    "unsafe_path",
                    "source path contains a Windows-special or reserved component",
                )
        return tuple(parts)

    @staticmethod
    def _component_is_link(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())

    def _resolve_candidate(self, worktree: Path, raw: str) -> tuple[str, Path]:
        parts = self._relative_parts(raw)
        root = worktree.resolve()
        current = root
        for part in parts:
            current = current / part
            if current.exists() and self._component_is_link(current):
                raise SourceOpError("symlink_refused", "source paths may not traverse symlinks or junctions")
        try:
            current.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise SourceOpError("unsafe_path", "source path escaped the task worktree") from exc
        return "/".join(parts), current

    def _resolve_existing_text(self, worktree: Path, raw: str) -> tuple[str, Path]:
        relative, path = self._resolve_candidate(worktree, raw)
        if not path.is_file():
            raise SourceOpError("file_missing", "source file was not found")
        return relative, path

    def _read_text_bytes(self, path: Path) -> tuple[bytes, str]:
        size = path.stat().st_size
        if size > self.max_file_bytes:
            raise SourceOpError("file_too_large", "source file exceeds the configured byte ceiling")
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceOpError("non_utf8", "source file is not UTF-8 text") from exc
        return data, text

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
            data, text = self._read_text_bytes(path)
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

    def diff(
        self,
        *,
        task_id: str,
        holder_id: str,
        authority_generation: int,
        iteration_id: str,
        mutations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(mutations, list) or not mutations:
            raise SourceOpError("invalid_mutations", "mutations must be a non-empty list")
        if len(mutations) > self.max_files:
            raise SourceOpError("too_many_files", "patch proposal exceeds the configured file-count ceiling")
        record, worktree = self.tasks.authorized_worktree(
            task_id=task_id,
            holder_id=holder_id,
            authority_generation=authority_generation,
            iteration_id=iteration_id,
            require_open_iteration=True,
        )

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_payload = 0
        diff_chunks: list[str] = []

        for raw in mutations:
            if not isinstance(raw, dict):
                raise SourceOpError("invalid_mutation", "each mutation must be an object")
            op = raw.get("op")
            if op == "modify":
                required = {"op", "path", "expected_sha256", "old", "new"}
            elif op == "create":
                required = {"op", "path", "expected_state", "content"}
            else:
                raise SourceOpError("invalid_mutation", "unsupported patch mutation operation")
            if set(raw) != required:
                raise SourceOpError("invalid_mutation", "patch mutation fields do not match the operation schema")

            relative, path = self._resolve_candidate(worktree, str(raw.get("path") or ""))
            folded = relative.casefold()
            if folded in seen:
                raise SourceOpError("duplicate_path", "patch proposal contains duplicate or conflicting paths")
            seen.add(folded)

            if op == "modify":
                expected = str(raw["expected_sha256"]).lower()
                if not _SHA256.fullmatch(expected):
                    raise SourceOpError("invalid_hash", "expected_sha256 must be a lowercase SHA-256 digest")
                if not path.is_file():
                    raise SourceOpError("file_missing", "modified source file was not found")
                data, current = self._read_text_bytes(path)
                actual = hashlib.sha256(data).hexdigest()
                if actual != expected:
                    raise SourceOpError("expected_hash_mismatch", "source file hash no longer matches the proposal precondition")
                old = raw["old"]
                new = raw["new"]
                if not isinstance(old, str) or not isinstance(new, str) or not old:
                    raise SourceOpError("invalid_mutation", "modify old/new values must be text and old must be non-empty")
                occurrences = current.count(old)
                if occurrences == 0:
                    raise SourceOpError("old_text_mismatch", "exact old text was not found in the source file")
                if occurrences != 1:
                    raise SourceOpError("old_text_ambiguous", "exact old text is not unique in the source file")
                resulting = current.replace(old, new, 1)
                result_bytes = resulting.encode("utf-8")
                if len(result_bytes) > self.max_file_bytes:
                    raise SourceOpError("file_too_large", "resulting source file exceeds the configured byte ceiling")
                total_payload += len(old.encode("utf-8")) + len(new.encode("utf-8"))
                normalized.append(
                    {
                        "op": "modify",
                        "path": relative,
                        "expected_sha256": expected,
                        "old": old,
                        "new": new,
                        "pre_state": {"sha256": actual, "size_bytes": len(data)},
                    }
                )
                diff_chunks.extend(
                    difflib.unified_diff(
                        current.splitlines(keepends=True),
                        resulting.splitlines(keepends=True),
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )
            else:
                if raw["expected_state"] != "absent":
                    raise SourceOpError("invalid_mutation", "create requires expected_state='absent'")
                if path.exists():
                    raise SourceOpError("expected_absent_exists", "create target already exists")
                content = raw["content"]
                if not isinstance(content, str):
                    raise SourceOpError("invalid_mutation", "create content must be UTF-8 text")
                result_bytes = content.encode("utf-8")
                if len(result_bytes) > self.max_file_bytes:
                    raise SourceOpError("file_too_large", "resulting source file exceeds the configured byte ceiling")
                total_payload += len(result_bytes)
                normalized.append(
                    {
                        "op": "create",
                        "path": relative,
                        "expected_state": "absent",
                        "content": content,
                        "pre_state": {"state": "absent"},
                    }
                )
                diff_chunks.extend(
                    difflib.unified_diff(
                        [],
                        content.splitlines(keepends=True),
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )

            if total_payload > self.max_patch_bytes:
                raise SourceOpError("patch_too_large", "patch proposal exceeds the configured byte ceiling")

        proposal_id = f"hm_patch_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()
        unified = "".join(diff_chunks)
        if len(unified.encode("utf-8")) > self.max_diff_bytes:
            raise SourceOpError(
                "diff_too_large",
                "rendered patch preview exceeds the configured byte ceiling",
            )
        digest_basis = {
            "schema_version": PATCH_PROPOSAL_SCHEMA,
            "proposal_id": proposal_id,
            "task_id": record.task_id,
            "holder_id": record.holder_id,
            "authority_generation": record.authority_generation,
            "iteration_id": iteration_id,
            "mutations": normalized,
            "unified_diff": unified,
            "created_at": created_at,
        }
        canonical = json.dumps(digest_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        proposal = PatchProposal(
            proposal_id=proposal_id,
            proposal_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            task_id=record.task_id,
            holder_id=record.holder_id,
            authority_generation=record.authority_generation,
            iteration_id=iteration_id,
            mutations=normalized,
            unified_diff=unified,
            created_at=created_at,
        )
        self.proposal_store.create(proposal)
        return {
            "proposal_id": proposal.proposal_id,
            "proposal_digest": proposal.proposal_digest,
            "state": proposal.state,
            "unified_diff": proposal.unified_diff,
        }

    @staticmethod
    def _proposal_content_digest(proposal: PatchProposal) -> str:
        basis = {
            "schema_version": PATCH_PROPOSAL_SCHEMA,
            "proposal_id": proposal.proposal_id,
            "task_id": proposal.task_id,
            "holder_id": proposal.holder_id,
            "authority_generation": proposal.authority_generation,
            "iteration_id": proposal.iteration_id,
            "mutations": proposal.mutations,
            "unified_diff": proposal.unified_diff,
            "created_at": proposal.created_at,
        }
        canonical = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _write_temp(parent: Path, name: str, data: bytes) -> Path:
        temp = parent / f".{name}.{uuid.uuid4().hex}.hm-tmp"
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return temp

    @staticmethod
    def _replace_file(source: Path, target: Path) -> None:
        os.replace(source, target)

    def _restore_path(self, target: Path, pre_bytes: bytes | None) -> None:
        if pre_bytes is None:
            if target.exists():
                if target.is_dir():
                    raise OSError("atomic rollback target unexpectedly became a directory")
                target.unlink()
            return
        restore = self._write_temp(target.parent, target.name, pre_bytes)
        try:
            self._replace_file(restore, target)
        finally:
            if restore.exists():
                restore.unlink()

    def patch(
        self,
        *,
        task_id: str,
        holder_id: str,
        authority_generation: int,
        iteration_id: str,
        proposal_id: str,
        proposal_digest: str,
    ) -> dict[str, Any]:
        with self._lock:
            record, worktree = self.tasks.authorized_worktree(
                task_id=task_id,
                holder_id=holder_id,
                authority_generation=authority_generation,
                iteration_id=iteration_id,
                require_open_iteration=True,
            )
            proposal = self.proposal_store.get(proposal_id)
            if proposal.state == "consumed":
                raise SourceOpError("proposal_consumed", "patch proposal has already been applied")
            if proposal.state != "ready":
                raise SourceOpError("proposal_stale", "patch proposal is not ready for application")
            if proposal.proposal_digest != proposal_digest:
                raise SourceOpError("proposal_digest_mismatch", "proposal digest does not match")
            if self._proposal_content_digest(proposal) != proposal.proposal_digest:
                raise SourceOpError("proposal_digest_mismatch", "stored proposal contents do not match its digest")
            if (
                proposal.task_id != record.task_id
                or proposal.holder_id != record.holder_id
                or proposal.authority_generation != record.authority_generation
                or proposal.iteration_id != iteration_id
            ):
                raise SourceOpError("proposal_stale", "proposal authority binding is no longer current")

            planned: list[dict[str, Any]] = []
            total_payload = 0
            for mutation in proposal.mutations:
                op = mutation["op"]
                relative, target = self._resolve_candidate(worktree, mutation["path"])
                if relative != mutation["path"]:
                    raise SourceOpError("proposal_stale", "proposal path no longer normalizes identically")
                if op == "modify":
                    if not target.is_file():
                        raise SourceOpError("proposal_stale", "modified source file is no longer present")
                    data, current = self._read_text_bytes(target)
                    actual = hashlib.sha256(data).hexdigest()
                    if actual != mutation["pre_state"]["sha256"] or actual != mutation["expected_sha256"]:
                        raise SourceOpError("proposal_stale", "modified source file pre-state changed after preview")
                    old = mutation["old"]
                    if current.count(old) != 1:
                        raise SourceOpError("proposal_stale", "exact old text changed after preview")
                    resulting = current.replace(old, mutation["new"], 1).encode("utf-8")
                    if len(resulting) > self.max_file_bytes:
                        raise SourceOpError("file_too_large", "resulting source file exceeds the configured byte ceiling")
                    total_payload += len(old.encode("utf-8")) + len(mutation["new"].encode("utf-8"))
                    planned.append(
                        {
                            "op": "modify",
                            "path": relative,
                            "target": target,
                            "pre_bytes": data,
                            "post_bytes": resulting,
                            "pre_sha256": actual,
                        }
                    )
                elif op == "create":
                    if target.exists():
                        raise SourceOpError("proposal_stale", "create target no longer has absent pre-state")
                    content = mutation["content"].encode("utf-8")
                    if len(content) > self.max_file_bytes:
                        raise SourceOpError("file_too_large", "resulting source file exceeds the configured byte ceiling")
                    total_payload += len(content)
                    planned.append(
                        {
                            "op": "create",
                            "path": relative,
                            "target": target,
                            "pre_bytes": None,
                            "post_bytes": content,
                            "pre_sha256": None,
                        }
                    )
                else:
                    raise SourceOpError("proposal_stale", "proposal contains an unsupported mutation")
                if total_payload > self.max_patch_bytes:
                    raise SourceOpError("patch_too_large", "patch proposal exceeds the configured byte ceiling")

            staged: list[tuple[dict[str, Any], Path]] = []
            created_dirs: set[Path] = set()
            touched: list[dict[str, Any]] = []
            try:
                for item in planned:
                    target: Path = item["target"]
                    missing: list[Path] = []
                    cursor = target.parent
                    root = worktree.resolve()
                    while cursor != root and not cursor.exists():
                        missing.append(cursor)
                        cursor = cursor.parent
                    if cursor != root:
                        try:
                            cursor.resolve(strict=False).relative_to(root)
                        except ValueError as exc:
                            raise SourceOpError("unsafe_path", "source parent escaped the task worktree") from exc
                    for directory in reversed(missing):
                        directory.mkdir()
                        created_dirs.add(directory)
                    relative, checked = self._resolve_candidate(worktree, item["path"])
                    if checked != target or relative != item["path"]:
                        raise SourceOpError("proposal_stale", "source path changed during patch staging")
                    if item["op"] == "create" and target.exists():
                        raise SourceOpError("proposal_stale", "create target appeared during patch staging")
                    if item["op"] == "modify":
                        current = target.read_bytes()
                        if hashlib.sha256(current).hexdigest() != item["pre_sha256"]:
                            raise SourceOpError("proposal_stale", "modified source file changed during patch staging")
                    temp = self._write_temp(target.parent, target.name, item["post_bytes"])
                    staged.append((item, temp))

                for item, temp in staged:
                    target = item["target"]
                    self._replace_file(temp, target)
                    touched.append(item)

                proposal.state = "consumed"
                proposal.consumed_at = datetime.now(UTC).isoformat()
                self.proposal_store.save(proposal)
            except SourceOpError:
                rollback_errors: list[str] = []
                for item in reversed(touched):
                    try:
                        self._restore_path(item["target"], item["pre_bytes"])
                    except Exception as exc:  # pragma: no cover - catastrophic disk failure
                        rollback_errors.append(type(exc).__name__)
                for _, temp in staged:
                    try:
                        if temp.exists():
                            temp.unlink()
                    except OSError:
                        pass
                for directory in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                if touched and rollback_errors:
                    raise SourceOpError("atomic_rollback_failed", "patch failed and rollback could not fully restore pre-state")
                raise
            except Exception as exc:
                rollback_errors: list[str] = []
                for item in reversed(touched):
                    try:
                        self._restore_path(item["target"], item["pre_bytes"])
                    except Exception as rollback_exc:  # pragma: no cover - catastrophic disk failure
                        rollback_errors.append(type(rollback_exc).__name__)
                for _, temp in staged:
                    try:
                        if temp.exists():
                            temp.unlink()
                    except OSError:
                        pass
                for directory in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                if rollback_errors:
                    raise SourceOpError("atomic_rollback_failed", "patch failed and rollback could not fully restore pre-state") from exc
                raise SourceOpError("atomic_apply_failed", "patch application failed; all touched paths were restored") from exc

            results: list[dict[str, Any]] = []
            for item in planned:
                post_sha = hashlib.sha256(item["post_bytes"]).hexdigest()
                if item["op"] == "modify":
                    results.append(
                        {
                            "op": "modify",
                            "path": item["path"],
                            "pre_sha256": item["pre_sha256"],
                            "post_sha256": post_sha,
                        }
                    )
                else:
                    results.append(
                        {
                            "op": "create",
                            "path": item["path"],
                            "pre_state": "absent",
                            "post_sha256": post_sha,
                        }
                    )
            return {
                "task_id": record.task_id,
                "authority_generation": record.authority_generation,
                "iteration_id": iteration_id,
                "proposal_id": proposal.proposal_id,
                "proposal_digest": proposal.proposal_digest,
                "state": proposal.state,
                "mutations": results,
            }

