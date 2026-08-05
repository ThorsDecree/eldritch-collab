from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from .sensory_controls import report
from .sensory_events import mark, record
from .utils import sha256_text


_SOURCE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "vestigia_discord_sensory_source", default={}
)


@dataclass(frozen=True)
class AddressDecision:
    addressed: bool
    signal_kind: str

    def __bool__(self) -> bool:
        return self.addressed

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return self.addressed == other
        return super().__eq__(other)


def _address(
    *,
    is_dm: bool,
    content: str,
    bot_is_mentioned: bool,
    replies_to_bot: bool,
    require_mention_or_reply: bool,
) -> AddressDecision:
    if is_dm:
        return AddressDecision(True, "dm")
    if content.lstrip().startswith("!"):
        return AddressDecision(True, "command")
    if not require_mention_or_reply:
        return AddressDecision(True, "ambient_text")
    if bot_is_mentioned:
        return AddressDecision(True, "mention")
    if replies_to_bot:
        return AddressDecision(True, "reply")
    return AddressDecision(False, "ambient_text")


def patch(module: Any) -> None:
    if getattr(module, "_vestigia_sensory_patched", False):
        return
    original_platform = module.discord_platform_rejection_reason
    original_load = module.load_resident_controls
    original_trigger = module.discord_trigger_decision
    original_record = module.record_listening_event
    original_mark = module.mark_listening_event
    ensure_listening_schema = module.record_listening_event.__globals__[
        "ensure_listening_schema"
    ]

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
        values.update(report(config, db, resident_id)["effective"])
        return values

    def trigger(
        *,
        is_dm: bool,
        content: str,
        addressed: Any,
        author_allowlisted: bool,
        controls: dict[str, Any],
    ) -> dict[str, Any]:
        source = dict(_SOURCE.get())
        channel_id = str(source.get("channel_id") or "")
        signal = str(
            getattr(addressed, "signal_kind", "dm" if is_dm else "ambient_text")
        )
        mode = str(controls.get("attention_mode") or "present")
        retention = str(controls.get("listening_retention") or "live_context")
        allowed_signals = {
            str(item)
            for item in controls.get(
                "listening_ingress_signals",
                ["mention", "reply", "dm", "command", "ambient_text"],
            )
        }
        included = {str(item) for item in controls.get("listening_channel_ids", [])}
        excluded = {
            str(item) for item in controls.get("listening_excluded_channel_ids", [])
        }
        recovery_command = content.casefold() in {"!wake", "!status"}

        if is_dm and not bool(controls.get("listening_allow_dms", True)):
            return {"kind": "ignored", "consequence": "ignore", "match": None,
                    "reason": "resident_dm_scope_disabled"}
        if not is_dm:
            if channel_id and channel_id in excluded:
                return {"kind": "ignored", "consequence": "ignore", "match": None,
                        "reason": "resident_channel_excluded"}
            if included and (not channel_id or channel_id not in included):
                return {"kind": "ignored", "consequence": "ignore", "match": None,
                        "reason": "outside_resident_channel_scope"}

        if bool(addressed) and author_allowlisted:
            if signal not in allowed_signals and not recovery_command:
                return {"kind": "ignored", "consequence": "ignore", "match": None,
                        "reason": "signal_disabled_by_resident"}
            if mode == "deaf" and not recovery_command:
                return {"kind": "ignored", "consequence": "ignore", "match": None,
                        "reason": "resident_temporarily_deaf"}
            if mode in {"peeking", "digest_only", "asleep"} and not recovery_command:
                return {
                    "kind": "contextual_listening",
                    "consequence": "queue_only",
                    "reason": "resident_attention_mode_queue_only",
                    "match": {
                        "match_kind": "attention_mode",
                        "matched_term": signal,
                        "matched_term_hash": sha256_text(f"attention:{signal}"),
                        "_sensory": {
                            "signal_kind": signal,
                            "attention_mode": mode,
                            "retention_mode": retention,
                            "permission_basis": (
                                "allowlisted_direct_signal_held_while_asleep"
                                if mode == "asleep"
                                else "allowlisted_direct_signal_observed_without_turn"
                            ),
                            "digest_chars": controls.get("listening_digest_chars", 280),
                        },
                    },
                }
            return {
                "kind": "direct",
                "consequence": "invite_turn",
                "match": None,
                "reason": "allowlisted_direct_signal",
            }

        if is_dm:
            return {"kind": "ignored", "consequence": "ignore", "match": None,
                    "reason": "dm_not_directly_authorized"}
        if "ambient_text" not in allowed_signals:
            return {"kind": "ignored", "consequence": "ignore", "match": None,
                    "reason": "ambient_signal_disabled_by_resident"}
        if mode == "deaf":
            return {"kind": "ignored", "consequence": "ignore", "match": None,
                    "reason": "resident_temporarily_deaf"}

        decision = original_trigger(
            is_dm=is_dm,
            content=content,
            addressed=False,
            author_allowlisted=author_allowlisted,
            controls=controls,
        )
        if decision.get("kind") != "contextual_listening":
            return decision
        match = dict(decision.get("match") or {})
        match["_sensory"] = {
            "signal_kind": "ambient_text",
            "attention_mode": mode,
            "retention_mode": retention,
            "permission_basis": "resident_literal_listening_policy",
            "digest_chars": controls.get("listening_digest_chars", 280),
        }
        consequence = str(decision.get("consequence") or "queue_only")
        if mode != "present" or retention != "live_context":
            consequence = "queue_only"
        return {**decision, "match": match, "consequence": consequence}

    def record_event(db: Any, **kwargs: Any) -> dict[str, Any]:
        return record(
            original_record,
            ensure_listening_schema,
            db,
            **kwargs,
        )

    def mark_event(db: Any, event_id: str, *, status: str) -> None:
        mark(
            original_mark,
            ensure_listening_schema,
            db,
            event_id,
            status=status,
        )

    module.discord_platform_rejection_reason = platform
    module.load_resident_controls = load_controls
    module.guild_message_is_addressed = _address
    module.discord_trigger_decision = trigger
    module.record_listening_event = record_event
    module.mark_listening_event = mark_event
    module._vestigia_sensory_patched = True
