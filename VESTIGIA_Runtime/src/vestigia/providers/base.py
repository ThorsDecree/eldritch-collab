from __future__ import annotations

from typing import Any, Protocol

from ..models import ProviderReply, ProviderRequest


class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderReply:
        ...


class ProviderError(RuntimeError):
    """A provider failure safe to expose without echoing request secrets/content."""


def model_for_route(config: Any, route: str) -> str:
    normalized = (route or "default").strip().lower()
    if normalized == "big":
        if not bool(config.get("models.allow_big", True)):
            raise PermissionError("The big model route is disabled")
        return str(config.get("models.big"))
    if normalized == "thinking":
        if not bool(config.get("models.allow_thinking", True)):
            raise PermissionError("The thinking model route is disabled")
        return str(config.get("models.thinking"))
    return str(config.get("models.default"))


def system_and_conversation(
    messages: tuple[dict[str, str], ...],
) -> tuple[str, list[dict[str, str]]]:
    """Map Runtime text messages to vendor system plus alternating user/assistant turns."""
    system: list[str] = []
    conversation: list[dict[str, str]] = []
    conversation_started = False
    for item in messages:
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("content", ""))
        if not content:
            continue
        if role in {"developer", "system"} and not conversation_started:
            system.append(content)
            continue
        conversation_started = True
        normalized_role = "assistant" if role == "assistant" else "user"
        if conversation and conversation[-1]["role"] == normalized_role:
            conversation[-1]["content"] += "\n\n" + content
        else:
            conversation.append({"role": normalized_role, "content": content})
    return "\n\n".join(system), conversation


def dump_usage(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        result = value.model_dump()
        return dict(result) if isinstance(result, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            key: item
            for key, item in attributes.items()
            if not key.startswith("_")
        }
    return {"value": str(value)}


def safe_provider_error(name: str, exc: Exception) -> ProviderError:
    return ProviderError(f"{name} request failed ({type(exc).__name__})")

