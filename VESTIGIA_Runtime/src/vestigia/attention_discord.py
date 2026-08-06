from __future__ import annotations

import contextvars
from typing import Any

from .attention_router import (
    LexicalDecision,
    increment_counter,
    lexical_decision,
    record_evaluation,
    report,
)
from .utils import sha256_text


_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "vestigia_attention_router_context", default={}
)
_SOURCE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "vestigia_attention_router_source", default={}
)


def _safe_counter(name: str) -> None:
    context = dict(_CONTEXT.get())
    db = context.get("db")
    resident_id = str(context.get("resident_id") or "")
    if db is None or not resident_id:
        return
    try:
        increment_counter(db, resident_id, name)
    except Exception:
        # Routing must not fail merely because observability could not be written.
        pass


def _lexical_from(value: dict[str, Any]) -> LexicalDecision:
    return LexicalDecision(
        route=str(value.get("route") or "ignore"),
        score=int(value.get("score") or 0),
        reasons=tuple(str(item) for item in value.get("reasons", [])),
        matched_term_hashes=tuple(
            str(item) for item in value.get("matched_term_hashes", [])
        ),
        hard_hits=int(value.get("hard_hits") or 0),
        soft_hits=int(value.get("soft_hits") or 0),
        suppress_hits=int(value.get("suppress_hits") or 0),
    )


def patch(module: Any) -> None:
    if getattr(module, "_vestigia_attention_router_patched", False):
        return

    original_platform = module.discord_platform_rejection_reason
    original_load = module.load_resident_controls
    original_trigger = module.discord_trigger_decision
    original_record = module.record_listening_event

    def platform(**kwargs: Any) -> str | None:
        _SOURCE.set(
            {
                "channel_id": str(kwargs.get("channel_id") or ""),
                "is_dm": bool(kwargs.get("is_dm", False)),
            }
        )
        return original_platform(**kwargs)

    def load_controls(config: Any, db: Any, resident_id: str) -> dict[str, Any]:
        values = dict(original_load(config, db, resident_id))
        state = report(
            config,
            db,
            resident_id,
            listening_controls=values,
        )
        values["_attention_router"] = state["effective"]
        _CONTEXT.set(
            {
                "config": config,
                "db": db,
                "resident_id": resident_id,
            }
        )
        return values

    def trigger(
        *,
        is_dm: bool,
        content: str,
        addressed: Any,
        author_allowlisted: bool,
        controls: dict[str, Any],
    ) -> dict[str, Any]:
        decision = original_trigger(
            is_dm=is_dm,
            content=content,
            addressed=addressed,
            author_allowlisted=author_allowlisted,
            controls=controls,
        )
        router = dict(controls.get("_attention_router") or {})
        if not router.get("enabled", True):
            return decision

        # Explicit/direct signals already have a deterministic doorway. The nano gate
        # must never become a tax on direct address.
        if is_dm or bool(addressed):
            _safe_counter("direct_bypass")
            return decision

        lexical = lexical_decision(
            content,
            router,
            author_allowlisted=author_allowlisted,
        )

        # Non-allowlisted material may use the existing hash-only queue boundary, but
        # it is never sent to the semantic gate.
        if not author_allowlisted:
            _safe_counter("non_allowlisted_no_semantic")
            if decision.get("kind") == "contextual_listening":
                match = dict(decision.get("match") or {})
                match["_attention_router"] = {
                    "lexical": lexical.public(),
                    "live_route": "queue",
                    "signal_kind": "ambient_text",
                    "shadow_candidate": False,
                    "semantic_forbidden": True,
                }
                return {**decision, "match": match}
            return decision

        if decision.get("kind") == "contextual_listening":
            live_route = (
                "invite"
                if str(decision.get("consequence")) == "invite_turn"
                else "queue"
            )
            match = dict(decision.get("match") or {})
            match["_attention_router"] = {
                "lexical": lexical.public(),
                "live_route": live_route,
                "signal_kind": "ambient_text",
                "shadow_candidate": True,
            }
            _safe_counter(f"existing_live_{live_route}")
            return {**decision, "match": match}

        if lexical.route == "ignore":
            _safe_counter("local_ignore")
            return decision

        # Shadow mode records what the new router would have done, but the live
        # consequence remains queue-only. No resident model call is created here.
        _safe_counter(f"shadow_{lexical.route}")
        source = dict(_SOURCE.get())
        return {
            "kind": "contextual_listening",
            "consequence": "queue_only",
            "reason": "attention_router_shadow_observation",
            "match": {
                "match_kind": "attention_router_shadow",
                "matched_term": "attention_router_shadow",
                "matched_term_hash": sha256_text(
                    "attention-router-shadow:"
                    + ":".join(lexical.matched_term_hashes)
                ),
                "_sensory": {
                    "signal_kind": "ambient_text",
                    "attention_mode": str(controls.get("attention_mode") or "present"),
                    "retention_mode": "receipt_only",
                    "permission_basis": "allowlisted_attention_router_shadow",
                    "digest_chars": 0,
                },
                "_attention_router": {
                    "lexical": lexical.public(),
                    "live_route": "ignore",
                    "signal_kind": "ambient_text",
                    "shadow_candidate": True,
                    "source_channel_id": str(source.get("channel_id") or ""),
                },
            },
        }

    def record_event(db: Any, **kwargs: Any) -> dict[str, Any]:
        match = dict(kwargs.get("match") or {})
        router_meta = dict(match.get("_attention_router") or {})
        result = original_record(db, **kwargs)
        if not router_meta or not result.get("accepted"):
            return result

        context = dict(_CONTEXT.get())
        config = context.get("config")
        if config is None:
            return {**result, "attention_router_status": "context_unavailable"}
        try:
            router_event = record_evaluation(
                db,
                config,
                resident_id=str(kwargs.get("resident_id") or context.get("resident_id") or ""),
                room_id=str(kwargs.get("room_id") or ""),
                listening_event_id=str(result.get("event_id") or "") or None,
                interface=str(kwargs.get("interface") or "discord"),
                channel_id=str(kwargs.get("channel_id") or ""),
                message_id=str(kwargs.get("message_id") or ""),
                author_trust=str(kwargs.get("author_trust") or ""),
                content=str(kwargs.get("content") or ""),
                lexical=_lexical_from(dict(router_meta.get("lexical") or {})),
                live_route=str(router_meta.get("live_route") or "queue"),
                signal_kind=str(router_meta.get("signal_kind") or "ambient_text"),
            )
            return {
                **result,
                "attention_router_event_id": router_event["id"],
                "attention_router_lexical_route": router_event["lexical_route"],
                "attention_router_semantic_status": router_event["semantic_status"],
                "attention_router_effective_route": router_event["effective_route"],
                "attention_router_shadow_mode": True,
            }
        except Exception as exc:
            # The established sensory decision remains authoritative. A shadow
            # observability failure must not widen or close the live doorway.
            return {
                **result,
                "attention_router_status": "record_failed",
                "attention_router_error_type": type(exc).__name__,
            }

    module.discord_platform_rejection_reason = platform
    module.load_resident_controls = load_controls
    module.discord_trigger_decision = trigger
    module.record_listening_event = record_event
    module._vestigia_attention_router_patched = True
