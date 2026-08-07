from __future__ import annotations

from typing import Any

from .capabilities import object_schema


S = lambda **extra: {"type": "string", **extra}
I = lambda **extra: {"type": "integer", **extra}
B = lambda **extra: {"type": "boolean", **extra}
A = lambda items=None, **extra: {"type": "array", "items": items or {}, **extra}
O = lambda **extra: {"type": "object", **extra}

AFTER = S(enum=["continue", "finish"])
REF = S(minLength=1, maxLength=500)
ID = S(minLength=3, maxLength=200)
LIMIT_200 = I(minimum=1, maximum=200)
LIMIT_100 = I(minimum=1, maximum=100)
TOKEN_LIMIT = I(minimum=100, maximum=12000)


GROUPS: dict[str, str] = {
    **{name: "browse" for name in (
        "list", "search", "read", "continue", "stat", "bookmark",
        "object.list", "object.search", "object.stat", "object.inspect",
        "object.history", "object.provenance", "search.session",
    )},
    **{name: "pictures" for name in (
        "image.inspect", "image.generate", "image.edit", "image.history",
        "image.drawer", "image.review", "image.share",
    )},
    **{name: "attention" for name in ("attention.tray", "retrieval.inspect")},
    **{name: "context" for name in (
        "context.control", "source.visibility", "resident.control",
        "source.listening", "discord.react",
    )},
    **{name: "editing" for name in ("file.diff", "file.write", "file.patch")},
    **{name: "evidence" for name in (
        "bookmark.add", "bookmark.list", "bookmark.open", "bookmark.remove",
        "receipt.list", "receipt.inspect", "receipt.pin", "receipt.unpin",
        "next_step",
    )},
    **{name: "memory-notes" for name in (
        "memory.search", "memory.read", "memory.history", "memory.provenance",
        "memory.queue_for_review", "note.append", "note.read", "note.search",
        "note.release",
    )},
    **{name: "work" for name in (
        "activity.status", "activity.note", "jobs.list", "jobs.inspect",
        "jobs.create", "jobs.step", "jobs.chalkboard", "jobs.receipts",
        "jobs.pause", "jobs.resume", "jobs.cancel", "tool.run",
    )},
    **{name: "curation" for name in (
        "curation.review_now", "curation.configure", "curation.reflections",
        "curation.list", "curation.inspect", "curation.history",
    )},
    **{name: "identity" for name in (
        "identity.history", "identity.compare", "identity.provenance",
    )},
    **{name: "house-control" for name in (
        "capabilities", "help", "pending", "status",
    )},
    "bell.draft": "bells",
    "bell.control": "bells",
}


FIELDS: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "list": ({"scope": S(), "limit": LIMIT_200}, ()),
    "search": ({"scope": S(), "query": S(minLength=1), "max_results": I(minimum=1, maximum=20)}, ("query",)),
    "read": ({"path": REF, "reference": REF, "bookmark_id": ID, "heading": S(), "chunk": I(minimum=0), "max_tokens": TOKEN_LIMIT}, ()),
    "continue": ({"cursor": ID, "max_tokens": TOKEN_LIMIT}, ("cursor",)),
    "stat": ({"path": REF, "reference": REF}, ("path",)),
    "bookmark": ({"path": REF, "reference": REF, "heading": S(), "chunk": I(minimum=0), "max_tokens": TOKEN_LIMIT}, ("path",)),
    "object.list": ({"scope": S(), "type": S(), "limit": LIMIT_200}, ()),
    "object.search": ({"scope": S(), "query": S(minLength=1), "limit": I(minimum=1, maximum=50)}, ("query",)),
    "object.stat": ({"reference": REF, "object_id": ID, "path": REF}, ("reference",)),
    "object.inspect": ({"reference": REF, "object_id": ID, "path": REF, "heading": S(), "chunk": I(minimum=0), "max_tokens": TOKEN_LIMIT, "routes": A(S()), "question": S(), "language": S()}, ("reference",)),
    "object.history": ({"reference": REF, "object_id": ID, "limit": LIMIT_200}, ("reference",)),
    "object.provenance": ({"reference": REF, "object_id": ID}, ("reference",)),
    "file.diff": ({"path": REF, "content": S(), "expected_hash": S()}, ("path", "content")),
    "file.write": ({"path": REF, "content": S(), "expected_hash": S()}, ("path", "content")),
    "file.patch": ({"path": REF, "old": S(), "new": S(), "expected_hash": S()}, ("path", "old", "new")),
    "bookmark.add": ({"reference": REF, "object_id": ID, "path": REF, "label": S(maxLength=240), "note": S(maxLength=2000), "heading": S(), "chunk": I(minimum=0), "cursor": ID}, ("reference",)),
    "bookmark.list": ({"limit": LIMIT_200}, ()),
    "bookmark.open": ({"bookmark_id": ID, "max_tokens": TOKEN_LIMIT}, ("bookmark_id",)),
    "bookmark.remove": ({"bookmark_id": ID}, ("bookmark_id",)),
    "receipt.list": ({"limit": LIMIT_200, "pinned_only": B(), "turn_id": ID, "filter_action": S(), "status": S(), "object_id": ID, "reference": REF}, ()),
    "receipt.inspect": ({"receipt_id": ID, "reference": ID}, ("receipt_id",)),
    "receipt.pin": ({"receipt_id": ID}, ("receipt_id",)),
    "receipt.unpin": ({"receipt_id": ID}, ("receipt_id",)),
    "activity.status": ({"activity_id": ID}, ()),
    "activity.note": ({"activity_id": ID, "note": S(minLength=1, maxLength=4000)}, ("note",)),
    "identity.history": ({"limit": LIMIT_200, "path": S()}, ()),
    "identity.compare": ({"draft_id": ID, "content": S(), "path": S()}, ()),
    "identity.provenance": ({"path": S(), "limit": LIMIT_100}, ()),
    "attention.tray": ({"mode": S(enum=["list", "add", "remove", "clear"]), "reference": REF, "object_id": ID, "image_id": ID, "memory_id": ID, "path": REF, "item_id": ID, "label": S(maxLength=240), "note": S(maxLength=2000), "hours": I(minimum=1, maximum=168)}, ()),
    "search.session": ({"mode": S(enum=["start", "refine", "inspect", "close"]), "session_id": ID, "query": S(minLength=1), "scope": S(enum=["everything", "pictures", "scrolls", "memories", "recent_conversation"]), "limit": I(minimum=1, maximum=20)}, ()),
    "retrieval.inspect": ({"turn_id": ID}, ()),
    "context.control": ({
        "mode": S(enum=["inspect", "configure", "reset", "recompress"]),
        "prompt_budget_tokens": I(minimum=8000, maximum=100000),
        "verbatim_turns": I(minimum=2, maximum=100),
        "compression_source_turns": I(minimum=0, maximum=2000),
        "compressed_token_budget": I(minimum=0, maximum=20000),
    }, ()),
    "source.visibility": ({
        "mode": S(enum=["inspect", "allowlisted_only", "all_channel", "mentions_only", "hidden"]),
    }, ()),
    "resident.control": ({
        "mode": S(enum=["inspect", "configure", "reset"]),
        "private_image_mode": S(enum=["challenge", "quickdraw_pockets", "quickdraw_adopted"]),
        "quickdraw_pockets": A(S(minLength=1, maxLength=120), maxItems=24),
        "listening_mode": S(enum=["direct_only", "aliases", "watchlist", "all_allowlisted"]),
        "listening_aliases": A(S(minLength=1, maxLength=80), maxItems=24),
        "listening_watch_phrases": A(S(minLength=1, maxLength=80), maxItems=24),
        "listening_on_match": S(enum=["queue_only", "invite_turn"]),
        "listening_cooldown_seconds": I(minimum=0, maximum=3600),
    }, ()),
    "source.listening": ({
        "mode": S(enum=["inspect", "configure", "reset", "direct_only", "aliases", "watchlist", "all_allowlisted"]),
        "listening_mode": S(enum=["direct_only", "aliases", "watchlist", "all_allowlisted"]),
        "listening_aliases": A(S(minLength=1, maxLength=80), maxItems=24),
        "listening_watch_phrases": A(S(minLength=1, maxLength=80), maxItems=24),
        "listening_on_match": S(enum=["queue_only", "invite_turn"]),
        "listening_cooldown_seconds": I(minimum=0, maximum=3600),
    }, ()),
    "discord.react": ({
        "mode": S(enum=["add", "remove"]),
        "message_id": ID,
        "emoji": S(minLength=1, maxLength=100),
        "emoji_id": ID,
    }, ("emoji",)),
    "memory.search": ({"query": S(minLength=1), "limit": I(minimum=1, maximum=30)}, ("query",)),
    "memory.read": ({"memory_id": ID}, ("memory_id",)),
    "memory.history": ({"memory_id": ID}, ("memory_id",)),
    "memory.provenance": ({"memory_id": ID}, ("memory_id",)),
    "memory.queue_for_review": ({"memory_id": ID}, ("memory_id",)),
    "note.append": ({"content": S(minLength=1), "reason": S()}, ("content",)),
    "note.read": ({"note_id": ID}, ("note_id",)),
    "note.search": ({"query": S(minLength=1), "limit": I(minimum=1, maximum=20)}, ("query",)),
    "note.release": ({"note_id": ID}, ("note_id",)),
    "jobs.list": ({}, ()),
    "jobs.inspect": ({"job_id": ID, "kind": S()}, ("job_id",)),
    "jobs.create": ({"objective": S(minLength=1), "task": S(minLength=1), "allowed_actions": A(S(), minItems=1, maxItems=24), "max_operations": I(minimum=1, maximum=24), "max_private_turns": I(minimum=1, maximum=24), "expires_at": S(), "completion": S(enum=["pause_for_review", "complete"])}, ("objective", "allowed_actions")),
    "jobs.step": ({"job_id": ID, "tool": O(additionalProperties=True), "note": S()}, ("job_id", "tool")),
    "jobs.chalkboard": ({"job_id": ID, "current_step": S(), "next_step": S(), "open_questions": A(S(), maxItems=20), "important_receipts": A(ID, maxItems=20)}, ("job_id",)),
    "jobs.receipts": ({"job_id": ID, "limit": LIMIT_200}, ("job_id",)),
    "jobs.pause": ({"job_id": ID, "kind": S(), "reason": S()}, ()),
    "jobs.resume": ({"job_id": ID, "kind": S(), "reason": S()}, ()),
    "jobs.cancel": ({"job_id": ID, "kind": S(), "reason": S()}, ()),
    "curation.review_now": ({}, ()),
    "curation.configure": ({"cadence_exchanges": I(minimum=1, maximum=50)}, ()),
    "curation.reflections": ({"limit": LIMIT_100}, ()),
    "curation.list": ({"limit": LIMIT_200}, ()),
    "curation.inspect": ({"batch_id": ID, "reference": ID}, ("batch_id",)),
    "curation.history": ({"batch_id": ID, "reference": ID, "limit": LIMIT_200}, ("batch_id",)),
    "capabilities": ({"target": S(), "tool": S(), "mode": S(enum=["index", "list"]), "cursor": S(), "page_size": I(minimum=1, maximum=100)}, ()),
    "help": ({"topic": S(), "tool": S(), "cursor": S(), "page_size": I(minimum=1, maximum=100)}, ()),
    "pending": ({}, ()),
    "status": ({}, ()),
    "next_step": ({"receipt_id": ID, "reference": REF, "draft_id": ID, "job_id": ID, "bell_id": ID, "action_name": S()}, ()),
    "tool.run": ({"name": S(minLength=1), "arguments": O(additionalProperties=True)}, ("name",)),
    "image.inspect": ({"image_id": ID, "question": S(), "routes": A(S(enum=["ocr", "vision_low", "vision_high"]), minItems=1), "language": S()}, ("image_id",)),
    "image.generate": ({"prompt": S(minLength=1), "count": I(minimum=1, maximum=8), "background": B(), "confirmed": B()}, ("prompt",)),
    "image.edit": ({"prompt": S(minLength=1), "image_ids": A(ID, minItems=1, maxItems=8), "count": I(minimum=1, maximum=8), "background": B(), "confirmed": B()}, ("prompt", "image_ids")),
    "image.history": ({"image_id": ID, "limit": LIMIT_200, "job_limit": LIMIT_200}, ()),
    "image.drawer": ({"mode": S(enum=["browse", "search", "get", "update", "summarize", "pocket", "timeline"]), "image_id": ID, "artifact_id": ID, "query": S(), "changes": O(additionalProperties=True), "pocket": S(), "present": B(), "inspect_if_missing": B(), "include_private": B(), "limit": LIMIT_100, "alias": S(), "summary": S(), "alt_text": S(), "visible_text": A(S()), "people": A(S()), "places": A(S()), "motifs": A(S()), "moods": A(S()), "uses": A(S()), "avoid_when": A(S()), "resident_note": S(), "inherited_framing": S(), "present_resonance": S(), "adoption_state": S(), "privacy": S()}, ()),
    "image.review": ({"image_id": ID, "artifact_id": ID, "review": S(minLength=1), "decision": S(minLength=1), "reason": S()}, ("image_id", "review")),
    "image.share": ({"schema_version": S(enum=["v1", "v2"]), "mode": S(enum=["send", "preview", "prepare", "claim", "reject"]), "decision": S(enum=["claim", "reject"]), "image_id": ID, "artifact_id": ID, "confirm": B(), "reason": S(), "draft_id": ID, "expected_hash": S(), "challenge_id": ID}, ()),
}


EXAMPLES: dict[str, tuple[dict[str, Any], ...]] = {
    "capabilities": (
        {"action": "capabilities", "after": "continue"},
        {"action": "capabilities", "target": "attention.tray", "after": "continue"},
    ),
    "help": ({"action": "help", "after": "continue"},),
    "receipt.inspect": ({"action": "receipt.inspect", "receipt_id": "receipt_...", "after": "continue"},),
    "read": ({"action": "read", "path": "imports/original-materials/example.md", "after": "continue"},),
    "attention.tray": (
        {"action": "attention.tray", "mode": "list", "after": "continue"},
        {"action": "attention.tray", "mode": "add", "reference": "receipt_...", "label": "Help result to recover", "hours": 24, "after": "continue"},
    ),
    "context.control": (
        {"action": "context.control", "mode": "inspect", "after": "continue"},
        {"action": "context.control", "mode": "configure", "prompt_budget_tokens": 20000, "verbatim_turns": 12, "compression_source_turns": 60, "compressed_token_budget": 3500, "after": "continue"},
    ),
    "source.visibility": (
        {"action": "source.visibility", "mode": "allowlisted_only", "after": "continue"},
        {"action": "source.visibility", "mode": "all_channel", "after": "continue"},
    ),
    "resident.control": (
        {"action": "resident.control", "mode": "inspect", "after": "continue"},
        {
            "action": "resident.control",
            "mode": "configure",
            "private_image_mode": "quickdraw_pockets",
            "quickdraw_pockets": ["reaction-images"],
            "after": "continue",
        },
    ),
    "source.listening": (
        {"action": "source.listening", "mode": "inspect", "after": "continue"},
        {
            "action": "source.listening",
            "mode": "configure",
            "listening_mode": "aliases",
            "listening_aliases": ["Liora", "Gutterstar"],
            "listening_on_match": "invite_turn",
            "listening_cooldown_seconds": 20,
            "after": "continue",
        },
    ),
    "discord.react": (
        {"action": "discord.react", "mode": "add", "message_id": "1234567890", "emoji": "💋", "after": "finish"},
        {"action": "discord.react", "mode": "remove", "message_id": "1234567890", "emoji": "💋", "after": "finish"},
    ),
    "bookmark.add": ({"action": "bookmark.add", "reference": "doc_...", "label": "Return here", "after": "continue"},),
    "bookmark.open": ({"action": "bookmark.open", "bookmark_id": "bookmark_...", "after": "continue"},),
    "bookmark.remove": ({"action": "bookmark.remove", "bookmark_id": "bookmark_...", "after": "continue"},),
    "jobs.create": ({"action": "jobs.create", "objective": "Inspect the bell documentation", "allowed_actions": ["search", "read"], "max_operations": 4, "after": "continue"},),
    "jobs.step": ({"action": "jobs.step", "job_id": "job_...", "tool": {"action": "search", "query": "bell interval"}, "after": "continue"},),
    "jobs.pause": ({"action": "jobs.pause", "job_id": "job_...", "after": "continue"},),
    "jobs.resume": ({"action": "jobs.resume", "job_id": "job_...", "after": "continue"},),
    "jobs.cancel": ({"action": "jobs.cancel", "job_id": "job_...", "after": "continue"},),
    "object.inspect": ({"action": "object.inspect", "reference": "doc_...", "after": "continue"},),
    "curation.inspect": ({"action": "curation.inspect", "batch_id": "batch_...", "after": "continue"},),
    "identity.history": ({"action": "identity.history", "limit": 20, "after": "continue"},),
    "next_step": ({"action": "next_step", "receipt_id": "receipt_...", "after": "continue"},),
    "image.share": (
        {
            "action": "image.share",
            "schema_version": "v2",
            "mode": "send",
            "image_id": "img_...",
            "after": "finish",
        },
        {
            "action": "image.share",
            "schema_version": "v2",
            "mode": "send",
            "image_id": "img_private_...",
            "confirm": True,
            "challenge_id": "ch_...",
            "after": "finish",
        },
        {
            "action": "image.share",
            "schema_version": "v1",
            "mode": "prepare",
            "image_id": "img_...",
            "reason": "high-assurance handoff",
            "after": "continue",
        },
    ),
}


RELATED: dict[str, tuple[str, ...]] = {
    "capabilities": ("help", "next_step", "receipt.inspect"),
    "help": ("capabilities", "next_step"),
    "receipt.inspect": ("receipt.pin", "next_step", "attention.tray"),
    "attention.tray": ("search.session", "receipt.inspect"),
    "context.control": ("source.visibility", "source.listening", "retrieval.inspect"),
    "source.visibility": ("context.control", "source.listening"),
    "resident.control": ("source.listening", "image.drawer", "image.share"),
    "source.listening": ("resident.control", "source.visibility"),
    "discord.react": ("image.drawer",),
    "bookmark.add": ("bookmark.open", "bookmark.remove"),
    "jobs.create": ("jobs.step", "jobs.inspect", "jobs.cancel"),
    "image.drawer": ("image.inspect", "image.share"),
    "image.share": ("image.drawer", "receipt.inspect"),
    "bell.draft": ("bell.control", "next_step"),
    "bell.control": ("bell.draft", "next_step"),
}


def contract_for(name: str) -> dict[str, Any]:
    from .bootstrap import bootstrap_runtime
    from .composition import apply_contract_contributions

    bootstrap_runtime()
    fields, required = FIELDS[name]
    examples = EXAMPLES.get(name)
    fields, required, examples, group, related = apply_contract_contributions(
        name,
        fields,
        required,
        examples,
        GROUPS.get(name, "other"),
        RELATED.get(name, ()),
    )
    properties = {
        "action": {"type": "string", "const": name},
        "after": AFTER,
        **fields,
    }
    schema = object_schema(
        properties,
        required=("action", *required),
        additional=False,
        description=f"Executable input contract for {name}.",
    )
    if name == "image.share":
        schema["confirm"] = "required only for private send or legacy claim"
    if examples is None:
        payload: dict[str, Any] = {"action": name, "after": "continue"}
        for field in required:
            payload[field] = _sample(properties[field], field)
        examples = (payload,)
    return {
        "input_schema": schema,
        "example_envelopes": examples,
        "group": group,
        "related_actions": related,
    }


def bell_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    draft_schema = object_schema(
        {
            "title": S(minLength=1),
            "purpose": S(minLength=1),
            "prompt": S(minLength=1),
            "schedule_kind": S(enum=["once", "interval", "daily", "weekly"]),
            "schedule": O(additionalProperties=True),
            "timezone": S(),
            "strength": S(),
            "quiet_start": S(),
            "quiet_end": S(),
            "no_response_required": B(),
            "choose_nothing": B(),
            "expires_at": S(),
        },
        required=("title", "purpose", "prompt", "schedule_kind", "schedule", "timezone"),
        additional=False,
        description="Resident-authored bell preview. It does not activate until claimed.",
    )
    control_schema = object_schema(
        {
            "draft_id": ID,
            "bell_id": ID,
            "action": S(enum=["claim", "reject", "pause", "resume", "delete", "defer", "revise"]),
            "expected_hash": S(),
            "until": S(),
            "reason": S(),
            "title": S(),
            "purpose": S(),
            "prompt": S(),
            "schedule_kind": S(enum=["once", "interval", "daily", "weekly"]),
            "schedule": O(additionalProperties=True),
            "timezone": S(),
            "quiet_start": S(),
            "quiet_end": S(),
            "strength": S(),
            "no_response_required": B(),
            "choose_nothing": B(),
            "expires_at": S(),
        },
        required=("action",),
        additional=False,
        description="Claim/reject a bell draft or control an existing fired-bell record.",
    )
    return (
        {
            "input_schema": draft_schema,
            "example_envelopes": (
                {
                    "title": "Three-hour pulse",
                    "purpose": "look_around",
                    "prompt": "Notice what wants attention, or choose nothing.",
                    "schedule_kind": "interval",
                    "schedule": {"seconds": 10800},
                    "timezone": "America/Chicago",
                },
            ),
            "group": "bells",
            "related_actions": ("bell.control", "next_step"),
        },
        {
            "input_schema": control_schema,
            "example_envelopes": (
                {
                    "draft_id": "bell_draft_...",
                    "action": "claim",
                    "expected_hash": "...",
                },
                {"bell_id": "bell_...", "action": "pause"},
            ),
            "group": "bells",
            "related_actions": ("bell.draft", "next_step"),
        },
    )


def _sample(schema: dict[str, Any], field: str) -> Any:
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "string":
        if field.endswith("_id"):
            return f"{field[:-3]}_..."
        return "example"
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "boolean":
        return True
    if kind == "array":
        return [_sample(schema.get("items", {}), field)]
    if kind == "object":
        return {}
    return None
