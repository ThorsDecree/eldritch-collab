from __future__ import annotations

from typing import Any

from ..config import ResolvedConfig
from ..models import ProviderReply, ProviderRequest
from .base import ProviderError, dump_usage, model_for_route, safe_provider_error, system_and_conversation


class GeminiProvider:
    """Native Gemini Interactions API adapter using stateless text history."""

    def __init__(self, config: ResolvedConfig, *, client: Any | None = None) -> None:
        self.config = config
        api_key = config.secret("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured for provider.kind=gemini")
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError(
                    "Install vestigia-runtime[providers-gemini] to use Gemini"
                ) from exc
            client = genai.Client(api_key=api_key)
        self.client = client

    def model_for(self, route: str) -> str:
        return model_for_route(self.config, route)

    def complete(self, request: ProviderRequest) -> ProviderReply:
        model = self.model_for(request.model_route)
        system, conversation = system_and_conversation(request.messages)
        if not conversation:
            raise ValueError("Gemini provider requires at least one conversation message")
        steps = [
            {
                "type": "model_output" if item["role"] == "assistant" else "user_input",
                "content": [{"type": "text", "text": item["content"]}],
            }
            for item in conversation
        ]
        kwargs: dict[str, Any] = {
            "model": model,
            "input": steps,
            "store": False,
            "generation_config": {
                "max_output_tokens": int(
                    self.config.get("provider.max_output_tokens", 8192)
                )
            },
        }
        if system:
            kwargs["system_instruction"] = system
        try:
            response = self.client.interactions.create(**kwargs)
        except Exception as exc:
            raise safe_provider_error("Gemini", exc) from None

        text = getattr(response, "output_text", None)
        if not isinstance(text, str):
            raise ProviderError("Gemini returned an invalid response")
        return ProviderReply(
            text=text,
            provider="gemini",
            model=str(getattr(response, "model", model)),
            response_id=str(getattr(response, "id", "")) or None,
            usage=dump_usage(getattr(response, "usage", None)),
        )
