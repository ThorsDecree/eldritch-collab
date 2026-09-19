from __future__ import annotations

import re
from dataclasses import dataclass

from .db import ContinuityDB
from .models import NormalizedMessage, RetrievedMemory


RETRIEVAL_POLICIES = frozenset(
    {"auto", "none", "prompt_only", "response_related", "resident_selected"}
)
_GENERIC_PROMPT_PATTERNS = (
    "notice what wants attention",
    "notice what wandered in",
    "look around",
)


@dataclass(frozen=True)
class RetrievalRequest:
    policy_requested: str
    policy_effective: str
    semantic_seed: str | None
    selected_sources: tuple[str, ...]
    control_plane_excluded: bool
    semantic_source: str
    deferred: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def query_terms(self) -> tuple[str, ...]:
        if not self.semantic_seed:
            return ()
        return tuple(dict.fromkeys(re.findall(r"[\w#-]{2,}", self.semantic_seed.casefold())))


def resolve_retrieval_request(message: NormalizedMessage) -> RetrievalRequest:
    """Derive dynamic-retrieval input without treating bell display text as a query."""
    if message.interface != "bell":
        return RetrievalRequest(
            policy_requested="ordinary_message",
            policy_effective="prompt_only",
            semantic_seed=message.content,
            selected_sources=(),
            control_plane_excluded=False,
            semantic_source="message.content",
        )

    raw = message.metadata.get("bell_retrieval")
    payload = raw if isinstance(raw, dict) else {}
    prompt = str(payload.get("resident_prompt", "")).strip()
    requested = str(payload.get("requested_policy", "auto")).strip().lower()
    selected = payload.get("selected_sources", ())
    selected_sources = tuple(
        dict.fromkeys(
            str(item).strip().lower()
            for item in selected
            if str(item).strip()
        )
    ) if isinstance(selected, (list, tuple)) else ()
    if requested not in RETRIEVAL_POLICIES:
        return RetrievalRequest(
            policy_requested=requested or "unknown",
            policy_effective="none",
            semantic_seed=None,
            selected_sources=selected_sources,
            control_plane_excluded=True,
            semantic_source="bell.resident_prompt",
            warnings=("unknown_bell_retrieval_policy",),
        )

    control_plane = payload.get("control_plane")
    purpose = ""
    if isinstance(control_plane, dict):
        purpose = str(control_plane.get("purpose", "")).strip().lower()
    generic = purpose == "look_around" or any(
        marker in prompt.casefold() for marker in _GENERIC_PROMPT_PATTERNS
    )
    effective = requested
    deferred = requested == "response_related"
    if requested == "auto":
        effective = "field_scan_v1" if generic else "prompt_only"
    if requested in {"none", "response_related"}:
        prompt = ""
    return RetrievalRequest(
        policy_requested=requested,
        policy_effective=effective,
        semantic_seed=prompt or None,
        selected_sources=selected_sources,
        control_plane_excluded=True,
        semantic_source="bell.resident_prompt",
        deferred=deferred,
    )


def field_scan_v1(
    db: ContinuityDB,
    *,
    resident_id: str,
    room_id: str,
    limit: int,
    include_inherited: bool = False,
) -> tuple[RetrievedMemory, ...]:
    """Return a bounded, deterministic, non-semantic memory field scan."""
    statuses = ["accepted"]
    if include_inherited:
        statuses.append("inherited_unreviewed")
    records = db.list_memories(
        resident_id=resident_id,
        room_id=room_id,
        statuses=statuses,
        tiers=["hot", "warm"],
        limit=max(200, int(limit) * 10),
    )

    def rank(record) -> tuple[int, str, str]:
        type_priority = {
            "tension": 0,
            "commitment": 1,
            "boundary": 2,
            "relationship": 3,
        }.get(record.memory_type, 4)
        return type_priority, record.created_at, record.id

    selected: list[RetrievedMemory] = []
    seen_types: set[str] = set()
    for record in sorted(records, key=rank):
        if record.privacy == "sealed":
            continue
        reason = "recent"
        if record.memory_type == "tension":
            reason = "unresolved_tension"
        elif record.memory_type in {"relationship", "commitment", "boundary"}:
            reason = "relationship_or_commitment"
        if record.memory_type in seen_types and len(selected) < min(len(records), limit):
            continue
        seen_types.add(record.memory_type)
        selected.append(
            RetrievedMemory(
                record=record,
                score=float(max(0, int(limit) - len(selected))),
                reasons=("field_scan_v1", reason),
            )
        )
        if len(selected) >= max(1, int(limit)):
            break
    return tuple(selected)
