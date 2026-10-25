from __future__ import annotations

from typing import Any

from ..config import ResolvedConfig
from ..models import ProviderReply, ProviderRequest
from .base import ProviderError, dump_usage, model_for_route, safe_provider_error, system_and_conversation


class AnthropicProvider:
    """Native Claude Messages API adapter for text-only Runtime turns."""

    def __init__(self, config: ResolvedConfig, *, client: Any | None = None) -> None:
        self.config = config
        api_key = config.secret("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured for provider.kind=anthropic")
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "Install vestigia-runtime[providers-anthropic] to use Claude"
                ) from exc
            client = Anthropic(api_key=api_key)
        self.client = client

    def model_for(self, route: str) -> str:
        return model_for_route(self.config, route)

    def complete(self, request: ProviderRequest) -> ProviderReply:
        model = self.model_for(request.model_route)
        system, messages = system_and_conversation(request.messages)
        if not messages:
            raise ValueError("Anthropic provider requires at least one conversation message")
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": int(self.config.get("provider.max_output_tokens", 8192)),
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        try:
            response = self.client.messages.create(**kwargs)
        except Exception as exc:
            raise safe_provider_error("Anthropic", exc) from None

        blocks = getattr(response, "content", None)
        if not isinstance(blocks, (list, tuple)):
            raise ProviderError("Anthropic returned an invalid response")
        text_parts = [
            str(getattr(block, "text", ""))
            for block in blocks
            if getattr(block, "type", None) == "text"
        ]
        if not text_parts:
            raise ProviderError("Anthropic returned no text content")
        return ProviderReply(
            text="".join(text_parts),
            provider="anthropic",
            model=str(getattr(response, "model", model)),
            response_id=str(getattr(response, "id", "")) or None,
            usage=dump_usage(getattr(response, "usage", None)),
        )
