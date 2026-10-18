from __future__ import annotations

import pytest

from vestigia.db import ContinuityDB
from vestigia.private_messages import PrivateMessageService


@pytest.fixture
def service(tmp_path):
    db = ContinuityDB(tmp_path / "continuity.db")
    db.initialize()
    return PrivateMessageService(db, "resident-1")


def create_grant(service, **overrides):
    values = {
        "destination_kind": "discord_channel",
        "destination_id": "123456789012345678",
        "active_from": "2026-10-18T00:00:00+00:00",
        "expires_at": "2026-11-18T00:00:00+00:00",
        "message_quota": 3,
    }
    values.update(overrides)
    return service.create_grant(**values)


def test_grant_starts_pending_and_accepts_only_its_exact_terms_hash(service):
    grant = create_grant(service)

    assert grant["state"] == "pending_resident_acceptance"
    assert grant["terms_hash"]
    assert grant["terms"]["destination_id"] == "123456789012345678"

    with pytest.raises(ValueError, match="terms hash"):
        service.grant_action(grant["id"], "accept", "stale-hash")

    accepted = service.grant_action(grant["id"], "accept", grant["terms_hash"])
    assert accepted["state"] == "active"


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination_kind": "discord_server"},
        {"destination_id": ""},
        {"active_from": "2026-10-18T00:00:00"},
        {"expires_at": "2026-10-17T00:00:00+00:00"},
        {"message_quota": 0},
        {"max_length": 1801},
        {"max_length": 0},
    ],
)
def test_grant_rejects_unbounded_or_malformed_terms(service, overrides):
    with pytest.raises((TypeError, ValueError)):
        create_grant(service, **overrides)


def test_declined_grant_is_unusable_and_resident_relinquishment_is_terminal(service):
    declined = create_grant(service)
    result = service.grant_action(declined["id"], "decline")
    assert result["state"] == "relinquished"

    relinquished = create_grant(service)
    service.grant_action(relinquished["id"], "accept", relinquished["terms_hash"])
    result = service.grant_action(relinquished["id"], "relinquish")
    assert result["state"] == "relinquished"


def test_draft_uses_grant_bound_destination_and_counts_utf16_code_units(service):
    grant = create_grant(service)
    service.grant_action(grant["id"], "accept", grant["terms_hash"])
    body = "a" * 899 + "😀" + "b" * 899

    draft = service.draft(grant["id"], body, turn_id="turn-1", provider_round=1)

    assert draft["length_utf16"] == 1800
    assert draft["destination_id"] == "123456789012345678"
    assert draft["draft_hash"]
    assert "body" not in draft

    with pytest.raises(ValueError, match="1800"):
        service.draft(grant["id"], body + "x", turn_id="turn-1", provider_round=1)


@pytest.mark.parametrize(
    "body",
    ["", "   ", "ping <@123456789>", "ping <@&123>", "@everyone", "@here", "bad\x00text", "bad\ud800text"],
)
def test_draft_rejects_empty_mentions_controls_and_invalid_unicode(service, body):
    grant = create_grant(service)
    service.grant_action(grant["id"], "accept", grant["terms_hash"])

    with pytest.raises((ValueError, UnicodeError)):
        service.draft(grant["id"], body, turn_id="turn-1", provider_round=1)


def test_revoked_and_expired_grants_cannot_be_used_for_drafting(service):
    revoked = create_grant(service)
    service.grant_action(revoked["id"], "accept", revoked["terms_hash"])
    service.revoke_grant(revoked["id"], actor="operator")
    with pytest.raises(PermissionError):
        service.draft(revoked["id"], "hello", turn_id="turn-1", provider_round=1)

    expired = create_grant(
        service,
        active_from="2026-09-01T00:00:00+00:00",
        expires_at="2026-10-01T00:00:00+00:00",
    )
    with pytest.raises(PermissionError, match="pending resident acceptance"):
        service.grant_action(expired["id"], "accept", expired["terms_hash"])
    with pytest.raises(PermissionError):
        service.draft(expired["id"], "hello", turn_id="turn-1", provider_round=1)
