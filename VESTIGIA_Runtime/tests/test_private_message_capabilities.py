from __future__ import annotations

import pytest
from concurrent.futures import ThreadPoolExecutor

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.private_messages import PrivateMessageService
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


def prepared_service(tmp_path):
    home = initialize_home(tmp_path / "home", name="Resident", glyph="🏮")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    service = PrivateMessageService(db, str(config.get("resident.id")))
    return home, config, db, service


def grant_and_draft(service, *, turn_id="curation-1", provider_round=0):
    grant = service.create_grant(
        destination_kind="discord_channel",
        destination_id="123456789012345678",
        active_from="2026-10-18T00:00:00+00:00",
        expires_at="2026-11-18T00:00:00+00:00",
        message_quota=1,
    )
    service.grant_action(grant["id"], "accept", grant["terms_hash"])
    draft = service.draft(
        grant["id"], "Approved exact text", turn_id=turn_id, provider_round=provider_round
    )
    return grant, draft


def test_submit_requires_curation_and_a_later_provider_round(tmp_path):
    _, _, _, service = prepared_service(tmp_path)
    _, draft = grant_and_draft(service)

    with pytest.raises(PermissionError, match="private curation"):
        service.submit(
            draft["id"], draft["draft_hash"], turn_id="curation-1",
            provider_round=1, source_invocation="conversation",
        )
    with pytest.raises(PermissionError, match="later provider round"):
        service.submit(
            draft["id"], draft["draft_hash"], turn_id="curation-1",
            provider_round=0, source_invocation="private_curation",
        )
    with pytest.raises(PermissionError, match="same private turn"):
        service.submit(
            draft["id"], draft["draft_hash"], turn_id="different-turn",
            provider_round=1, source_invocation="private_curation",
        )

    with pytest.raises(ValueError, match="draft hash"):
        service.submit(
            draft["id"], "wrong-hash", turn_id="curation-1",
            provider_round=1, source_invocation="private_curation",
        )


def test_submit_atomically_reserves_one_use_and_is_idempotent(tmp_path):
    _, _, db, service = prepared_service(tmp_path)
    grant, draft = grant_and_draft(service)

    first = service.submit(
        draft["id"], draft["draft_hash"], turn_id="curation-1",
        provider_round=1, source_invocation="private_curation",
    )
    second = service.submit(
        draft["id"], draft["draft_hash"], turn_id="curation-1",
        provider_round=2, source_invocation="private_curation",
    )

    assert first["status"] == "queued"
    assert second["id"] == first["id"]
    assert second["reservation_id"] == first["reservation_id"]
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM private_message_outbox WHERE grant_id=?", (grant["id"],)
        ).fetchone()[0] == 1


def test_concurrent_submissions_cannot_exceed_grant_quota(tmp_path):
    _, _, db, service = prepared_service(tmp_path)
    grant = service.create_grant(
        destination_kind="discord_channel",
        destination_id="123456789012345678",
        active_from="2026-10-18T00:00:00+00:00",
        expires_at="2026-11-18T00:00:00+00:00",
        message_quota=1,
    )
    service.grant_action(grant["id"], "accept", grant["terms_hash"])
    drafts = [
        service.draft(grant["id"], f"Message {index}", turn_id="curation-1", provider_round=0)
        for index in range(2)
    ]

    def submit(draft):
        try:
            return service.submit(
                draft["id"], draft["draft_hash"], turn_id="curation-1",
                provider_round=1, source_invocation="private_curation",
            )["status"]
        except PermissionError:
            return "quota_exhausted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, drafts))

    assert outcomes.count("queued") == 1
    assert outcomes.count("quota_exhausted") == 1
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM private_message_outbox WHERE grant_id=?", (grant["id"],)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM private_message_quota_reservations WHERE grant_id=? AND state='reserved'",
            (grant["id"],),
        ).fetchone()[0] == 1


def test_house_capabilities_are_registered_and_reject_non_curation_submission(tmp_path):
    _, config, db, service = prepared_service(tmp_path)
    house = HousePort(config, db)
    grant, draft = grant_and_draft(service)

    for action in ("message.grant", "message.draft", "message.submit"):
        spec = house.registry.describe(action)[0]
        assert spec["registered"] and spec["schema_complete"] and spec["callable_now"]

    with pytest.raises(PermissionError, match="private curation"):
        house.dispatch(
            {"action": "message.submit", "draft_id": draft["id"], "draft_hash": draft["draft_hash"]},
            turn_id="curation-1",
            context={"interface": "discord", "invocation": "conversation", "house_tool_round": 1},
        )

    result = house.dispatch(
        {"action": "message.submit", "draft_id": draft["id"], "draft_hash": draft["draft_hash"]},
        turn_id="curation-1",
        context={"interface": "curation", "invocation": "private_curation", "house_tool_round": 1},
    )
    assert result["status"] == "queued"
    assert result["grant_id"] == grant["id"]


def test_runtime_forwards_current_provider_round_to_capability_dispatch(tmp_path):
    home, config, _, _ = prepared_service(tmp_path)
    action = '[[' + 'TOOL_ACTION {"action":"status","after":"continue"}' + ']]'
    provider = FakeProvider([action, "Finished."])
    runtime = CoreRuntime(config, provider=provider, fake=True)
    observed = []

    def capture_dispatch(payload, *, turn_id=None, context=None):
        observed.append(dict(context or {}))
        return {
            "ok": True,
            "action": payload["action"],
            "after": "continue",
            "receipt_id": "test-receipt",
        }

    runtime.house.dispatch = capture_dispatch
    runtime._complete_with_house_tools(
        turn_id="curation-1",
        model_route="default",
        messages=({"role": "user", "content": "private"},),
        metadata={"invocation": "private_curation", "interface": "curation"},
    )

    assert observed[0]["house_tool_round"] == 0
