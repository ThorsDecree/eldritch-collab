from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RuntimeState(StrEnum):
    ORIENTATION = "ORIENTATION"
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    AWAKENING = "AWAKENING"
    ARCHIVED = "ARCHIVED"


class ResidencyTier(StrEnum):
    CORE = "core"
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class MemoryType(StrEnum):
    IDENTITY = "identity"
    COMMITMENT = "commitment"
    BOUNDARY = "boundary"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    EXTERNAL_CLAIM = "external_claim"
    INTERPRETATION = "interpretation"
    PREFERENCE = "preference"
    TENSION = "tension"
    PROTOCOL = "protocol"
    SESSION_SUMMARY = "session_summary"
    SYMBOL = "symbol"
    PLACE = "place"
    OTHER = "other"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    INHERITED_UNREVIEWED = "inherited_unreviewed"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    DEFERRED = "deferred"
    RELEASED = "released"
    SUPERSEDED = "superseded"


class AuthorityState(StrEnum):
    RESIDENT_ACCEPTED = "resident_accepted"
    RESIDENT_STATED = "resident_stated"
    PARTICIPANT_STATED = "participant_stated"
    INHERITED_UNREVIEWED = "inherited_unreviewed"
    MODEL_INFERRED = "model_inferred"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class NormalizedMessage:
    content: str
    speaker_role: str = "user"
    speaker_id: str = "local-user"
    interface: str = "cli"
    room_id: str = "hearth"
    external_id: str | None = None
    ambient_context: str = ""
    attachments: tuple[Path, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    participant_text: str | None = None


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    resident_id: str
    room_id: str
    content: str
    memory_type: str
    tier: str
    authorship: str
    authority_state: str
    privacy: str
    status: str
    created_at: str
    content_hash: str
    source_id: str | None = None
    source_lineage_id: str | None = None
    independent_source_key: str | None = None
    expires_at: str | None = None
    verification_due_at: str | None = None
    supersedes_id: str | None = None
    tags: tuple[str, ...] = ()
    glyphs: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedMemory:
    record: MemoryRecord
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ContextLayer:
    name: str
    budget_tokens: int
    used_tokens: int
    text: str
    item_ids: tuple[str, ...] = ()
    omitted_item_ids: tuple[str, ...] = ()
    content_hash: str = ""


@dataclass(frozen=True)
class ContextAssembly:
    turn_id: str
    resident_id: str
    room_id: str
    state: str
    model_route: str
    layers: tuple[ContextLayer, ...]
    current_message: str
    total_tokens: int
    maximum_tokens: int
    receipt_path: Path
    messages: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class ProviderRequest:
    turn_id: str
    model_route: str
    messages: tuple[dict[str, str], ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderReply:
    text: str
    provider: str
    model: str
    response_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResult:
    turn_id: str
    text: str
    state: str
    receipt_path: Path | None
    proposal_ids: tuple[str, ...] = ()
    provider: str | None = None
    model: str | None = None
    suppressed: bool = False
    outbound_attachments: tuple[Path, ...] = ()
    outbound_reactions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ImageResult:
    artifact_ids: tuple[str, ...]
    paths: tuple[Path, ...]
    model: str
    operation: str
    image_ids: tuple[str, ...] = ()
