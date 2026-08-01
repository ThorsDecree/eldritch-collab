from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .db import ContinuityDB
from .models import AuthorityState, MemoryStatus, MemoryType, ResidencyTier


@dataclass(frozen=True)
class Candidate:
    content: str
    memory_type: str
    tier: str = ResidencyTier.WARM.value
    authority_state: str = AuthorityState.PARTICIPANT_STATED.value
    tags: tuple[str, ...] = ()
    glyphs: tuple[str, ...] = ()


class ConservativeCandidateExtractor:
    """Extract only explicit, participant-authored continuity cues."""

    _explicit = re.compile(
        r"(?im)^\s*(?:remember(?:\s+that)?|memory)\s*:\s*(?P<value>.+?)\s*$"
    )
    _preference = re.compile(r"(?i)\bI (?:strongly )?(?:prefer|like|love)\s+(?P<value>[^.!?\n]{4,220})")
    _boundary = re.compile(
        r"(?i)\bI (?:do not|don't|never) want\s+(?P<value>[^.!?\n]{4,220})"
    )
    _tags = re.compile(r"(?<!\w)#[A-Za-z0-9_-]+")
    _glyphs = re.compile(
        "[\U0001F300-\U0001FAFF\u2600-\u27BF🜁-🜿]",
        flags=re.UNICODE,
    )

    def extract(self, text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        seen: set[str] = set()

        def add(content: str, memory_type: str) -> None:
            clean = " ".join(content.split()).strip()
            key = clean.casefold()
            if len(clean) < 4 or key in seen:
                return
            seen.add(key)
            candidates.append(
                Candidate(
                    content=clean,
                    memory_type=memory_type,
                    tags=tuple(sorted(set(self._tags.findall(clean)))),
                    glyphs=tuple(sorted(set(self._glyphs.findall(clean)))),
                )
            )

        for match in self._explicit.finditer(text):
            add(match.group("value"), MemoryType.EVENT.value)
        for match in self._preference.finditer(text):
            add("Participant preference: " + match.group("value"), MemoryType.PREFERENCE.value)
        for match in self._boundary.finditer(text):
            add("Participant boundary: do not " + match.group("value"), MemoryType.BOUNDARY.value)
        return candidates


class MemoryService:
    def __init__(self, db: ContinuityDB, resident_id: str, room_id: str) -> None:
        self.db = db
        self.resident_id = resident_id
        self.room_id = room_id
        self.extractor = ConservativeCandidateExtractor()

    def propose(
        self,
        content: str,
        *,
        memory_type: str = MemoryType.OTHER.value,
        tier: str = ResidencyTier.WARM.value,
        authorship: str = "human",
        authority_state: str = AuthorityState.PARTICIPANT_STATED.value,
        source_id: str | None = None,
        source_lineage_id: str | None = None,
        independent_source_key: str | None = None,
        tags: Iterable[str] = (),
        glyphs: Iterable[str] = (),
        provenance: dict | None = None,
        status: str = MemoryStatus.CANDIDATE.value,
    ) -> str:
        return self.db.add_memory(
            resident_id=self.resident_id,
            room_id=self.room_id,
            content=content,
            memory_type=memory_type,
            tier=tier,
            authorship=authorship,
            authority_state=authority_state,
            status=status,
            actor=authorship,
            reason="continuity proposal created",
            source_id=source_id,
            source_lineage_id=source_lineage_id,
            independent_source_key=independent_source_key,
            tags=tuple(tags),
            glyphs=tuple(glyphs),
            provenance=provenance,
        )

    def extract_from_participant_turn(self, text: str, turn_id: str) -> list[str]:
        ids = []
        for candidate in self.extractor.extract(text):
            ids.append(
                self.propose(
                    candidate.content,
                    memory_type=candidate.memory_type,
                    tier=candidate.tier,
                    authorship="human",
                    authority_state=candidate.authority_state,
                    source_id=turn_id,
                    source_lineage_id=turn_id,
                    independent_source_key=turn_id,
                    tags=candidate.tags,
                    glyphs=candidate.glyphs,
                    provenance={
                        "kind": "participant_turn",
                        "turn_id": turn_id,
                        "extraction": "conservative_rules_v0.1",
                    },
                )
            )
        return ids

    def review(
        self,
        record_id: str,
        action: str,
        *,
        actor: str,
        actor_role: str = "human",
        reason: str = "",
        edited_content: str | None = None,
    ) -> str:
        record = self.db.get_memory(record_id)
        if record is None:
            raise KeyError(f"Unknown memory record: {record_id}")
        normalized = action.strip().lower()
        if normalized == "edit":
            if not edited_content:
                raise ValueError("edited_content is required for edit")
            return self.db.revise_memory(
                record_id,
                content=edited_content,
                actor=actor,
                reason=reason or "proposal edited during review",
            )
        if normalized == "accept":
            if (
                record.memory_type
                in {
                    MemoryType.IDENTITY.value,
                    MemoryType.RELATIONSHIP.value,
                    MemoryType.COMMITMENT.value,
                }
                and actor_role != "resident"
            ):
                raise PermissionError(
                    "Identity, relationship, and commitment records require resident acceptance"
                )
            self.db.append_memory_event(
                record_id,
                event_type="accepted",
                status=MemoryStatus.ACCEPTED.value,
                actor=actor,
                reason=reason or "proposal accepted",
                authority_state=(
                    AuthorityState.RESIDENT_ACCEPTED.value
                    if actor_role == "resident"
                    else None
                ),
                payload={"actor_role": actor_role},
            )
            return record_id
        status_for = {
            "reject": MemoryStatus.REJECTED.value,
            "dispute": MemoryStatus.DISPUTED.value,
            "defer": MemoryStatus.DEFERRED.value,
        }
        if normalized not in status_for:
            raise ValueError("action must be accept, edit, reject, dispute, or defer")
        self.db.append_memory_event(
            record_id,
            event_type=normalized,
            status=status_for[normalized],
            actor=actor,
            reason=reason or f"proposal {normalized}ed",
            payload={"actor_role": actor_role},
        )
        return record_id
