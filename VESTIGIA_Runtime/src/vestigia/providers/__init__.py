from .base import Provider
from .fake import FakeProvider
from .anthropic_provider import AnthropicProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider


def build_provider(config, *, fake: bool = False) -> Provider:
    kind = str(config.get("provider.kind", "openai")).strip().lower()
    if fake or kind == "fake":
        return FakeProvider()
    if kind == "openai":
        return OpenAIProvider(config)
    if kind == "anthropic":
        return AnthropicProvider(config)
    if kind == "gemini":
        return GeminiProvider(config)
    raise ValueError("provider.kind must be openai, anthropic, gemini, or fake")


__all__ = [
    "AnthropicProvider",
    "FakeProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "Provider",
    "build_provider",
]

