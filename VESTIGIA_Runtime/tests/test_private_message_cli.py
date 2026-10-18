from __future__ import annotations

import json

from vestigia.cli import main
from vestigia.home import initialize_home
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.private_messages import PrivateMessageService


def create_queued_message(home):
    config = load_config(home)
    db = ContinuityDB(config.home_path / "memory" / "continuity.db")
    db.initialize()
    service = PrivateMessageService(db, str(config.get("resident.id")))
    grant = service.create_grant(
        "discord_channel", "123456789012345678",
        "2026-10-18T00:00:00+00:00", "2026-11-18T00:00:00+00:00", 3,
    )
    service.grant_action(grant["id"], "accept", grant["terms_hash"])
    draft = service.draft(grant["id"], "Approved exact text", turn_id="turn-1", provider_round=0)
    return service, service.submit(
        draft["id"], draft["draft_hash"], turn_id="turn-1", provider_round=1,
        source_invocation="private_curation",
    )


def test_operator_can_create_list_show_and_revoke_finite_grant(tmp_path, capsys):
    home = initialize_home(tmp_path / "home", name="Resident", glyph="🏮")
    common = [
        str(home),
        "--destination-kind", "discord_channel",
        "--destination-id", "123456789012345678",
        "--active-from", "2026-10-18T00:00:00+00:00",
        "--expires-at", "2026-11-18T00:00:00+00:00",
        "--message-quota", "2",
    ]

    assert main(["message", "grant", "create", *common]) == 0
    created = json.loads(capsys.readouterr().out)
    grant_id = created["id"]
    assert created["state"] == "pending_resident_acceptance"
    assert created["terms_hash"]

    assert main(["message", "grant", "list", str(home)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [grant["id"] for grant in listed] == [grant_id]

    assert main(["message", "grant", "show", str(home), grant_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["terms"]["destination_id"] == "123456789012345678"

    assert main(["message", "grant", "revoke", str(home), grant_id]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["state"] == "revoked"


def test_operator_cannot_create_grant_without_exact_destination_or_finite_terms(tmp_path, capsys):
    home = initialize_home(tmp_path / "home", name="Resident", glyph="🏮")

    result = main([
            "message", "grant", "create", str(home),
            "--destination-kind", "discord_channel",
            "--destination-id", "not-an-id",
            "--active-from", "2026-10-18T00:00:00+00:00",
            "--expires-at", "2026-11-18T00:00:00+00:00",
            "--message-quota", "2",
        ])
    assert result == 2
    assert "numeric Discord ID" in capsys.readouterr().err


def test_outbox_show_hides_body_unless_explicitly_requested(tmp_path, capsys):
    home = initialize_home(tmp_path / "home", name="Resident", glyph="🏮")
    _, outbox = create_queued_message(home)

    assert main(["message", "outbox", "show", str(home), outbox["id"]]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["status"] == "queued"
    assert metadata["body_available"] is True
    assert "body" not in metadata

    assert main([
        "message", "outbox", "show", str(home), outbox["id"], "--include-body"
    ]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["body"] == "Approved exact text"


def test_operator_can_cancel_and_reconcile_with_a_separate_receipt(tmp_path, capsys):
    home = initialize_home(tmp_path / "home", name="Resident", glyph="🏮")
    service, cancellable = create_queued_message(home)
    assert main([
        "message", "outbox", "cancel", str(home), cancellable["id"]
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"

    service, uncertain = create_queued_message(home)
    service.claim(uncertain["id"])
    service.finish_delivery(
        uncertain["id"], "delivery_uncertain", safe_error_code="network_timeout"
    )
    assert main([
        "message", "outbox", "reconcile", str(home), uncertain["id"],
        "--outcome", "not-delivered", "--reason", "Verified absent in destination",
    ]) == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled["status"] == "delivery_uncertain"
    assert reconciled["reconciliation"]["outcome"] == "not-delivered"
    inspected = service.inspect_outbox(uncertain["id"], include_body=True)
    assert inspected["status"] == "delivery_uncertain"
    assert inspected["body_available"] is False
    assert "body" not in inspected

    events = service.db.connect()
    with events as connection:
        receipt = connection.execute(
            "SELECT event_type, metadata_json FROM private_message_events "
            "WHERE object_id=? ORDER BY created_at DESC LIMIT 1", (uncertain["id"],)
        ).fetchone()
    assert receipt["event_type"] == "reconciled"

    assert main([
        "message", "outbox", "reconcile", str(home), uncertain["id"],
        "--outcome", "delivered", "--reason", "Second attempt should fail",
    ]) == 2
    assert "already been reconciled" in capsys.readouterr().err
