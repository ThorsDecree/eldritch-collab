from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vestigia.config import ResolvedConfig, load_config
from vestigia.home import initialize_home
from vestigia.models import ProviderRequest
from vestigia.providers import AnthropicProvider, GeminiProvider, build_provider
from vestigia.web_app import _status


def _config(*, provider="anthropic", secrets=None, **model_overrides):
    models = {
        "default": "default-model",
        "big": "big-model",
        "thinking": "thinking-model",
        "allow_big": True,
        "allow_thinking": True,
    }
    models.update(model_overrides)
    return ResolvedConfig(
        data={
            "provider": {"kind": provider, "max_output_tokens": 8192},
            "models": models,
        },
        sources={},
        home_path=Path("/tmp/provider-test-home"),
        secrets=secrets or {},
    )


def _request(route="default"):
    return ProviderRequest(
        turn_id="turn-provider-test",
        model_route=route,
        messages=(
            {"role": "developer", "content": "Resident system prompt."},
            {"role": "user", "content": "First question."},
            {"role": "assistant", "content": "Earlier answer."},
            {"role": "developer", "content": "Private action receipt."},
            {"role": "user", "content": "Continue."},
        ),
    )


class _AnthropicClient:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

        class Messages:
            def __init__(inner, outer):
                inner.outer = outer

            def create(inner, **kwargs):
                inner.outer.calls.append(kwargs)
                if inner.outer.failure:
                    raise inner.outer.failure
                return inner.outer.response

        self.messages = Messages(self)


class _GeminiClient:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

        class Interactions:
            def __init__(inner, outer):
                inner.outer = outer

            def create(inner, **kwargs):
                inner.outer.calls.append(kwargs)
                if inner.outer.failure:
                    raise inner.outer.failure
                return inner.outer.response

        self.interactions = Interactions(self)


def test_anthropic_adapter_translates_system_and_stateless_messages():
    response = SimpleNamespace(
        id="message-123",
        model="claude-test",
        content=[SimpleNamespace(type="text", text="Hello from Claude.")],
        usage=SimpleNamespace(input_tokens=17, output_tokens=5),
    )
    client = _AnthropicClient(response)
    provider = AnthropicProvider(_config(secrets={"ANTHROPIC_API_KEY": "secret"}), client=client)

    reply = provider.complete(_request("big"))

    assert client.calls == [
        {
            "model": "big-model",
            "max_tokens": 8192,
            "system": "Resident system prompt.",
            "messages": [
                {"role": "user", "content": "First question."},
                {"role": "assistant", "content": "Earlier answer."},
                {"role": "user", "content": "Private action receipt.\n\nContinue."},
            ],
        }
    ]
    assert reply.text == "Hello from Claude."
    assert reply.provider == "anthropic"
    assert reply.model == "claude-test"
    assert reply.response_id == "message-123"
    assert reply.usage["input_tokens"] == 17


def test_gemini_adapter_uses_native_stateless_interactions():
    response = SimpleNamespace(
        id="interaction-456",
        model="gemini-test",
        output_text="Hello from Gemini.",
        usage=SimpleNamespace(total_input_tokens=19, total_output_tokens=7),
    )
    client = _GeminiClient(response)
    provider = GeminiProvider(_config(provider="gemini", secrets={"GEMINI_API_KEY": "secret"}), client=client)

    reply = provider.complete(_request("thinking"))

    call = client.calls[0]
    assert call["model"] == "thinking-model"
    assert call["system_instruction"] == "Resident system prompt."
    assert call["store"] is False
    assert call["generation_config"] == {"max_output_tokens": 8192}
    assert call["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "First question."}]},
        {"type": "model_output", "content": [{"type": "text", "text": "Earlier answer."}]},
        {"type": "user_input", "content": [{"type": "text", "text": "Private action receipt.\n\nContinue."}]},
    ]
    assert "tools" not in call
    assert reply.text == "Hello from Gemini."
    assert reply.provider == "gemini"
    assert reply.model == "gemini-test"
    assert reply.response_id == "interaction-456"
    assert reply.usage["total_input_tokens"] == 19


@pytest.mark.parametrize(
    ("provider_type", "secret_name"),
    [(AnthropicProvider, "ANTHROPIC_API_KEY"), (GeminiProvider, "GEMINI_API_KEY")],
)
def test_native_provider_requires_its_own_credential(provider_type, secret_name):
    with pytest.raises(RuntimeError, match=secret_name):
        provider_type(_config(provider="gemini" if secret_name == "GEMINI_API_KEY" else "anthropic"))


@pytest.mark.parametrize(
    ("provider_type", "client", "secret"),
    [
        (AnthropicProvider, _AnthropicClient(failure=RuntimeError("key=private-anthropic-key")), "private-anthropic-key"),
        (GeminiProvider, _GeminiClient(failure=RuntimeError("key=private-gemini-key")), "private-gemini-key"),
    ],
)
def test_native_provider_errors_do_not_echo_secrets(provider_type, client, secret):
    provider_name = "gemini" if provider_type is GeminiProvider else "anthropic"
    secret_name = "GEMINI_API_KEY" if provider_name == "gemini" else "ANTHROPIC_API_KEY"
    provider = provider_type(_config(provider=provider_name, secrets={secret_name: secret}), client=client)

    with pytest.raises(RuntimeError) as raised:
        provider.complete(_request())

    assert secret not in str(raised.value)


def test_anthropic_adapter_reports_missing_optional_sdk(monkeypatch):
    real_import = __import__

    def without_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ModuleNotFoundError("anthropic not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", without_anthropic)
    with pytest.raises(RuntimeError, match="providers-anthropic"):
        AnthropicProvider(_config(secrets={"ANTHROPIC_API_KEY": "secret"}))


def test_gemini_adapter_reports_missing_optional_sdk(monkeypatch):
    real_import = __import__

    def without_google_genai(name, *args, **kwargs):
        if name == "google":
            raise ModuleNotFoundError("google-genai not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", without_google_genai)
    with pytest.raises(RuntimeError, match="providers-gemini"):
        GeminiProvider(_config(provider="gemini", secrets={"GEMINI_API_KEY": "secret"}))


@pytest.mark.parametrize(
    ("provider_type", "client", "secret_name"),
    [
        (AnthropicProvider, _AnthropicClient(response=SimpleNamespace(content=[])), "ANTHROPIC_API_KEY"),
        (GeminiProvider, _GeminiClient(response=SimpleNamespace(output_text=None)), "GEMINI_API_KEY"),
    ],
)
def test_native_provider_rejects_responses_without_text(provider_type, client, secret_name):
    provider_name = "gemini" if secret_name == "GEMINI_API_KEY" else "anthropic"
    provider = provider_type(
        _config(provider=provider_name, secrets={secret_name: "secret"}), client=client
    )

    with pytest.raises(RuntimeError, match="no text|invalid response"):
        provider.complete(_request())


def test_provider_factory_selects_native_provider(monkeypatch):
    import vestigia.providers as providers

    selected = object()
    monkeypatch.setattr(providers, "GeminiProvider", lambda config: selected)

    assert build_provider(_config(provider="gemini", secrets={"GEMINI_API_KEY": "secret"})) is selected


def test_load_config_keeps_native_keys_in_secret_boundary(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "home.yaml").write_text(
        "resident:\n  id: tester\nroom:\n  active_resident_ids: [tester]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    config = load_config(home)

    assert config.secret("ANTHROPIC_API_KEY") == "anthropic-secret"
    assert config.secret("GEMINI_API_KEY") == "google-secret"
    assert "google-secret" not in repr(config)


@pytest.mark.parametrize(
    ("provider", "key_name"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
def test_web_status_checks_the_configured_provider_key(tmp_path, monkeypatch, provider, key_name):
    home = initialize_home(tmp_path / provider, name="Provider resident")
    (home / "home.yaml").write_text(f"provider:\n  kind: {provider}\n", encoding="utf-8")
    env_file = tmp_path / f"{provider}.env"
    env_file.write_text(f"{key_name}=configured-secret\n", encoding="utf-8")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    status = _status(home, env_file)

    assert status["provider"] == provider
    assert status["credential_ready"] is True


@pytest.mark.parametrize(
    ("provider_config", "message"),
    [
        ("not-a-provider", "provider.kind"),
        ("gemini\n  max_output_tokens: 0", "max_output_tokens"),
    ],
)
def test_load_config_validates_provider_and_output_limit(tmp_path, provider_config, message):
    home = tmp_path / "home"
    home.mkdir()
    (home / "home.yaml").write_text(
        f"provider:\n  kind: {provider_config}\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=message):
        load_config(home)
