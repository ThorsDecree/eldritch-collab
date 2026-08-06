from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .utils import parse_csv


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": "vestigia.home.v0.1",
    "resident": {
        "id": "resident",
        "name": "Resident",
        "glyph": "🏮",
    },
    "room": {
        "id": "hearth",
        "name": "Hearth",
        "active_resident_ids": ["resident"],
        "participant_ids": ["resident", "local-user"],
    },
    "interface": {
        "default": "auto",
        "cli": {"enabled": True},
        "discord": {"enabled": False},
    },
    "provider": {
        "kind": "openai",
        "api_style": "responses",
        "base_url": "",
    },
    "models": {
        "default": "gpt-5-mini",
        "big": "gpt-5.6",
        "thinking": "gpt-5.6",
        "image": "gpt-image-2",
        "vision": "gpt-5-mini",
        "reasoning_effort": "medium",
        "allow_big": True,
        "allow_thinking": True,
        "auto_route_big": False,
        "auto_route_thinking": False,
    },
    "context": {
        "total_tokens": 20000,
        "runtime_contract_tokens": 1000,
        "identity_core_tokens": 800,
        "relationship_tokens": 1200,
        "tension_tokens": 1200,
        "retrieval_tokens": 3800,
        "attention_tray_tokens": 900,
        "breadcrumb_tokens": 500,
        "session_summary_tokens": 2000,
        "transcript_tail_tokens": 3800,
        "verbatim_turns": 12,
        "compression_source_turns": 60,
        "compressed_transcript_tokens": 3500,
        "current_message_tokens": 2000,
        "image_context_tokens": 3000,
        "capability_panel_tokens": 2200,
        "resident_max_total_tokens": 20000,
        "resident_max_verbatim_turns": 100,
        "resident_max_compression_source_turns": 2000,
        "resident_max_compressed_transcript_tokens": 20000,
    },
    "retrieval": {
        "limit": 18,
        "transcript_tail_messages": 12,
        "include_inherited_during_orientation": True,
    },
    "memory": {
        "auto_extract_conservative_candidates": True,
        "core_hard_limit_tokens": 2000,
    },
    "curation": {
        "enabled": True,
        "model_route": "default",
        "cadence_exchanges": 3,
        "batch_max_items": 8,
        "packet_tokens": 4500,
        "queue_pressure": 8,
    },
    "house": {
        "enabled": True,
        "max_private_turns": 6,
        "max_tool_rounds": 5,
        "max_tool_calls": 12,
        "max_result_tokens": 6000,
        "max_file_bytes": 5000000,
        "max_write_bytes": 1000000,
        "chunk_chars": 6000,
        "receipt_context_limit": 6,
        "job_max_operations": 24,
        "attention_tray_hours": 24,
        "accessible_roots": [
            "identity",
            "imports",
            "sessions",
            "scrapbook",
            "artifacts",
            "exports",
            "workspace",
        ],
        "writable_roots": ["workspace"],
    },
    "forge": {
        "enabled": True,
        "max_steps": 6,
    },
    "traces": {
        "save_full_context": False,
        "save_prompt_hashes": True,
    },
    "images": {
        "enabled": True,
        "edits_enabled": True,
        "default_size": "auto",
        "default_quality": "auto",
        "max_per_request": 2,
        "daily_limit": 20,
        "require_confirmation": False,
        "save_prompts": True,
        "preserve_inputs": True,
        "store_received": True,
        "max_input_bytes": 20000000,
        "ocr_enabled": True,
        "ocr_binary": "tesseract",
        "ocr_language": "eng",
        "ocr_page_segmentation": 6,
        "ocr_timeout_seconds": 20,
        "ocr_recipe": "direct-psm6",
        "vision_enabled": True,
        "vision_default_detail": "low",
        "job_poll_seconds": 3,
        "job_stale_seconds": 900,
    },
    "discord": {
        "allowed_user_ids": [],
        "allowed_channel_ids": [],
        "allow_dms": True,
        "require_mention_or_reply_in_guilds": True,
        "log_rejections": False,
        "recent_messages": 10,
        "recent_max_chars": 2200,
        "ambient_visibility": "allowlisted_only",
        "listening_mode": "direct_only",
        "listening_aliases": [],
        "listening_watch_phrases": [],
        "listening_on_match": "queue_only",
        "listening_cooldown_seconds": 20,
        "max_message_chars": 1900,
        "rate_limit_user_calls": 6,
        "rate_limit_user_window": 60,
        "activity_window": False,
        "activity_poll_seconds": 2,
    },
    "resident_controls": {
        "allowed_private_image_modes": [
            "challenge",
            "quickdraw_pockets",
            "quickdraw_adopted",
        ],
        "allowed_listening_modes": [
            "direct_only",
            "aliases",
            "watchlist",
            "all_allowlisted",
        ],
        "allowed_listening_on_match": ["queue_only", "invite_turn"],
        "max_quickdraw_pockets": 24,
        "max_listening_terms": 24,
        "max_listening_term_length": 80,
        "min_listening_cooldown_seconds": 5,
        "max_listening_cooldown_seconds": 3600,
    },
    "bells": {
        "enabled": True,
        "poll_seconds": 30,
        "timezone": "UTC",
        "quiet_start": "22:00",
        "quiet_end": "08:00",
    },
}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str) -> int:
    return int(value.strip())


ENV_MAP: dict[str, tuple[str, Callable[[str], Any]]] = {
    "VESTIGIA_PROVIDER": ("provider.kind", str),
    "VESTIGIA_API_STYLE": ("provider.api_style", str),
    "OPENAI_BASE_URL": ("provider.base_url", str),
    "VESTIGIA_MODEL_DEFAULT": ("models.default", str),
    "VESTIGIA_MODEL_BIG": ("models.big", str),
    "VESTIGIA_MODEL_THINKING": ("models.thinking", str),
    "VESTIGIA_MODEL_IMAGE": ("models.image", str),
    "VESTIGIA_MODEL_VISION": ("models.vision", str),
    "VESTIGIA_REASONING_EFFORT": ("models.reasoning_effort", str),
    "VESTIGIA_ALLOW_BIG_MODEL": ("models.allow_big", _as_bool),
    "VESTIGIA_ALLOW_THINKING_MODEL": ("models.allow_thinking", _as_bool),
    "VESTIGIA_AUTO_ROUTE_BIG_MODEL": ("models.auto_route_big", _as_bool),
    "VESTIGIA_AUTO_ROUTE_THINKING_MODEL": ("models.auto_route_thinking", _as_bool),
    "VESTIGIA_CONTEXT_TOKENS": ("context.total_tokens", _as_int),
    "VESTIGIA_RUNTIME_TOKENS": ("context.runtime_contract_tokens", _as_int),
    "VESTIGIA_CORE_TOKENS": ("context.identity_core_tokens", _as_int),
    "VESTIGIA_RELATIONSHIP_TOKENS": ("context.relationship_tokens", _as_int),
    "VESTIGIA_TENSION_TOKENS": ("context.tension_tokens", _as_int),
    "VESTIGIA_RETRIEVAL_TOKENS": ("context.retrieval_tokens", _as_int),
    "VESTIGIA_ATTENTION_TRAY_TOKENS": ("context.attention_tray_tokens", _as_int),
    "VESTIGIA_SESSION_SUMMARY_TOKENS": ("context.session_summary_tokens", _as_int),
    "VESTIGIA_TRANSCRIPT_TAIL_TOKENS": ("context.transcript_tail_tokens", _as_int),
    "VESTIGIA_VERBATIM_TURNS": ("context.verbatim_turns", _as_int),
    "VESTIGIA_COMPRESSION_SOURCE_TURNS": ("context.compression_source_turns", _as_int),
    "VESTIGIA_COMPRESSED_TRANSCRIPT_TOKENS": ("context.compressed_transcript_tokens", _as_int),
    "VESTIGIA_CURRENT_MESSAGE_TOKENS": ("context.current_message_tokens", _as_int),
    "VESTIGIA_IMAGE_CONTEXT_TOKENS": ("context.image_context_tokens", _as_int),
    "VESTIGIA_CAPABILITY_PANEL_TOKENS": ("context.capability_panel_tokens", _as_int),
    "VESTIGIA_RESIDENT_MAX_TOTAL_TOKENS": ("context.resident_max_total_tokens", _as_int),
    "VESTIGIA_RESIDENT_MAX_VERBATIM_TURNS": ("context.resident_max_verbatim_turns", _as_int),
    "VESTIGIA_RESIDENT_MAX_COMPRESSION_SOURCE_TURNS": ("context.resident_max_compression_source_turns", _as_int),
    "VESTIGIA_RESIDENT_MAX_COMPRESSED_TRANSCRIPT_TOKENS": ("context.resident_max_compressed_transcript_tokens", _as_int),
    "VESTIGIA_RETRIEVAL_LIMIT": ("retrieval.limit", _as_int),
    "VESTIGIA_TRANSCRIPT_TAIL_MESSAGES": ("retrieval.transcript_tail_messages", _as_int),
    "VESTIGIA_AUTO_EXTRACT_MEMORY": ("memory.auto_extract_conservative_candidates", _as_bool),
    "VESTIGIA_CURATION_ENABLED": ("curation.enabled", _as_bool),
    "VESTIGIA_CURATION_MODEL_ROUTE": ("curation.model_route", str),
    "VESTIGIA_CURATION_CADENCE": ("curation.cadence_exchanges", _as_int),
    "VESTIGIA_CURATION_BATCH_MAX": ("curation.batch_max_items", _as_int),
    "VESTIGIA_CURATION_PACKET_TOKENS": ("curation.packet_tokens", _as_int),
    "VESTIGIA_HOUSE_ENABLED": ("house.enabled", _as_bool),
    "VESTIGIA_RESIDENT_MAX_PRIVATE_TURNS": ("house.max_private_turns", _as_int),
    "VESTIGIA_HOUSE_TOOL_ROUNDS": ("house.max_tool_rounds", _as_int),
    "VESTIGIA_RESIDENT_MAX_TOOL_CALLS": ("house.max_tool_calls", _as_int),
    "VESTIGIA_HOUSE_TOOL_CALLS": ("house.max_tool_calls", _as_int),
    "VESTIGIA_HOUSE_RESULT_TOKENS": ("house.max_result_tokens", _as_int),
    "VESTIGIA_HOUSE_MAX_FILE_BYTES": ("house.max_file_bytes", _as_int),
    "VESTIGIA_HOUSE_MAX_WRITE_BYTES": ("house.max_write_bytes", _as_int),
    "VESTIGIA_RECEIPT_CONTEXT_LIMIT": ("house.receipt_context_limit", _as_int),
    "VESTIGIA_JOB_MAX_OPERATIONS": ("house.job_max_operations", _as_int),
    "VESTIGIA_ATTENTION_TRAY_HOURS": ("house.attention_tray_hours", _as_int),
    "VESTIGIA_FORGE_ENABLED": ("forge.enabled", _as_bool),
    "VESTIGIA_FORGE_MAX_MANIFEST_STEPS": ("forge.max_steps", _as_int),
    "VESTIGIA_FORGE_MAX_STEPS": ("forge.max_steps", _as_int),
    "VESTIGIA_SAVE_FULL_CONTEXT": ("traces.save_full_context", _as_bool),
    "VESTIGIA_IMAGES_ENABLED": ("images.enabled", _as_bool),
    "VESTIGIA_IMAGE_EDITS_ENABLED": ("images.edits_enabled", _as_bool),
    "VESTIGIA_IMAGE_DEFAULT_SIZE": ("images.default_size", str),
    "VESTIGIA_IMAGE_DEFAULT_QUALITY": ("images.default_quality", str),
    "VESTIGIA_IMAGE_MAX_PER_REQUEST": ("images.max_per_request", _as_int),
    "VESTIGIA_IMAGE_DAILY_LIMIT": ("images.daily_limit", _as_int),
    "VESTIGIA_IMAGE_REQUIRE_CONFIRMATION": ("images.require_confirmation", _as_bool),
    "VESTIGIA_IMAGE_SAVE_PROMPTS": ("images.save_prompts", _as_bool),
    "VESTIGIA_IMAGE_PRESERVE_INPUTS": ("images.preserve_inputs", _as_bool),
    "VESTIGIA_IMAGE_STORE_RECEIVED": ("images.store_received", _as_bool),
    "VESTIGIA_IMAGE_MAX_INPUT_BYTES": ("images.max_input_bytes", _as_int),
    "VESTIGIA_OCR_ENABLED": ("images.ocr_enabled", _as_bool),
    "VESTIGIA_OCR_BINARY": ("images.ocr_binary", str),
    "VESTIGIA_OCR_LANGUAGE": ("images.ocr_language", str),
    "VESTIGIA_OCR_PAGE_SEGMENTATION": ("images.ocr_page_segmentation", _as_int),
    "VESTIGIA_OCR_TIMEOUT_SECONDS": ("images.ocr_timeout_seconds", _as_int),
    "VESTIGIA_VISION_ENABLED": ("images.vision_enabled", _as_bool),
    "VESTIGIA_VISION_DEFAULT_DETAIL": ("images.vision_default_detail", str),
    "VESTIGIA_IMAGE_JOB_POLL_SECONDS": ("images.job_poll_seconds", _as_int),
    "VESTIGIA_IMAGE_JOB_STALE_SECONDS": ("images.job_stale_seconds", _as_int),
    "VESTIGIA_DISCORD_ENABLED": ("interface.discord.enabled", _as_bool),
    "DISCORD_ALLOWED_USER_IDS": ("discord.allowed_user_ids", parse_csv),
    "DISCORD_ALLOWED_CHANNEL_IDS": ("discord.allowed_channel_ids", parse_csv),
    "VESTIGIA_DISCORD_ALLOW_DMS": ("discord.allow_dms", _as_bool),
    "VESTIGIA_DISCORD_REQUIRE_MENTION_OR_REPLY": (
        "discord.require_mention_or_reply_in_guilds",
        _as_bool,
    ),
    "VESTIGIA_DISCORD_LOG_REJECTIONS": ("discord.log_rejections", _as_bool),
    "VESTIGIA_DISCORD_RECENT_MESSAGES": ("discord.recent_messages", _as_int),
    "VESTIGIA_DISCORD_RECENT_MAX_CHARS": ("discord.recent_max_chars", _as_int),
    "VESTIGIA_DISCORD_AMBIENT_VISIBILITY": ("discord.ambient_visibility", str),
    "VESTIGIA_DISCORD_LISTENING_MODE": ("discord.listening_mode", str),
    "VESTIGIA_DISCORD_LISTENING_ALIASES": ("discord.listening_aliases", parse_csv),
    "VESTIGIA_DISCORD_LISTENING_WATCH_PHRASES": (
        "discord.listening_watch_phrases",
        parse_csv,
    ),
    "VESTIGIA_DISCORD_LISTENING_ON_MATCH": ("discord.listening_on_match", str),
    "VESTIGIA_DISCORD_LISTENING_COOLDOWN_SECONDS": (
        "discord.listening_cooldown_seconds",
        _as_int,
    ),
    "VESTIGIA_RESIDENT_ALLOWED_PRIVATE_IMAGE_MODES": (
        "resident_controls.allowed_private_image_modes",
        parse_csv,
    ),
    "VESTIGIA_RESIDENT_ALLOWED_LISTENING_MODES": (
        "resident_controls.allowed_listening_modes",
        parse_csv,
    ),
    "VESTIGIA_DISCORD_MAX_MESSAGE_CHARS": ("discord.max_message_chars", _as_int),
    "VESTIGIA_RATE_LIMIT_USER_CALLS": ("discord.rate_limit_user_calls", _as_int),
    "VESTIGIA_RATE_LIMIT_USER_WINDOW": ("discord.rate_limit_user_window", _as_int),
    "VESTIGIA_DISCORD_ACTIVITY_WINDOW": ("discord.activity_window", _as_bool),
    "VESTIGIA_DISCORD_ACTIVITY_POLL_SECONDS": (
        "discord.activity_poll_seconds",
        _as_int,
    ),
    "VESTIGIA_BELLS_ENABLED": ("bells.enabled", _as_bool),
    "VESTIGIA_BELL_POLL_SECONDS": ("bells.poll_seconds", _as_int),
    "VESTIGIA_BELL_TIMEZONE": ("bells.timezone", str),
    "VESTIGIA_BELL_QUIET_START": ("bells.quiet_start", str),
    "VESTIGIA_BELL_QUIET_END": ("bells.quiet_end", str),
}


@dataclass(frozen=True)
class ResolvedConfig:
    data: dict[str, Any]
    sources: dict[str, str]
    home_path: Path
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def get(self, dotted: str, default: Any = None) -> Any:
        cursor: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def secret(self, name: str) -> str:
        return self.secrets.get(name, "").strip()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any], prefix: str, sources: dict[str, str]) -> None:
    for key, value in overlay.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value, dotted, sources)
        else:
            base[key] = value
            sources[dotted] = "home.yaml"


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = data
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _flatten_defaults(value: Any, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            dotted = f"{prefix}.{key}" if prefix else key
            result.update(_flatten_defaults(child, dotted))
    else:
        result[prefix] = "built-in"
    return result


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def load_config(home_path: str | Path, env_file: str | Path | None = None) -> ResolvedConfig:
    home = Path(home_path).resolve()
    config_path = home / "home.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Not a VESTIGIA home: missing {config_path}")

    data = copy.deepcopy(DEFAULT_CONFIG)
    sources = _flatten_defaults(data)
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("home.yaml must contain a mapping")
    _deep_merge(data, loaded, "", sources)

    env_values: dict[str, str] = {}
    candidates = [Path(env_file)] if env_file else []
    if not candidates:
        candidates = [Path.cwd() / ".env", Path.cwd() / ".env.local"]
    for candidate in candidates:
        env_values.update(_parse_env_file(candidate.resolve()))
    env_values.update(os.environ)

    for env_name, (dotted, converter) in ENV_MAP.items():
        raw = env_values.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            converted = converter(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {env_name}: {exc}") from exc
        _set_dotted(data, dotted, converted)
        sources[dotted] = f"environment:{env_name}"

    resident_id = str(data["resident"]["id"])
    active = [str(item) for item in data["room"].get("active_resident_ids", [])]
    if resident_id not in active:
        raise ValueError("v0.1 requires resident.id to be active in room.active_resident_ids")
    if len(active) != 1:
        raise ValueError("v0.1 implements exactly one active resident per turn")

    if int(data["context"]["total_tokens"]) < 1000:
        raise ValueError("context.total_tokens must be at least 1000")
    if str(data["discord"].get("ambient_visibility", "allowlisted_only")) not in {
        "allowlisted_only",
        "all_channel",
        "mentions_only",
        "hidden",
    }:
        raise ValueError("discord.ambient_visibility is invalid")
    secrets = {
        name: str(env_values.get(name, "")).strip()
        for name in ("OPENAI_API_KEY", "DISCORD_BOT_TOKEN")
    }
    return ResolvedConfig(data=data, sources=sources, home_path=home, secrets=secrets)


def dump_home_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
