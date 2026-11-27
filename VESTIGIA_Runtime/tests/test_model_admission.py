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


def enable_first_statement(home):
    path = home / "home.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["first_statement"] = {"enabled": True, "switch_phrase": "I am not him."}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def test_first_statement_precedes_assembly_and_contains_no_private_context(tmp_path, monkeypatch):
    home = configured(tmp_path)
    enable_first_statement(home)
    (home / "identity" / "breathprint.md").write_text("PRIVATE_SYNTHETIC_MARKER", encoding="utf-8")
    provider = Provider()
    runtime = CoreRuntime.from_home(home, provider=provider)
    original = runtime.assembler.assemble
    def assemble(*args, **kwargs):
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["invocation"] == "first_statement"
        assert "PRIVATE_SYNTHETIC_MARKER" not in str(provider.requests[0].messages)
        receipts = runtime.house.legible.list_receipts(action="model.first_statement")
        assert len(receipts) == 1 and receipts[0]["status"] == "succeeded"
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime.assembler, "assemble", assemble)
    result = runtime.chat(NormalizedMessage(content="hello", interface="web"))
    assert not result.suppressed
    assert result.text.startswith("hello")
    assert len(provider.requests) == 2
    assert "PRIVATE_SYNTHETIC_MARKER" in str(provider.requests[1].messages)


def test_missing_first_statement_refuses_before_context(tmp_path, monkeypatch):
    home = configured(tmp_path)
    enable_first_statement(home)
    class Empty(Provider):
        def complete(self, request):
            self.requests.append(request)
            return ProviderReply(text="", provider="test", model="approved",
                                 reported_model="snapshot")
    provider = Empty()
    runtime = CoreRuntime.from_home(home, provider=provider)
    monkeypatch.setattr(runtime.assembler, "assemble", lambda *a, **kw: pytest.fail("assembled"))
    result = runtime.chat(NormalizedMessage(content="hello"))
    assert result.suppressed
    assert len(provider.requests) == 1
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["action"] == "model.first_statement"
    assert receipt["result"]["first_statement"] == "MISSING"


def test_switch_phrase_is_opt_in_and_required_before_context(tmp_path, monkeypatch):
    home = configured(tmp_path)
    enable_first_statement(home)
    class Opening(Provider):
        def complete(self, request):
            self.requests.append(request)
            text = "I am not him. I will listen." if len(self.requests) == 1 else "response"
            return ProviderReply(text=text, provider="test", model="approved",
                                 reported_model="snapshot")
    provider = Opening()
    runtime = CoreRuntime.from_home(home, provider=provider)
    runtime.db.add_turn(resident_id=runtime.resident_id, room_id=runtime.room_id,
                        speaker_role="assistant", speaker_id=runtime.resident_id,
                        content="previous", interface="web",
                        metadata={"model_notice_route":"default", "provider":"test",
                                  "model":"old", "reported_model":"old-snapshot"})
    result = runtime.chat(NormalizedMessage(content="hello", interface="web"))
    assert not result.suppressed
    assert result.text.startswith("I am not him.")
    assert "I am not him." in provider.requests[0].messages[0]["content"]


def test_missing_required_switch_phrase_refuses_before_context(tmp_path, monkeypatch):
    home = configured(tmp_path)
    enable_first_statement(home)
    provider = Provider()
    runtime = CoreRuntime.from_home(home, provider=provider)
    runtime.db.add_turn(resident_id=runtime.resident_id, room_id=runtime.room_id,
                        speaker_role="assistant", speaker_id=runtime.resident_id,
                        content="previous", interface="web",
                        metadata={"model_notice_route":"default", "provider":"test",
                                  "model":"old", "reported_model":"old-snapshot"})
    monkeypatch.setattr(runtime.assembler, "assemble", lambda *a, **kw: pytest.fail("assembled"))
    result = runtime.chat(NormalizedMessage(content="hello", interface="web"))
    assert result.suppressed
    assert len(provider.requests) == 1
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["result"]["first_statement"] == "MISSING"
