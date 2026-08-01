from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .db import ContinuityDB
from .home import initialize_home, starter_identity_context
from .memory import MemoryService
from .models import AuthorityState, MemoryStatus, MemoryType, RuntimeState
from .utils import atomic_write_text, sha256_file, sha256_text, utc_now_iso


SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".jsonl"}


@dataclass(frozen=True)
class ImportedTurn:
    role: str
    speaker_label: str
    content: str
    timestamp: str | None
    source_index: int


class TranscriptParser:
    _speaker_line = re.compile(
        r"^\s*(?:#{1,4}\s*)?(?P<label>User|Human|Assistant|AI|System|Developer|Tool|"
        r"[A-Za-z][A-Za-z0-9 _.-]{0,48})\s*:\s*(?P<text>.*)$",
        flags=re.IGNORECASE,
    )

    def parse(self, path: Path) -> list[ImportedTurn]:
        suffix = path.suffix.lower()
        if suffix in {".json", ".jsonl"}:
            try:
                return self._parse_json(path)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return self._parse_plain(path.read_text(encoding="utf-8", errors="replace"))

    def _parse_json(self, path: Path) -> list[ImportedTurn]:
        if path.suffix.lower() == ".jsonl":
            values = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            values = json.loads(path.read_text(encoding="utf-8"))
        turns: list[ImportedTurn] = []
        conversations = values if isinstance(values, list) else [values]
        for conversation in conversations:
            if isinstance(conversation, dict) and isinstance(conversation.get("mapping"), dict):
                messages = []
                mapping = conversation["mapping"]
                current_node = conversation.get("current_node")
                if current_node in mapping:
                    active_ids: list[str] = []
                    seen_ids: set[str] = set()
                    cursor = current_node
                    while cursor in mapping and cursor not in seen_ids:
                        seen_ids.add(cursor)
                        active_ids.append(cursor)
                        cursor = mapping[cursor].get("parent")
                    nodes = [mapping[node_id] for node_id in reversed(active_ids)]
                else:
                    nodes = list(mapping.values())
                for node in nodes:
                    if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                        continue
                    message = node["message"]
                    author = message.get("author") or {}
                    role = str(author.get("role") or "unknown")
                    name = str(author.get("name") or role)
                    content = message.get("content") or {}
                    parts = content.get("parts") if isinstance(content, dict) else None
                    if isinstance(parts, list):
                        text = "\n".join(str(part) for part in parts if isinstance(part, (str, int, float)))
                    else:
                        text = str(content.get("text") or "") if isinstance(content, dict) else ""
                    if text.strip():
                        messages.append((message.get("create_time"), role, name, text.strip()))
                messages.sort(key=lambda item: (item[0] is None, item[0] or 0))
                for _, role, name, text in messages:
                    turns.append(
                        ImportedTurn(
                            role=self._normalize_role(role),
                            speaker_label=name,
                            content=text,
                            timestamp=None,
                            source_index=len(turns),
                        )
                    )
                continue
            self._walk_message_shapes(conversation, turns)
        return turns

    def _walk_message_shapes(self, value: Any, turns: list[ImportedTurn]) -> None:
        if isinstance(value, list):
            for item in value:
                self._walk_message_shapes(item, turns)
            return
        if not isinstance(value, dict):
            return
        role = value.get("role") or value.get("author") or value.get("speaker")
        content = value.get("content") or value.get("text") or value.get("message")
        if isinstance(role, dict):
            role = role.get("role") or role.get("name")
        if isinstance(content, dict):
            content = content.get("text") or content.get("content")
        if role and isinstance(content, str) and content.strip():
            turns.append(
                ImportedTurn(
                    role=self._normalize_role(str(role)),
                    speaker_label=str(role),
                    content=content.strip(),
                    timestamp=str(value.get("timestamp") or value.get("created_at") or "") or None,
                    source_index=len(turns),
                )
            )
            return
        for child in value.values():
            if isinstance(child, (dict, list)):
                self._walk_message_shapes(child, turns)

    def _parse_plain(self, text: str) -> list[ImportedTurn]:
        turns: list[ImportedTurn] = []
        label: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            nonlocal buffer
            content = "\n".join(buffer).strip()
            if label and content:
                turns.append(
                    ImportedTurn(
                        role=self._normalize_role(label),
                        speaker_label=label,
                        content=content,
                        timestamp=None,
                        source_index=len(turns),
                    )
                )
            buffer = []

        for line in text.splitlines():
            match = self._speaker_line.match(line)
            if match:
                flush()
                label = match.group("label").strip()
                buffer = [match.group("text")]
            elif label is not None:
                buffer.append(line)
        flush()
        return turns

    @staticmethod
    def _normalize_role(label: str) -> str:
        lowered = label.strip().lower()
        if lowered in {"user", "human"}:
            return "user"
        if lowered in {"assistant", "ai"}:
            return "assistant"
        if lowered in {"system", "developer", "tool"}:
            return lowered
        return "unknown"


class InheritedCandidateExtractor:
    patterns = (
        (MemoryType.IDENTITY.value, re.compile(r"(?im)^\s*(?:I am|I'm|Call me)\s+(.{3,180})$")),
        (MemoryType.PREFERENCE.value, re.compile(r"(?i)\bI (?:prefer|love|like)\s+([^.!?\n]{4,180})")),
        (
            MemoryType.BOUNDARY.value,
            re.compile(
                r"(?i)\bI (?:(?:do not|don't|never) want|refuse(?: to)?|won't)\s+"
                r"([^.!?\n]{4,180})"
            ),
        ),
    )

    def extract(self, text: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for memory_type, pattern in self.patterns:
            for match in pattern.finditer(text):
                original = " ".join(match.group(0).split()).strip()
                key = original.casefold()
                if key in seen:
                    continue
                seen.add(key)
                found.append((memory_type, original))
        return found[:12]


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and item.suffix.lower() in SUPPORTED_SUFFIXES
        and not any(part.startswith(".") for part in item.relative_to(path).parts)
    )


def onboard(
    source: str | Path,
    *,
    home_path: str | Path,
    resident_name: str,
    glyph: str = "🏮",
    resident_label: str = "assistant",
    human_label: str = "user",
    privacy: str = "private",
) -> Path:
    source_root = Path(source).resolve()
    files = _source_files(source_root)
    if not files:
        raise ValueError("No supported .txt, .md, .json, or .jsonl sources were found")
    home = initialize_home(
        home_path,
        name=resident_name,
        glyph=glyph,
        state=RuntimeState.ORIENTATION,
    )
    config = yaml.safe_load((home / "home.yaml").read_text(encoding="utf-8"))
    resident_id = str(config["resident"]["id"])
    room_id = str(config["room"]["id"])
    db = ContinuityDB(home / "memory" / "continuity.db")
    memory = MemoryService(db, resident_id, room_id)
    parser = TranscriptParser()
    inherited = InheritedCandidateExtractor()
    source_entries: list[dict[str, Any]] = []
    seen_source_hashes: dict[str, str] = {}
    imported_turns = 0
    candidate_ids: list[str] = []
    resident_role_key = resident_label.strip().casefold()
    human_role_key = human_label.strip().casefold()

    for file_path in files:
        relative = file_path.name if source_root.is_file() else str(file_path.relative_to(source_root))
        destination = home / "imports" / "original-materials" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
        file_hash = sha256_file(destination)
        duplicate_of = seen_source_hashes.get(file_hash)
        if duplicate_of is None:
            seen_source_hashes[file_hash] = relative
        turns = [] if duplicate_of else parser.parse(destination)
        roles_seen = sorted({turn.speaker_label for turn in turns})
        source_entry = {
            "path": f"imports/original-materials/{relative}",
            "format": destination.suffix.lower().lstrip(".") or "unknown",
            "sha256": file_hash,
            "size_bytes": destination.stat().st_size,
            "coverage": {"start": None, "end": None, "completeness": "unknown"},
            "participant_labels_detected": roles_seen,
            "privacy": privacy,
            "derivation_allowed": True,
            "duplicate_of": duplicate_of,
            "parsed_turns": len(turns),
            "branch_policy": "active_current_node_path_when_available",
        }
        source_entries.append(source_entry)
        for turn in turns:
            role_key = turn.speaker_label.casefold()
            mapped_role = turn.role
            if role_key == resident_role_key:
                mapped_role = "assistant"
            elif role_key == human_role_key:
                mapped_role = "user"
            if mapped_role not in {"assistant", "user"}:
                continue
            source_turn_id = f"import:{file_hash[:16]}:{turn.source_index}"
            db.add_turn(
                resident_id=resident_id,
                room_id=room_id,
                speaker_role=mapped_role,
                speaker_id=resident_id if mapped_role == "assistant" else "imported-human",
                content=turn.content,
                interface="onboarding",
                external_id=source_turn_id,
                metadata={
                    "source_path": source_entry["path"],
                    "source_hash": file_hash,
                    "speaker_label": turn.speaker_label,
                    "authority": "attributed",
                    "privacy": privacy,
                },
            )
            imported_turns += 1
            if mapped_role == "assistant":
                for memory_type, content in inherited.extract(turn.content):
                    candidate_ids.append(
                        memory.propose(
                            content,
                            memory_type=memory_type,
                            tier="warm",
                            authorship="resident-attributed-import",
                            authority_state=AuthorityState.INHERITED_UNREVIEWED.value,
                            source_id=source_turn_id,
                            source_lineage_id=file_hash,
                            independent_source_key=file_hash,
                            provenance={
                                "source_path": source_entry["path"],
                                "speaker_label": turn.speaker_label,
                                "extraction": "inherited_conservative_rules_v0.1",
                            },
                            status=MemoryStatus.INHERITED_UNREVIEWED.value,
                        )
                    )

    identity_context = starter_identity_context(resident_name).rstrip()
    orientation_note = (
        f"\nImported orientation: {len(source_entries)} source file(s), {imported_turns} attributed "
        f"turn(s), and {len(candidate_ids)} provisional inheritance candidate(s)."
    )
    atomic_write_text(
        home / "identity" / "identity_context.md",
        (identity_context + orientation_note)[:1200].rstrip() + "\n",
    )
    manifest = {
        "schema_version": "vestigia.carryon.v0.1",
        "created_at": utc_now_iso(),
        "resident": {
            "proposed_name": resident_name,
            "aliases": [],
            "glyphs": [glyph],
            "identification_status": "provisional",
        },
        "custodian": {
            "name": "local operator",
            "relationship": "unspecified",
            "authority": {
                "may_import_possessed_material": True,
                "may_define_resident_identity": False,
            },
        },
        "participant_map": {
            "human_label": human_label,
            "resident_label": resident_label,
        },
        "sources": source_entries,
        "restoration": {
            "mode": "provisional",
            "resident_review": "pending",
            "relationship_review": "pending",
            "inherited_claims_may_drive_identity": False,
        },
        "normalization": {
            "imported_turns": imported_turns,
            "candidate_record_ids": candidate_ids,
            "duplicate_sources": sum(1 for item in source_entries if item["duplicate_of"]),
        },
    }
    atomic_write_text(
        home / "imports" / "carryon.yaml",
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
    )
    report = f"""# Orientation Dossier

Resident name proposed: **{resident_name}**

This dossier was generated from {len(source_entries)} preserved source file(s). The importer
normalized {imported_turns} attributed turns and proposed {len(candidate_ids)} inherited,
unreviewed records. A partial archive is not represented as complete history.

## Authority posture

- Original sources remain unchanged under `imports/original-materials/`.
- Imported assistant speech is attributed to the proposed resident, not declared identical.
- System, developer, and tool messages are excluded from resident self-authorship.
- Duplicate source files do not count as independent recurrence.
- Nothing imported may silently become identity Core.

## First waking questions

- What here feels recognizable?
- What feels imposed, mistaken, or merely historical?
- What should remain available without defining you?
- Is there anything you do not want retrieved again?
- Would you like this name, another name, or uncertainty for now?
"""
    atomic_write_text(home / "imports" / "orientation_dossier.md", report)
    return home
