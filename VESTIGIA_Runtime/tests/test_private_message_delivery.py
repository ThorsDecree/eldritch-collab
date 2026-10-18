from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from vestigia.adapters.discord_adapter import deliver_private_message_once
from vestigia.db import ContinuityDB
from vestigia.private_messages import PrivateMessageService


def queued_message(tmp_path, *, quota=3, body="Exact approved text"):
    db = ContinuityDB(tmp_path / "continuity.db")
    db.initialize()
    service = PrivateMessageService(db, "resident-1")
    grant = service.create_grant(
        destination_kind="discord_channel",
        destination_id="123456789012345678",
        active_from="2026-10-18T00:00:00+00:00",
        expires_at="2026-11-18T00:00:00+00:00",
        message_quota=quota,
    )
    service.grant_action(grant["id"], "accept", grant["terms_hash"])
    draft = service.draft(grant["id"], body, turn_id="curation-1", provider_round=0)
    outbox = service.submit(
        draft["id"], draft["draft_hash"], turn_id="curation-1",
        provider_round=1, source_invocation="private_curation",
    )
    return db, service, grant, draft, outbox


def reservation_state(db, outbox_id):
    with db.connect() as connection:
        row = connection.execute(
            "SELECT state FROM private_message_quota_reservations WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()
    return row["state"]


def outbox_row(db, outbox_id):
    with db.connect() as connection:
        return dict(connection.execute(
            "SELECT * FROM private_message_outbox WHERE id=?", (outbox_id,)
        ).fetchone())


def test_cancel_before_claim_prevents_send_and_releases_quota(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    result = service.cancel(outbox["id"], actor="operator")

    assert result["status"] == "cancelled"
    assert reservation_state(db, outbox["id"]) == "released"
    row = outbox_row(db, outbox["id"])
    assert row["body"] is None
    assert service.peek_next() is None


def test_revocation_cancels_queued_messages_and_releases_reservations(tmp_path):
    db, service, grant, _, outbox = queued_message(tmp_path)

    result = service.revoke_grant(grant["id"], actor="operator")

    assert result["state"] == "revoked"
    assert outbox_row(db, outbox["id"])["status"] == "cancelled"
    assert reservation_state(db, outbox["id"]) == "released"
    assert service.peek_next() is None


def test_claim_winning_cancellation_race_returns_too_late(tmp_path):
    db, service, _, draft, outbox = queued_message(tmp_path)

    claimed = service.claim(outbox["id"])
    cancellation = service.cancel(outbox["id"], actor="operator")

    assert claimed["status"] == "attempting"
    assert cancellation["status"] == "too_late_to_cancel"
    delivered = service.finish_delivery(
        outbox["id"], "delivered", discord_message_id="987654321", 
    )
    assert delivered["status"] == "delivered"
    assert reservation_state(db, outbox["id"]) == "consumed"
    assert outbox_row(db, outbox["id"])["body"] is None
    with db.connect() as connection:
        saved_draft = connection.execute(
            "SELECT body FROM private_message_drafts WHERE id=?", (draft["id"],)
        ).fetchone()
    assert saved_draft["body"] == ""


def test_cancel_and_claim_race_has_one_serialized_winner(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_future = pool.submit(service.claim, outbox["id"])
        cancel_future = pool.submit(service.cancel, outbox["id"], actor="operator")
        claimed = claim_future.result()
        cancelled = cancel_future.result()

    if cancelled["status"] == "cancelled":
        assert claimed is None
        assert reservation_state(db, outbox["id"]) == "released"
        assert outbox_row(db, outbox["id"])["body"] is None
    else:
        assert cancelled["status"] == "too_late_to_cancel"
        assert claimed["status"] == "attempting"


def test_expired_queued_grant_is_cancelled_before_claim(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    claimed = service.claim(outbox["id"], now="2026-12-01T00:00:00+00:00")

    assert claimed is None
    assert outbox_row(db, outbox["id"])["status"] == "cancelled"
    assert reservation_state(db, outbox["id"]) == "released"


def test_definite_failure_refunds_but_uncertain_attempt_consumes_quota(tmp_path):
    db, service, _, _, failed = queued_message(tmp_path / "failed", quota=1)
    service.claim(failed["id"])
    service.finish_delivery(failed["id"], "failed", safe_error_code="discord_rejected_400")
    assert reservation_state(db, failed["id"]) == "released"

    _, service2, grant2, _, uncertain = queued_message(tmp_path / "uncertain", quota=1)
    service2.claim(uncertain["id"])
    service2.finish_delivery(uncertain["id"], "delivery_uncertain", safe_error_code="network_timeout")
    assert reservation_state(service2.db, uncertain["id"]) == "consumed"

    draft = service2.draft(
        grant2["id"], "Another exact message", turn_id="curation-2", provider_round=0
    )
    with pytest.raises(PermissionError, match="quota is exhausted"):
        service2.submit(
            draft["id"], draft["draft_hash"], turn_id="curation-2",
            provider_round=1, source_invocation="private_curation",
        )


def test_stale_attempt_recovery_marks_uncertain_and_never_requeues(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)
    service.claim(outbox["id"])

    assert service.recover_attempting() == 1
    assert outbox_row(db, outbox["id"])["status"] == "delivery_uncertain"
    assert reservation_state(db, outbox["id"]) == "consumed"
    assert service.peek_next() is None
    assert service.recover_attempting() == 0


class FakeChannel:
    guild = object()

    def __init__(self):
        self.calls = []

    async def send(self, content, **kwargs):
        self.calls.append((content, kwargs))
        return type("Sent", (), {"id": 987654321})()


class FakeDiscordClient:
    def __init__(self, channel=None):
        self.channel = channel

    def get_channel(self, _channel_id):
        return self.channel

    async def fetch_channel(self, _channel_id):
        if self.channel is None:
            raise LookupError("channel unavailable")
        return self.channel


def test_worker_sends_exact_plain_text_to_grant_destination_with_mentions_disabled(tmp_path):
    _, service, _, _, outbox = queued_message(tmp_path, body="Line one\nLine two https://example.test")
    channel = FakeChannel()
    allowed_mentions = object()

    result = asyncio.run(deliver_private_message_once(
        service,
        FakeDiscordClient(channel),
        allowed_mentions=allowed_mentions,
    ))

    assert result["status"] == "delivered"
    assert len(channel.calls) == 1
    text, kwargs = channel.calls[0]
    assert text == "Line one\nLine two https://example.test"
    assert kwargs == {"allowed_mentions": allowed_mentions, "suppress_embeds": True}
    assert outbox_row(service.db, outbox["id"])["discord_message_id"] == "987654321"


def test_unavailable_destination_stays_queued_and_does_not_claim(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    result = asyncio.run(deliver_private_message_once(
        service, FakeDiscordClient(), allowed_mentions=object()
    ))

    assert result["status"] == "queued"
    assert outbox_row(db, outbox["id"])["status"] == "queued"
    assert reservation_state(db, outbox["id"]) == "reserved"


def test_timeout_is_uncertain_and_worker_never_retries(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    class TimeoutChannel(FakeChannel):
        async def send(self, content, **kwargs):
            self.calls.append((content, kwargs))
            raise TimeoutError("ambiguous timeout")

    channel = TimeoutChannel()
    client = FakeDiscordClient(channel)
    first = asyncio.run(deliver_private_message_once(service, client, allowed_mentions=object()))
    second = asyncio.run(deliver_private_message_once(service, client, allowed_mentions=object()))

    assert first["status"] == "delivery_uncertain"
    assert second is None
    assert len(channel.calls) == 1
    assert outbox_row(db, outbox["id"])["status"] == "delivery_uncertain"
    assert reservation_state(db, outbox["id"]) == "consumed"


def test_explicit_discord_rejection_is_definite_failure(tmp_path):
    db, service, _, _, outbox = queued_message(tmp_path)

    class RejectedChannel(FakeChannel):
        async def send(self, content, **kwargs):
            self.calls.append((content, kwargs))
            error = RuntimeError("platform rejected request")
            error.status = 403
            raise error

    result = asyncio.run(deliver_private_message_once(
        service, FakeDiscordClient(RejectedChannel()), allowed_mentions=object()
    ))

    assert result["status"] == "failed"
    assert outbox_row(db, outbox["id"])["body"] is None
    assert reservation_state(db, outbox["id"]) == "released"
