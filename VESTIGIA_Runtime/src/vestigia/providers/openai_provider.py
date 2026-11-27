from __future__ import annotations

from typing import Any

from ..config import ResolvedConfig
from ..models import ProviderReply, ProviderRequest


class OpenAIProvider:
    def __init__(self, config: ResolvedConfig) -> None:
        self.config = config
        api_key = config.secret("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Set it in the process environment or "
                "pass --env-file PATH to the CLI command. Without --env-file, VESTIGIA "
                "only checks .env and .env.local in the process current working directory. "
                "Use --fake for deterministic offline tests."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the project dependencies before using the OpenAI provider") from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = str(config.get("provider.base_url", "")).strip()
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def model_for(self, route: str) -> str:
        normalized = (route or "default").strip().lower()
        if normalized == "big":
            if not bool(self.config.get("models.allow_big", True)):
                raise PermissionError("The big model route is disabled")
            return str(self.config.get("models.big"))
        if normalized == "thinking":
            if not bool(self.config.get("models.allow_thinking", True)):
                raise PermissionError("The thinking model route is disabled")
            return str(self.config.get("models.thinking"))
        return str(self.config.get("models.default"))

    def admission_identity(self, route: str) -> dict[str, str]:
        style = str(self.config.get("provider.api_style", "responses")).strip().lower()
        if style not in {"responses", "chat_completions"}:
            raise ValueError("unknown OpenAI API style")
        return {"provider": "openai-compatible" if style == "chat_completions" else "openai",
                "model": self.model_for(route)}

    def complete(self, request: ProviderRequest) -> ProviderReply:
        model = self.model_for(request.model_route)
        api_style = str(self.config.get("provider.api_style", "responses")).strip().lower()
        if api_style == "chat_completions":
            messages = [
                {
                    "role": "system" if item["role"] == "developer" else item["role"],
                    "content": item["content"],
                }
                for item in request.messages
            ]
            response = self.client.chat.completions.create(model=model, messages=messages)
            text = response.choices[0].message.content or ""
            usage = self._dump(getattr(response, "usage", None))
            return ProviderReply(
                text=text,
                provider="openai-compatible",
                model=model,
                reported_model=getattr(response, "model", None) or None,
                response_id=getattr(response, "id", None),
                usage=usage,
            )
        if api_style != "responses":
            raise ValueError("provider.api_style must be responses or chat_completions")

        kwargs: dict[str, Any] = {
            "model": model,
            "input": list(request.messages),
        }
        if request.model_route == "thinking":
            kwargs["reasoning"] = {"effort": str(self.config.get("models.reasoning_effort", "medium"))}
        response = self.client.responses.create(**kwargs)
        return ProviderReply(
            text=response.output_text or "",
            provider="openai",
            model=model,
            reported_model=getattr(response, "model", None) or None,
            response_id=getattr(response, "id", None),
            usage=self._dump(getattr(response, "usage", None)),
        )

    @staticmethod
    def _dump(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        if isinstance(value, dict):
            return dict(value)
        return {"value": str(value)}
