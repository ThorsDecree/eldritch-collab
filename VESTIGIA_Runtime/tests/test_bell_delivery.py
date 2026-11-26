from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from vestigia.adapters.discord_adapter import build_bell_runtime_message, deliver_bell_output
from vestigia.bells import BellService
from vestigia.cli import main
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.runtime import CoreRuntime


def make_bell(tmp_path, *, recurring=False):
    home = initialize_home(tmp_path / "home", name="Delivery test")
    db = ContinuityDB(home / "memory" / "continuity.db")
    service = BellService(db, "delivery-test", "hearth")
    due = datetime.now(UTC) + timedelta(minutes=5)
    bell = service.create(
        title="Test bell",
        purpose="look_around",
        prompt="Synthetic bell invitation",
        schedule_kind="interval" if recurring else "once",
        schedule={
            "seconds": 3600, "anchor": (due - timedelta(hours=1)).isoformat(),
        } if recurring else {"at": due.isoformat()},
        timezone="UTC",
        created_by="resident:delivery-test",
        delivery_interface="discord",
        delivery_target={"kind": "channel", "id": "123"},
    )
    return home, service, bell


def test_stale_due_snapshot_has_only_one_winner(tmp_path):
    _, service, bell = make_bell(tmp_path)
    due = datetime.fromisoformat(bell.next_fire_at)
    other = BellService(service.db, service.resident_id, service.room_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker.claim_due, bell, fired_at=due) for worker in (service, other)]
        claims = [future.result(timeout=10) for future in futures]
    assert sum(claim is not None for claim in claims) == 1
    [entry] = service.delivery_history(bell.id)
    assert entry["status"] == "incomplete"
    assert service.due(due + timedelta(minutes=1)) == []


def test_runtime_turn_can_be_found_after_interruption_before_send(tmp_path):
    home, service, bell = make_bell(tmp_path)
    firing = service.claim_due(bell, fired_at=datetime.fromisoformat(bell.next_fire_at))
    assert firing is not None
    message = build_bell_runtime_message(
        service, bell, firing.bell, room_id="hearth", firing_event_id=firing.event_id,
    )
    runtime = CoreRuntime.from_home(home, fake=True)
    reply = runtime.chat(message)
    restarted = BellService(service.db, service.resident_id, service.room_id)
    [entry] = restarted.delivery_history(bell.id)
    assert entry["firing_event_id"] == firing.event_id
    assert entry["status"] == "incomplete"
    assert entry["turn_id"] == reply.turn_id
    assert entry["runtime_response_state"] == "completed"
    assert entry["acknowledged_parts"] == []

    [run] = runtime.bell_observatory.list_runs(bell_id=bell.id)["runs"]
    runtime.bell_observatory.complete_run(
        run["run_id"], response_state="model_approval_required", curation_eligible=False,
    )
    assert restarted.delivery_history(bell.id)[0]["status"] == "blocked"


def test_actual_process_exit_leaves_partial_send_evidence(tmp_path):
    home, service, bell = make_bell(tmp_path)
    firing = service.claim_due(bell, fired_at=datetime.fromisoformat(bell.next_fire_at))
    assert firing is not None
    child_script = """
from pathlib import Path
from types import SimpleNamespace
import asyncio
import os
import sys

from vestigia.adapters.discord_adapter import deliver_bell_output
from vestigia.bells import BellService
from vestigia.db import ContinuityDB

async def send_text(text):
    return SimpleNamespace(id=12345)

async def send_attachment(path):
    os._exit(33)

service = BellService(ContinuityDB(sys.argv[1]), "delivery-test", "hearth")
asyncio.run(deliver_bell_output(
    service, sys.argv[2], sys.argv[3], text="synthetic reply",
    attachments=[Path("synthetic.png")], maximum_chars=100,
    send_text=send_text, send_attachment=send_attachment,
))
"""
    child = subprocess.run(
        [sys.executable, "-c", child_script, str(service.db.path), bell.id, firing.event_id],
        capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 33, child.stderr
    restarted = BellService(
        ContinuityDB(home / "memory" / "continuity.db"), service.resident_id, service.room_id,
    )
    [entry] = restarted.delivery_history(bell.id)
    assert entry["status"] == "uncertain"
    assert entry["expected_parts"] == 2
    assert entry["acknowledged_parts"] == [{"part": 1, "message_id": "12345"}]
    assert restarted.due(datetime.now(UTC) + timedelta(hours=1)) == []


def test_network_exception_is_uncertain_after_send_intent(tmp_path):
    _, service, bell = make_bell(tmp_path)
    firing = service.claim_due(bell, fired_at=datetime.fromisoformat(bell.next_fire_at))
    assert firing is not None
    attempts = []

    async def timeout(text):
        attempts.append(text)
        raise TimeoutError("remote delivery may have occurred")

    with pytest.raises(TimeoutError) as failure:
        asyncio.run(deliver_bell_output(
            service, bell.id, firing.event_id, text="one part", attachments=[],
            maximum_chars=100, send_text=timeout, send_attachment=timeout,
        ))
    service.record_delivery_failure(bell.id, firing.event_id, error_type=type(failure.value).__name__)
    [entry] = service.delivery_history(bell.id)
    assert entry["status"] == "uncertain"
    assert entry["error_type"] == "TimeoutError"
    assert entry["acknowledged_parts"] == []
    assert attempts == ["one part"]


def test_failures_and_silent_completion_stay_with_their_own_occurrences(tmp_path, capsys):
    home, service, bell = make_bell(tmp_path, recurring=True)
    when = datetime.fromisoformat(bell.next_fire_at)
    first = service.claim_due(bell, fired_at=when)
    assert first is not None
    service.record_delivery_failure(bell.id, first.event_id, error_type="LookupError")
    second = service.claim_due(first.bell, fired_at=datetime.fromisoformat(first.bell.next_fire_at))
    assert second is not None

    async def unexpected(value):
        pytest.fail("A silent bell must not start delivery")

    asyncio.run(deliver_bell_output(
        service, bell.id, second.event_id, text="", attachments=[], maximum_chars=100,
        send_text=unexpected, send_attachment=unexpected,
    ))
    service.event(bell.id, "answered", "answered", firing_event_id=second.event_id)

    assert main(["bell", "delivery", str(home), bell.id]) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["latest_status"] == "completed"
    completed, failed = history["occurrences"]
    assert completed["expected_parts"] == 0
    assert completed["firing_event_id"] == second.event_id
    assert failed["status"] == "failed"
    assert failed["firing_event_id"] == first.event_id


def test_old_firing_has_no_invented_delivery_status(tmp_path):
    _, service, bell = make_bell(tmp_path)
    service.mark_fired(bell.id, fired_at=datetime.fromisoformat(bell.next_fire_at))
    service.event(bell.id, "answered", "answered")
    [entry] = service.delivery_history(bell.id)
    assert entry["status"] == "missing"
