from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .utils import sha256_text



ROUTES = {"ignore", "queue", "semantic_check", "invite"}
SEMANTIC_ROUTES = {"ignore", "queue", "invite"}
RELEVANCE = {"none", "incidental", "meaningful", "direct"}
REASON_CODES = {
    "direct_address",
    "direct_request",
    "meaningful_relevance",
    "incidental_reference",
    "quoted_or_reported_speech",
    "not_for_resident",
    "insufficient_context",
}

_ROUTER_JOB_KIND = "attention_router_controls"
_SCHEMA_VERSION = "vestigia.attention-router.v0.1"


@dataclass(frozen=True)
class LexicalDecision:
    route: str
    score: int
    reasons: tuple[str, ...]
    matched_term_hashes: tuple[str, ...]
    hard_hits: int = 0
    soft_hits: int = 0
    suppress_hits: int = 0

    def public(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "reasons": list(self.reasons),
            "matched_term_hashes": list(self.matched_term_hashes),
        }


SemanticEvaluator = Callable[[Any, str, dict[str, Any]], dict[str, Any]]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw.strip())


def _env_text(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw.strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).split()).strip()
        key = normalize(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def _phrase_matches(content: str, phrase: str) -> bool:
    normalized_content = normalize(content)
    normalized_phrase = normalize(phrase)
    if not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    if normalized_phrase[0].isalnum() or normalized_phrase[0] == "_":
        pattern = r"(?<!\w)" + pattern
    if normalized_phrase[-1].isalnum() or normalized_phrase[-1] == "_":
        pattern += r"(?!\w)"
    return re.search(pattern, normalized_content, flags=re.UNICODE) is not None


def _term_hash(kind: str, term: str) -> str:
    return sha256_text(f"{kind}:{normalize(term)}")


def _quoted_or_code_like(content: str) -> bool:
    lines = [line.strip() for line in str(content).splitlines() if line.strip()]
    if not lines:
        return False
    quoted = sum(1 for line in lines if line.startswith(">"))
    return "```" in content or quoted >= max(1, len(lines) // 2)


def _question_like(content: str) -> bool:
    text = normalize(content)
    return "?" in content or bool(
        re.match(r"^(can|could|would|will|do|does|did|is|are|was|were|should|may)\b", text)
    )


def operator_settings(config: Any) -> dict[str, Any]:
    semantic_enabled = _env_bool(
        "VESTIGIA_ATTENTION_SEMANTIC_ENABLED",
        bool(config.get("attention_router.semantic_enabled", False)),
    )
    model = _env_text(
        "VESTIGIA_ATTENTION_MODEL",
        str(config.get("attention_router.model", "gpt-5-nano")),
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "enabled": _env_bool(
            "VESTIGIA_ATTENTION_ROUTER_ENABLED",
            bool(config.get("attention_router.enabled", True)),
        ),
        "mode": "shadow",
        "semantic_enabled": semantic_enabled,
        "model": model,
        "reasoning_effort": _env_text(
            "VESTIGIA_ATTENTION_REASONING_EFFORT",
            str(config.get("attention_router.reasoning_effort", "minimal")),
        ),
        "max_calls_per_hour": max(
            0,
            _env_int(
                "VESTIGIA_ATTENTION_MAX_CALLS_PER_HOUR",
                int(config.get("attention_router.max_calls_per_hour", 30)),
            ),
        ),
        "max_calls_per_day": max(
            0,
            _env_int(
                "VESTIGIA_ATTENTION_MAX_CALLS_PER_DAY",
                int(config.get("attention_router.max_calls_per_day", 200)),
            ),
        ),
        "daily_input_token_budget": max(
            0,
            _env_int(
                "VESTIGIA_ATTENTION_DAILY_INPUT_TOKENS",
                int(config.get("attention_router.daily_input_token_budget", 20000)),
            ),
        ),
        "max_message_chars": max(
            80,
            _env_int(
                "VESTIGIA_ATTENTION_MAX_MESSAGE_CHARS",
                int(config.get("attention_router.max_message_chars", 1200)),
            ),
        ),
        "max_output_tokens": max(
            32,
            _env_int(
                "VESTIGIA_ATTENTION_MAX_OUTPUT_TOKENS",
                int(config.get("attention_router.max_output_tokens", 96)),
            ),
        ),
        "cache_hours": max(
            0,
            _env_int(
                "VESTIGIA_ATTENTION_CACHE_HOURS",
                int(config.get("attention_router.cache_hours", 24)),
            ),
        ),
        "invite_confidence": min(
            1.0,
            max(
                0.0,
                _env_float(
                    "VESTIGIA_ATTENTION_INVITE_CONFIDENCE",
                    float(config.get("attention_router.invite_confidence", 0.85)),
                ),
            ),
        ),
        "queue_confidence": min(
            1.0,
            max(
                0.0,
                _env_float(
                    "VESTIGIA_ATTENTION_QUEUE_CONFIDENCE",
                    float(config.get("attention_router.queue_confidence", 0.55)),
                ),
            ),
        ),
        "estimated_resident_input_tokens": max(
            0,
            int(
                config.get(
                    "attention_router.estimated_resident_input_tokens",
                    config.get("context.total_tokens", 20000),
                )
            ),
        ),
        "max_terms": max(
            1, int(config.get("attention_router.max_terms", 64))
        ),
        "max_term_length": max(
            8, int(config.get("attention_router.max_term_length", 80))
        ),
        "allow_non_allowlisted_semantic": False,
        "live_semantic_routing": False,
        "fail_closed_route": "queue",
        "raw_content_storage": False,
    }


def defaults(config: Any) -> dict[str, Any]:
    return {
        "hard_wake_terms": _string_list(
            config.get("attention_router.hard_wake_terms", [])
        ),
        "soft_signal_terms": _string_list(
            config.get("attention_router.soft_signal_terms", [])
        ),
        "suppress_terms": _string_list(
            config.get("attention_router.suppress_terms", [])
        ),
        "include_resident_name": bool(
            config.get("attention_router.include_resident_name", True)
        ),
        "include_listening_aliases": bool(
            config.get("attention_router.include_listening_aliases", True)
        ),
        "include_watch_phrases": bool(
            config.get("attention_router.include_watch_phrases", True)
        ),
        "queue_threshold": int(config.get("attention_router.queue_threshold", 1)),
        "semantic_threshold": int(
            config.get("attention_router.semantic_threshold", 2)
        ),
    }



def lexical_decision(
    content: str,
    controls: dict[str, Any],
    *,
    author_allowlisted: bool,
) -> LexicalDecision:
    if not controls.get("enabled", True):
        return LexicalDecision("ignore", 0, ("router_disabled",), ())
    if not author_allowlisted:
        return LexicalDecision(
            "ignore", 0, ("non_allowlisted_never_semantic",), ()
        )

    reasons: list[str] = []
    hashes: list[str] = []
    hard_hits = 0
    soft_hits = 0
    suppress_hits = 0
    score = 0

    for term in controls.get("hard_wake_terms", []):
        if _phrase_matches(content, str(term)):
            hard_hits += 1
            score += 10
            hashes.append(_term_hash("hard", str(term)))
    for term in controls.get("soft_signal_terms", []):
        if _phrase_matches(content, str(term)):
            soft_hits += 1
            score += 2
            hashes.append(_term_hash("soft", str(term)))
    for term in controls.get("suppress_terms", []):
        if _phrase_matches(content, str(term)):
            suppress_hits += 1
            score -= 12
            hashes.append(_term_hash("suppress", str(term)))

    if hard_hits:
        reasons.append("hard_term")
    if soft_hits:
        reasons.append("soft_term")
    if suppress_hits:
        reasons.append("suppress_term")
    if _quoted_or_code_like(content):
        score -= 2
        reasons.append("quoted_or_code_like")
    if _question_like(content) and (hard_hits or soft_hits):
        score += 1
        reasons.append("question_like")

    if suppress_hits and not hard_hits:
        route = "ignore"
    elif hard_hits and score > 0:
        route = "invite"
    elif score >= int(controls.get("semantic_threshold", 2)):
        route = "semantic_check"
    elif score >= int(controls.get("queue_threshold", 1)):
        route = "queue"
    else:
        route = "ignore"
    if not reasons:
        reasons.append("no_local_signal")
    return LexicalDecision(
        route=route,
        score=score,
        reasons=tuple(reasons),
        matched_term_hashes=tuple(sorted(set(hashes))),
        hard_hits=hard_hits,
        soft_hits=soft_hits,
        suppress_hits=suppress_hits,
    )
