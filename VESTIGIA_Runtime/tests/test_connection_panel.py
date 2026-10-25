from __future__ import annotations

import json

import pytest

from vestigia.config import load_config
from vestigia.context import ContextAssembler
from vestigia.context_sources import ContextSourceItem, ContextSourceRequest, ContextSourceResult
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.models import NormalizedMessage, RuntimeState
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime
from vestigia.utils import sha256_text


class CountingSource:
    name = "fixture_archive"
    required = False

    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, request: ContextSourceRequest) -> ContextSourceResult:
        self.calls += 1
        text = "A fixture archive fact."
        return ContextSourceResult(
            source_name=self.name,
            layer_name="fixture_archive_context",
            query=request.query,
            items=(ContextSourceItem(
                item_id="fixture:1",
                text=text,
                provenance_class="fixture",
                authority="fixture_archive",
                content_hash=sha256_text(text),
                source_ref="fixture.md",
            ),),
            budget_tokens=200,
            required=False,
            authority="fixture_archive",
        )


def _house(home_path):
    initialize_home(home_path, name="Panel Resident", glyph="🏮")
    config = load_config(home_path)
    db = ContinuityDB(home_path / "memory" / "continuity.db")
    db.initialize()
    return config, db, HousePort(config, db)


def _dispatch(house: HousePort, payload: dict):
    result, _, _ = house.registry.dispatch(payload)
    return result


def test_panel_reports_provider_interfaces_sources_and_work_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-secret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret-value")
    initialize_home(tmp_path / "home", name="Panel Resident", glyph="🏮")
    runtime = CoreRuntime.from_home(tmp_path / "home", provider=FakeProvider())

    panel = _dispatch(runtime.house, {"action": "connection.panel"})
    encoded = json.dumps(panel)

    assert panel["provider"]["configured_kind"] == "openai"
    assert panel["provider"]["adapter"] == "FakeProvider"
    assert panel["provider"]["reachability"] == "not_reported"
    assert panel["provider"]["routes"]["default"]
    assert panel["runtime"]["lifecycle_state"] == RuntimeState.ORIENTATION.value
    assert panel["resident_preferences"]["private_image_mode"]
    assert panel["credentials"]["openai"] == "present"
    assert panel["credentials"]["discord_bot"] == "present"
    assert panel["interfaces"]["discord"]["enabled"] is False
    assert panel["context_sources"]["attached"] is True
    assert panel["context_sources"]["sources"][0]["name"] == "runtime_memory"
    assert panel["pending_work"]["total"] == 0
    assert "discord-secret-value" not in encoded
    assert "provider-secret-value" not in encoded


def test_panel_reports_absent_credentials_without_claiming_reachability(tmp_path, monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DISCORD_BOT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    initialize_home(tmp_path / "home", name="Panel Resident", glyph="🏮")
    runtime = CoreRuntime.from_home(tmp_path / "home", provider=FakeProvider())

    panel = _dispatch(runtime.house, {"action": "connection.panel"})

    assert panel["credentials"] == {
        "openai": "absent",
        "anthropic": "absent",
        "gemini": "absent",
        "discord_bot": "absent",
    }
    assert panel["provider"]["reachability"] == "not_reported"


def test_resident_can_disable_and_reenable_only_optional_attached_source_for_future_turns(tmp_path):
    config, db, house = _house(tmp_path / "home")
    source = CountingSource()
    assembler = ContextAssembler(config, db, additional_sources=[source])
    house.bind_runtime_connections(provider=FakeProvider(), context_sources=assembler.context_sources)

    panel = _dispatch(house, {"action": "connection.panel"})
    archive = next(item for item in panel["context_sources"]["sources"] if item["name"] == source.name)
    assert archive["enabled_for_future_context"] is True

    result = _dispatch(house, {"action": "connection.control", "mode": "disable", "source": source.name})
    assert result["enabled_for_future_context"] is False
    assembler.assemble(NormalizedMessage(content="hello"), state=RuntimeState.ACTIVE.value)
    assert source.calls == 0

    _dispatch(house, {"action": "connection.control", "mode": "enable", "source": source.name})
    assembler.assemble(NormalizedMessage(content="hello"), state=RuntimeState.ACTIVE.value)
    assert source.calls == 1


def test_required_and_unattached_sources_cannot_be_changed(tmp_path):
    config, db, house = _house(tmp_path / "home")
    assembler = ContextAssembler(config, db)
    house.bind_runtime_connections(provider=FakeProvider(), context_sources=assembler.context_sources)
    with pytest.raises(ValueError, match="required"):
        _dispatch(house, {"action": "connection.control", "mode": "disable", "source": "runtime_memory"})
    with pytest.raises(KeyError, match="attached"):
        _dispatch(house, {"action": "connection.control", "mode": "disable", "source": "missing_source"})


def test_pending_work_counts_only_resident_tasks_not_control_or_service_rows(tmp_path):
    config, db, house = _house(tmp_path / "home")
    with db.connect() as connection:
        for kind in ("house_index", "curation", "attention_router_controls", "sensory_controls"):
            connection.execute(
                "INSERT INTO resident_jobs (id,resident_id,kind,status,config_json,updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(resident_id,kind) DO UPDATE SET status=excluded.status",
                (kind, house.resident_id, kind, "active", "{}", "2026-01-01T00:00:00+00:00"),
            )
        connection.execute(
            "INSERT INTO resident_jobs (id,resident_id,kind,status,config_json,updated_at) VALUES (?,?,?,?,?,?)",
            ("task1", house.resident_id, "resident_task:task1", "active", "{}", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO resident_jobs (id,resident_id,kind,status,config_json,updated_at) VALUES (?,?,?,?,?,?)",
            (
                "expired-task",
                house.resident_id,
                "resident_task:expired-task",
                "active",
                '{"expires_at":"2020-01-01T00:00:00+00:00"}',
                "2026-01-01T00:00:00+00:00",
            ),
        )

    panel = _dispatch(house, {"action": "connection.panel"})
    assert panel["pending_work"] == {"active": 1, "queued": 0, "paused": 0, "total": 1}
    with db.connect() as connection:
        status = connection.execute(
            "SELECT status FROM resident_jobs WHERE id='expired-task'"
        ).fetchone()["status"]
    assert status == "active"
