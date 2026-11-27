from __future__ import annotations

import json

import pytest

from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage, ProviderReply
from vestigia.runtime import CoreRuntime


class FixedProvider:
    def __init__(self, model, reported_model=None):
        self.reply = ProviderReply(
            text="A resident reply.",
            provider="openai",
            model=model,
            reported_model=reported_model,
        )

    def complete(self, request):
        return self.reply


def chat(runtime, *, route="default", interface="cli"):
    return runtime.chat(
        NormalizedMessage(content="Hello", interface=interface), model_route=route
    )


def test_reported_model_change_is_visible_and_recorded_across_restart(tmp_path):
    home = initialize_home(tmp_path / "home", name="Notice test")
    before = CoreRuntime.from_home(home, provider=FixedProvider("alias", "snapshot-a"))
    first = chat(before)
    assert first.model_change_notice is None

    after = CoreRuntime.from_home(home, provider=FixedProvider("alias", "snapshot-b"))
    reply = chat(after, interface="discord")

    assert reply.model_change_notice == (
        "⚠️ Model switched (default route) · reported model: snapshot-a → snapshot-b"
    )
    assert reply.text == "A resident reply."
    path = home / "traces" / f"{reply.turn_id}.result.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    change = receipt["model_change"]
    previous = after.db.get_turn(change["previous_assistant_turn_id"])
    assert previous["parent_turn_id"] == first.turn_id
    assert change["changed"] == ["reported_model"]
    stored = after.db.get_turn(receipt["assistant_turn_id"])
    assert stored["metadata"]["model_change"] == change
    assert stored["content"] == reply.text
    assert chat(after).model_change_notice is None


def test_routes_are_compared_independently_and_unknown_reports_are_not_changes(tmp_path):
    home = initialize_home(tmp_path / "home", name="Notice test")
    default = CoreRuntime.from_home(home, provider=FixedProvider("default-model", "snapshot-a"))
    big = CoreRuntime.from_home(home, provider=FixedProvider("big-model"))
    assert chat(default).model_change_notice is None
    assert chat(big, route="big").model_change_notice is None
    assert chat(default).model_change_notice is None

    no_report = CoreRuntime.from_home(home, provider=FixedProvider("default-model"))
    assert chat(no_report).model_change_notice is None
    changed = CoreRuntime.from_home(home, provider=FixedProvider("new-big-model"))
    assert chat(changed, route="big").model_change_notice == (
        "⚠️ Model switched (big route) · model ID: big-model → new-big-model"
    )


def test_background_reply_leaves_the_conversation_baseline_available(tmp_path):
    home = initialize_home(tmp_path / "home", name="Notice test")
    before = CoreRuntime.from_home(home, provider=FixedProvider("old"))
    chat(before)

    after = CoreRuntime.from_home(home, provider=FixedProvider("new"))
    assert chat(after, interface="bell").model_change_notice is None
    assert chat(after).model_change_notice == (
        "⚠️ Model switched (default route) · model ID: old → new"
    )


def test_web_shows_notice_on_reply_and_after_reload(tmp_path, monkeypatch):
    pytest.importorskip("python_multipart")
    testclient = pytest.importorskip("fastapi.testclient")
    from vestigia.web_app import create_app

    home = initialize_home(tmp_path / "home", name="Notice test")
    before = CoreRuntime.from_home(home, provider=FixedProvider("old"))
    chat(before)
    after = CoreRuntime.from_home(home, provider=FixedProvider("new"))
    monkeypatch.setattr(CoreRuntime, "from_home", lambda *args, **kwargs: after)
    app = create_app(initial_home=home)

    with testclient.TestClient(app) as client:
        response = client.post(
            "/api/talk",
            data={"message": "Hello", "home": str(home), "csrf_token": app.state.csrf_token},
        )
        assert response.status_code == 200
        payload = response.json()
        notice = "⚠️ Model switched (default route) · model ID: old → new"
        assert payload["text"] == "A resident reply."
        assert payload["model_change_notice"] == notice
        page = client.get("/talk")
        assert page.status_code == 200
        assert f'<p class="notice" role="status">{notice}</p>' in page.text
