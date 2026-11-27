import pytest
import yaml

from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage, ProviderReply, ProviderRequest
from vestigia.model_approval import ModelApprovalRequired
from vestigia.runtime import CoreRuntime


class Provider:
    def __init__(self, model="approved", reported="snapshot"):
        self.model, self.reported, self.requests = model, reported, []

    def admission_identity(self, route):
        return {"provider": "test", "model": self.model}

    def complete(self, request):
        self.requests.append(request)
        return ProviderReply(text="hello", provider="test", model=self.model,
                             reported_model=self.reported)


def configured(tmp_path):
    home = initialize_home(tmp_path / "home", name="Synthetic")
    path = home / "home.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["model_approval"] = {
        "enabled": True, "pre_dispatch": True,
        "routes": {"default": {"provider": "test", "model": "approved",
                               "reported_model": "snapshot"}},
    }
    config["curation"]["enabled"] = False
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return home


def test_mismatch_stops_before_context_and_provider(tmp_path, monkeypatch):
    home = configured(tmp_path)
    provider = Provider(model="unapproved")
    runtime = CoreRuntime.from_home(home, provider=provider)
    monkeypatch.setattr(runtime.assembler, "assemble",
                        lambda *a, **k: pytest.fail("private context assembled"))
    result = runtime.chat(NormalizedMessage(content="synthetic input"))
    assert result.suppressed and result.model_approval_receipt_id
    assert not provider.requests
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["action"] == "model.admission"
    assert receipt["status"] == "refused"
    assert receipt["result"]["phase"] == "before_context"
    assert receipt["result"]["reason"] == "request_identity_mismatch"
    assert not list((home / "traces" / "provider-calls").glob("*.json"))


def test_approved_request_and_post_check(tmp_path):
    home = configured(tmp_path)
    provider = Provider()
    runtime = CoreRuntime.from_home(home, provider=provider)
    result = runtime.chat(NormalizedMessage(content="synthetic input"))
    assert not result.suppressed
    assert len(provider.requests) == 1
    assert list((home / "traces" / "provider-calls").glob("*.json"))


def test_each_provider_call_is_guarded(tmp_path):
    home = configured(tmp_path)
    provider = Provider(model="unapproved")
    runtime = CoreRuntime.from_home(home, provider=provider)
    request = ProviderRequest(turn_id="synthetic", model_route="default",
                              messages=({"role": "user", "content": "private"},),
                              metadata={"invocation": "memory_experiment"})
    with pytest.raises(ModelApprovalRequired):
        runtime.provider.complete(request)
    assert not provider.requests


def test_unknown_adapter_fails_closed(tmp_path):
    home = configured(tmp_path)

    class Unknown:
        def complete(self, request):
            pytest.fail("unknown provider must not be called")

    runtime = CoreRuntime.from_home(home, provider=Unknown())
    result = runtime.chat(NormalizedMessage(content="synthetic input"))
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["result"]["reason"] == "request_identity_unknown"


def test_reported_identity_still_checked_after_admission(tmp_path):
    home = configured(tmp_path)
    provider = Provider(reported="unexpected")
    runtime = CoreRuntime.from_home(home, provider=provider)
    result = runtime.chat(NormalizedMessage(content="synthetic input"))
    assert len(provider.requests) == 1
    assert result.suppressed
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["action"] == "model.approval_gate"
    assert receipt["status"] == "refused"
