from __future__ import annotations

import contextvars
from typing import Any

from .attention_keyring_store import quiet_state, scoped_preference_terms
from .attention_types import _string_list


_SOURCE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "vestigia_attention_keyring_source", default={}
)
_WAKE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "vestigia_attention_keyring_wake", default={}
)


def current_wake_context() -> dict[str, Any]:
    return dict(_WAKE.get())


def consume_wake_context() -> dict[str, Any]:
    value = dict(_WAKE.get())
    _WAKE.set({})
    return value


def _signal_kind(is_dm: bool, addressed: Any, content: str) -> str:
    if is_dm:
        return "dm"
    declared = str(getattr(addressed, "signal_kind", "") or "")
    if declared:
        return declared
    if str(content).lstrip().startswith("!"):
        return "command"
    return "ambient_text"


def _wake_reason(signal: str, decision: dict[str, Any]) -> str:
    direct = {
        "mention": "direct_mention",
        "reply": "direct_reply",
        "dm": "direct_message",
        "command": "command",
    }
    if decision.get("kind") == "direct":
        return direct.get(signal, "direct_signal")
    match = dict(decision.get("match") or {})
    router = dict(match.get("_attention_router") or {})
    if router.get("semantic_route") == "invite":
        return "soft_signal_semantic_invite"
    lexical = dict(router.get("lexical") or {})
    if lexical.get("route") == "invite":
        return "hard_wake_term"
    return "inherited_live_route"


def _merge_preferences(
    router: dict[str, Any], preferences: dict[str, list[str]]
) -> dict[str, Any]:
    result = dict(router)
    result["hard_wake_terms"] = _string_list(
        [*result.get("hard_wake_terms", []), *preferences["always_notice"]]
    )
    result["suppress_terms"] = _string_list(
        [*result.get("suppress_terms", []), *preferences["usually_ignore"]]
    )
    result["soft_signal_terms"] = _string_list(
        [
            *result.get("soft_signal_terms", []),
            *preferences["semantic_check_only"],
        ]
    )
    result["preference_ledger_applied"] = {
        kind: len(values) for kind, values in preferences.items()
    }
    return result


def patch(module: Any) -> None:
    if getattr(module, "_vestigia_attention_keyring_patched", False):
        return

    original_platform = module.discord_platform_rejection_reason
    original_load = module.load_resident_controls
    original_trigger = module.discord_trigger_decision

    def platform(**kwargs: Any) -> str | None:
        _SOURCE.set(
            {
                "channel_id": str(kwargs.get("channel_id") or ""),
                "is_dm": bool(kwargs.get("is_dm", False)),
            }
        )
        _WAKE.set({})
        return original_platform(**kwargs)

    def load_controls(config: Any, db: Any, resident_id: str) -> dict[str, Any]:
        values = dict(original_load(config, db, resident_id))
        source = dict(_SOURCE.get())
        channel_id = str(source.get("channel_id") or "")
        preferences = scoped_preference_terms(
            db,
            resident_id,
            interface="discord",
            channel_id=channel_id,
        )
        values["_attention_router"] = _merge_preferences(
            dict(values.get("_attention_router") or {}), preferences
        )
        quiet = quiet_state(db, resident_id, values)
        effective = dict(quiet["effective"])
        allowed_signals: list[str] = []
        for signal, key in (
            ("ambient_text", "ambient_open"),
            ("mention", "mention_open"),
            ("reply", "reply_open"),
            ("command", "command_open"),
            ("dm", "dm_open"),
        ):
            if effective.get(key):
                allowed_signals.append(signal)
        values["listening_ingress_signals"] = allowed_signals
        values["listening_allow_dms"] = bool(effective.get("dm_open"))
        values["_attention_quiet"] = quiet
        values["_attention_preferences"] = preferences
        return values

    def trigger(
        *,
        is_dm: bool,
        content: str,
        addressed: Any,
        author_allowlisted: bool,
        controls: dict[str, Any],
    ) -> dict[str, Any]:
        signal = _signal_kind(is_dm, addressed, content)
        recovery = str(content).strip().casefold() in {"!wake", "!status"}
        quiet = dict(controls.get("_attention_quiet") or {})
        effective = dict(quiet.get("effective") or {})
        signal_key = {
            "ambient_text": "ambient_open",
            "mention": "mention_open",
            "reply": "reply_open",
            "command": "command_open",
            "dm": "dm_open",
        }.get(signal, "ambient_open")
        if quiet.get("phase") in {"quiet", "restored_locked"}:
            if not bool(effective.get(signal_key, False)) and not recovery:
                _WAKE.set({})
                prefix = (
                    "resident_restoration_cap"
                    if quiet.get("phase") == "restored_locked"
                    else "resident_quiet"
                )
                return {
                    "kind": "ignored",
                    "consequence": "ignore",
                    "match": None,
                    "reason": f"{prefix}_{signal}_closed",
                }

        routed_controls = controls
        if recovery:
            routed_controls = dict(controls)
            signals = set(routed_controls.get("listening_ingress_signals", []))
            signals.update({"command", "dm"})
            routed_controls["listening_ingress_signals"] = sorted(signals)
            routed_controls["listening_allow_dms"] = True

        decision = original_trigger(
            is_dm=is_dm,
            content=content,
            addressed=addressed,
            author_allowlisted=author_allowlisted,
            controls=routed_controls,
        )
        if str(decision.get("consequence") or "") != "invite_turn":
            _WAKE.set({})
            return decision
        source = dict(_SOURCE.get())
        _WAKE.set(
            {
                "interface": "discord",
                "channel_id": str(source.get("channel_id") or ""),
                "signal_kind": signal,
                "reason_code": _wake_reason(signal, decision),
                "live_route": "invite",
                "quiet_phase": str(quiet.get("phase") or "open"),
                "platform_allowed": True,
                "resident_scope_allowed": True,
            }
        )
        return decision

    module.discord_platform_rejection_reason = platform
    module.load_resident_controls = load_controls
    module.discord_trigger_decision = trigger
    module._vestigia_attention_keyring_patched = True
