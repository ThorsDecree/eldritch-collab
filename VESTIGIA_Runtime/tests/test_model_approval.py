from __future__ import annotations

import json
from collections import deque

import pytest
import yaml

from vestigia.cli import main
from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage, ProviderReply
from vestigia.runtime import CoreRuntime


class ScriptedProvider:
    def __init__(self, *replies: ProviderReply) -> None:
        self.replies = deque(replies)
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        return self.replies.popleft()


def response(text, reported="snapshot-1", *, model="alias", provider="openai"):
    return ProviderReply(text=text, provider=provider, model=model, reported_model=reported)


def write_action(path):
    payload = {"action": "file.write", "path": f"workspace/{path}", "content": "test write"}
    return f"[[TOOL_ACTION {json.dumps(payload)}]]"


def configure(home, *, route="default", reported="snapshot-1", pin=True, curation=False):
    config_path = home / "home.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["model_approval"] = {
        "enabled": True,
        "routes": (
            {route: {"provider": "openai", "model": "alias", "reported_model": reported}}
            if pin else {}
        ),
    }
    config["curation"]["enabled"] = curation
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")


def gate_result(runtime, result):
    assert result.suppressed
    assert result.model_approval_receipt_id is not None
    receipt = runtime.house.legible.inspect_receipt(result.model_approval_receipt_id)
    assert receipt["action"] == "model.approval_gate"
    assert receipt["status"] == "refused"
    assert receipt["turn_id"] == result.turn_id
    return receipt["result"]


@pytest.mark.parametrize(
    ("model", "reported", "provider"),
    [("alias", "snapshot-2", "openai"), ("alias", None, "openai"),
     ("new-alias", "snapshot-1", "openai"), ("alias", "snapshot-1", "openai-compatible")],
)
def test_mismatched_identity_pauses_before_dispatch(tmp_path, model, reported, provider):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home)
    runtime = CoreRuntime.from_home(
        home, provider=ScriptedProvider(response(
            write_action("blocked.md"), reported, model=model, provider=provider,
        ))
    )
    result = runtime.chat(NormalizedMessage(content="hello"))

    evidence = gate_result(runtime, result)
    assert evidence["observed"] == {"provider": provider, "model": model, "reported_model": reported}
    assert evidence["reason"] == "identity_mismatch"
    assert not (home / "workspace" / "blocked.md").exists()
    trace = home / "traces" / f"{result.turn_id}.result.json"
    assert json.loads(trace.read_text(encoding="utf-8"))["model_approval"] == evidence
    assert "test write" not in trace.read_text(encoding="utf-8")


def test_missing_pin_blocks_final_controls_and_preserves_operator_reading(
    tmp_path, monkeypatch, capsys,
):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home, pin=False)
    provider = ScriptedProvider(response("private draft\n[[BELL_CONTROL {}]]"))
    runtime = CoreRuntime.from_home(home, provider=provider)
    local = home / "workspace" / "prior.md"
    local.write_text("already available", encoding="utf-8")

    def unexpected_controls(*args, **kwargs):
        pytest.fail("Unapproved reply entered final-response control processing")

    monkeypatch.setattr(runtime.house, "apply_resident_controls", unexpected_controls)
    monkeypatch.setattr(runtime.curator, "apply_resident_controls", unexpected_controls)
    result = runtime.chat(NormalizedMessage(content="hello", interface="discord"))
    assert gate_result(runtime, result)["reason"] == "route_not_approved"
    assert provider.calls == 1
    assert "private draft" not in result.text
    assert main(["house-read", str(home), "read", "--path", "workspace/prior.md"]) == 0
    assert "already available" in capsys.readouterr().out
    turn = runtime.db.recent_turns(runtime.resident_id, runtime.room_id, 1)[0]
    assert turn["speaker_role"] == "system"
    assert turn["parent_turn_id"] == result.turn_id


def test_every_private_round_is_checked_and_an_approved_edit_is_not_replayed(tmp_path):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home)
    provider = ScriptedProvider(
        response(write_action("approved.md")),
        response(write_action("blocked.md"), "snapshot-2"),
    )
    runtime = CoreRuntime.from_home(home, provider=provider)
    result = runtime.chat(NormalizedMessage(content="hello"))

    evidence = gate_result(runtime, result)
    assert evidence["call_index"] == 1
    assert evidence["earlier_tool_attempts"] == 1
    assert (home / "workspace" / "approved.md").is_file()
    assert not (home / "workspace" / "blocked.md").exists()
    assert provider.calls == 2

    configure(home, reported="snapshot-2")
    restarted = CoreRuntime.from_home(
        home, provider=ScriptedProvider(response("new turn", "snapshot-2"))
    )
    next_result = restarted.chat(NormalizedMessage(content="A different turn"))
    assert next_result.text == "new turn"
    assert not (home / "workspace" / "blocked.md").exists()


def test_an_explicit_null_pin_allows_no_report_only_on_its_selected_route(tmp_path):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home, route="big", reported=None)
    runtime = CoreRuntime.from_home(
        home, provider=ScriptedProvider(response("blocked", None), response("allowed", None))
    )
    assert gate_result(runtime, runtime.chat(NormalizedMessage(content="default")))["route"] == "default"
    assert runtime.chat(NormalizedMessage(content="alternate"), model_route="big").text == "allowed"


def test_bell_observatory_records_block_without_assistant_reply(tmp_path):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home)
    runtime = CoreRuntime.from_home(home, provider=ScriptedProvider(response("unapproved", "new")))
    result = runtime.chat(NormalizedMessage(
        content="test bell", interface="bell", metadata={"bell_id": "test-bell"},
    ))
    gate_result(runtime, result)
    assert runtime.bell_observatory.list_runs(bell_id="test-bell")["runs"][0]["response_state"] == (
        "model_approval_required"
    )
    assert runtime.db.recent_turn_count(runtime.resident_id, runtime.room_id) == 1


def test_gate_is_disabled_for_existing_homes_by_default(tmp_path):
    home = initialize_home(tmp_path / "home", name="Approval test")
    runtime = CoreRuntime.from_home(home, provider=ScriptedProvider(response("ordinary reply")))
    result = runtime.chat(NormalizedMessage(content="hello"))
    assert result.text == "ordinary reply"
    assert not result.suppressed
    assert not runtime.house.legible.list_receipts(action="model.approval_gate")


def test_private_curation_refusal_preserves_the_approved_reply(tmp_path):
    home = initialize_home(tmp_path / "home", name="Approval test")
    configure(home, curation=True)
    provider = ScriptedProvider(
        response("First"), response("Second"), response("Third"),
        response(write_action("blocked-curation.md"), "snapshot-2"),
    )
    runtime = CoreRuntime.from_home(home, provider=provider)
    for prompt in ("one", "two", "three"):
        result = runtime.chat(NormalizedMessage(content=prompt))

    assert result.text == "Third"
    assert not (home / "workspace" / "blocked-curation.md").exists()
    with runtime.db.connect() as connection:
        batch = connection.execute("SELECT status FROM curation_batches").fetchone()
    assert batch["status"] == "model_approval_required"
    receipts = runtime.house.legible.list_receipts(action="model.approval_gate", status="refused")
    assert len(receipts) == 1
    receipt = runtime.house.legible.inspect_receipt(receipts[0]["id"])
    assert receipt["result"]["invocation"] == "private_curation"
