from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vestigia.adapters.discord_adapter import (
    discord_platform_rejection_reason,
    discord_trigger_decision,
    guild_message_is_addressed,
    load_resident_controls,
)
from vestigia.attention_router import LexicalDecision, inspect_event, record_evaluation
from vestigia.capabilities import is_formal_object_schema
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


class AttentionKeyringCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Test Resident", glyph="R"
        )
        self.config = load_config(self.home)
        self.db = ContinuityDB(self.home / "memory" / "continuity.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def source(self, channel_id: str = "room", *, is_dm: bool = False) -> None:
        discord_platform_rejection_reason(
            author_is_bot=False,
            author_is_self=False,
            channel_id=channel_id,
            is_dm=is_dm,
            allowed_channels={"room", "other"},
            allow_dms=True,
        )


class AttentionKeyringTests(AttentionKeyringCase):
    def test_quiet_expiry_restores_captured_baseline_without_widening(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        runtime.house.dispatch(
            {
                "action": "sensory.control",
                "mode": "configure",
                "listening_ingress_signals": ["mention"],
                "listening_allow_dms": False,
            }
        )
        activated = runtime.house.dispatch(
            {
                "action": "attention.quiet",
                "mode": "activate",
                "preset": "everything_closed",
                "duration_seconds": 600,
            }
        )
        self.assertEqual("quiet", activated["quiet"]["phase"])
        self.assertTrue(activated["receipt"])

        runtime.house.dispatch(
            {
                "action": "sensory.control",
                "mode": "configure",
                "listening_ingress_signals": [
                    "mention",
                    "reply",
                    "dm",
                    "command",
                    "ambient_text",
                ],
                "listening_allow_dms": True,
            }
        )
        with runtime.db.connect() as connection:
            connection.execute(
                "UPDATE attention_quiet_sessions SET expires_at=? WHERE resident_id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), runtime.resident_id),
            )

        self.source("room")
        controls = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )
        self.assertEqual(["mention"], controls["listening_ingress_signals"])
        self.assertFalse(controls["listening_allow_dms"])
        self.assertEqual("restored_locked", controls["_attention_quiet"]["phase"])

        ambient = guild_message_is_addressed(
            is_dm=False,
            content="ordinary ambient text",
            bot_is_mentioned=False,
            replies_to_bot=False,
            require_mention_or_reply=True,
        )
        ignored = discord_trigger_decision(
            is_dm=False,
            content="ordinary ambient text",
            addressed=ambient,
            author_allowlisted=True,
            controls=controls,
        )
        self.assertEqual("ignored", ignored["kind"])
        self.assertIn("restoration_cap", ignored["reason"])

        released = runtime.house.dispatch(
            {"action": "attention.quiet", "mode": "release"}
        )
        self.assertEqual("open", released["quiet"]["phase"])
        self.source("room")
        reopened = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )
        self.assertIn("ambient_text", reopened["listening_ingress_signals"])
        self.assertTrue(reopened["listening_allow_dms"])

    def test_preferences_are_explicit_scoped_and_reviewable(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        created = runtime.house.dispatch(
            {
                "action": "attention.preference",
                "mode": "create",
                "kind": "always_notice",
                "term": "Workshop Within",
                "interface": "discord",
                "channel_id": "room",
                "reason": "Explicit resident preference",
            }
        )["result"]
        runtime.house.dispatch(
            {
                "action": "attention.preference",
                "mode": "create",
                "kind": "usually_ignore",
                "term": "quoted fixture",
                "interface": "discord",
            }
        )
        runtime.house.dispatch(
            {
                "action": "attention.preference",
                "mode": "create",
                "kind": "semantic_check_only",
                "term": "show her this",
                "interface": "all",
            }
        )
        self.assertFalse(created["provenance"]["inferred_from_memory"])
        self.assertEqual("resident_attention_keyring", created["provenance"]["source"])

        self.source("room")
        room_controls = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )["_attention_router"]
        self.assertIn("Workshop Within", room_controls["hard_wake_terms"])
        self.assertIn("quoted fixture", room_controls["suppress_terms"])
        self.assertIn("show her this", room_controls["soft_signal_terms"])

        self.source("other")
        other_controls = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )["_attention_router"]
        self.assertNotIn("Workshop Within", other_controls["hard_wake_terms"])
        self.assertIn("quoted fixture", other_controls["suppress_terms"])

        deleted = runtime.house.dispatch(
            {
                "action": "attention.preference",
                "mode": "delete",
                "preference_id": created["id"],
            }
        )["result"]
        self.assertTrue(deleted["deleted"])
        self.assertEqual("deleted", deleted["status"])

    def test_live_discord_turn_records_why_wake_receipt(self) -> None:
        runtime = CoreRuntime(
            self.config,
            provider=FakeProvider(["I am awake and present."]),
            fake=True,
        )
        self.source("room")
        addressed = guild_message_is_addressed(
            is_dm=False,
            content="<@42> hello",
            bot_is_mentioned=True,
            replies_to_bot=False,
            require_mention_or_reply=True,
        )
        controls = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )
        decision = discord_trigger_decision(
            is_dm=False,
            content="<@42> hello",
            addressed=addressed,
            author_allowlisted=True,
            controls=controls,
        )
        self.assertEqual("invite_turn", decision["consequence"])

        result = runtime.chat(
            NormalizedMessage(
                content="<@42> hello",
                participant_text="<@42> hello",
                speaker_role="user",
                speaker_id="keeper",
                interface="discord",
                room_id=runtime.room_id,
                external_id="wake-message-one",
                ambient_context="Earlier safe context",
                metadata={
                    "channel_id": "room",
                    "is_dm": False,
                    "triggering_message_id": "wake-message-one",
                    "ambient_message_ids": ["ambient-one"],
                    "contextual_listening": False,
                },
            )
        )
        self.assertFalse(result.suppressed)
        receipts = runtime.house.dispatch(
            {"action": "attention.wake.receipts", "mode": "list"}
        )["result"]
        self.assertEqual(1, len(receipts))
        receipt = receipts[0]
        self.assertEqual("direct_mention", receipt["reason_code"])
        self.assertEqual("mention", receipt["signal_kind"])
        self.assertEqual("completed", receipt["status"])
        self.assertEqual(result.turn_id, receipt["turn_id"])
        self.assertEqual(
            ["wake-message-one", "ambient-one"], receipt["included_context_ids"]
        )
        self.assertFalse(receipt["included_is_influenced"])
        inspected = runtime.house.dispatch(
            {
                "action": "attention.wake.receipts",
                "mode": "inspect",
                "wake_id": receipt["id"],
            }
        )["result"]
        self.assertIn("included, not proven influential", inspected["why"])

    def test_correction_ergonomics_create_reviewable_evidence(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        event = record_evaluation(
            runtime.db,
            self.config,
            resident_id=runtime.resident_id,
            room_id=runtime.room_id,
            listening_event_id=None,
            interface="discord",
            channel_id="room",
            message_id="fixture-message",
            author_trust="allowlisted",
            content="Test Resident appears in a quoted fixture.",
            lexical=LexicalDecision(
                route="invite",
                score=10,
                reasons=("hard_term", "quoted_or_code_like"),
                matched_term_hashes=("hash",),
                hard_hits=1,
            ),
            live_route="ignore",
        )
        labeled = runtime.house.dispatch(
            {
                "action": "attention.correction",
                "mode": "label",
                "event_id": event["id"],
                "kind": "fixture_or_quote",
                "note": "Quoted test data, not a live invitation.",
            }
        )["result"]
        self.assertEqual("awaiting_review", labeled["status"])
        self.assertFalse(labeled["automatic_retraining"])
        self.assertEqual(
            "ignore",
            inspect_event(runtime.db, runtime.resident_id, event["id"])[
                "corrected_route"
            ],
        )
        dashboard = runtime.house.dispatch(
            {"action": "house.attention_dashboard", "limit": 10}
        )
        self.assertEqual(
            labeled["id"], dashboard["corrections_awaiting_review"][0]["id"]
        )
        reviewed = runtime.house.dispatch(
            {
                "action": "attention.correction",
                "mode": "review",
                "correction_id": labeled["id"],
                "status": "reviewed",
            }
        )["result"]
        self.assertEqual("reviewed", reviewed["status"])

    def test_dashboard_and_capabilities_are_formal_and_read_only(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        for name in (
            "attention.quiet",
            "attention.preference",
            "attention.wake.receipts",
            "attention.correction",
            "house.attention_dashboard",
        ):
            capability = runtime.house.dispatch(
                {"action": "capabilities", "target": name}
            )["capability"]
            self.assertTrue(is_formal_object_schema(capability["input_schema"]))
        dashboard = runtime.house.dispatch(
            {"action": "house.attention_dashboard", "limit": 5}
        )
        self.assertTrue(dashboard["read_only"])
        self.assertFalse(dashboard["outward_action"])
        self.assertFalse(dashboard["authority_changed"])
        self.assertIn("remaining", dashboard["semantic_budget"]["hourly"])
        self.assertIn("Platform reachability", dashboard["platform_reachability"]["meaning"])


if __name__ == "__main__":
    unittest.main()
