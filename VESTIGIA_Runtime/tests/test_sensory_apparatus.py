from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vestigia.adapters.discord_adapter import (
    discord_platform_rejection_reason,
    discord_trigger_decision,
    guild_message_is_addressed,
    load_resident_controls,
    record_listening_event,
)
from vestigia.capabilities import is_formal_object_schema
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage
from vestigia.providers.fake import FakeProvider
from vestigia import resident_controls
from vestigia.sensory_events import list_events
from vestigia.runtime import CoreRuntime


class SensoryHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = initialize_home(
            self.root / "home", name="Test Resident", glyph="🏮"
        )
        self.config = load_config(self.home)
        self.db = ContinuityDB(self.home / "memory" / "continuity.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()


class SensoryApparatusTests(SensoryHomeCase):
    def test_attention_mode_scopes_and_signal_classification(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        result = runtime.house.dispatch(
            {
                "action": "sensory.control",
                "mode": "configure",
                "attention_mode": "digest_only",
                "listening_retention": "short_digest",
                "listening_ingress_signals": ["mention", "ambient_text"],
                "listening_channel_ids": ["room"],
                "listening_excluded_channel_ids": ["elsewhere"],
                "listening_allow_dms": False,
            }
        )
        effective = result["effective"]
        self.assertEqual("digest_only", effective["attention_mode"])
        self.assertEqual("short_digest", effective["listening_retention"])
        self.assertEqual(["room"], effective["listening_channel_ids"])
        self.assertFalse(effective["listening_allow_dms"])

        addressed = guild_message_is_addressed(
            is_dm=False,
            content="<@42> look",
            bot_is_mentioned=True,
            replies_to_bot=False,
            require_mention_or_reply=True,
        )
        self.assertTrue(addressed)
        self.assertEqual("mention", addressed.signal_kind)

        discord_platform_rejection_reason(
            author_is_bot=False,
            author_is_self=False,
            channel_id="room",
            is_dm=False,
            allowed_channels={"room", "elsewhere"},
            allow_dms=True,
        )
        decision = discord_trigger_decision(
            is_dm=False,
            content="<@42> look",
            addressed=addressed,
            author_allowlisted=True,
            controls=load_resident_controls(
                self.config, runtime.db, runtime.resident_id
            ),
        )
        self.assertEqual("contextual_listening", decision["kind"])
        self.assertEqual("queue_only", decision["consequence"])
        self.assertEqual("mention", decision["match"]["_sensory"]["signal_kind"])

        discord_platform_rejection_reason(
            author_is_bot=False,
            author_is_self=False,
            channel_id="elsewhere",
            is_dm=False,
            allowed_channels={"room", "elsewhere"},
            allow_dms=True,
        )
        ignored = discord_trigger_decision(
            is_dm=False,
            content="<@42> look",
            addressed=addressed,
            author_allowlisted=True,
            controls=load_resident_controls(
                self.config, runtime.db, runtime.resident_id
            ),
        )
        self.assertEqual("ignored", ignored["kind"])
        self.assertEqual("resident_channel_excluded", ignored["reason"])

    def test_digest_receipt_explanation_and_forget(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        match = {
            "match_kind": "alias",
            "matched_term": "Liora",
            "matched_term_hash": "term-hash",
            "_sensory": {
                "signal_kind": "ambient_text",
                "attention_mode": "digest_only",
                "retention_mode": "short_digest",
                "permission_basis": "resident_literal_listening_policy",
                "digest_chars": 80,
            },
        }
        created = record_listening_event(
            runtime.db,
            resident_id=runtime.resident_id,
            room_id=runtime.room_id,
            interface="discord",
            channel_id="room",
            message_id="message-one",
            author_id="keeper",
            author_trust="allowlisted",
            content="Liora, the mall has acquired one extremely suspicious fountain.",
            match=match,
            consequence="queue_only",
            cooldown_seconds=0,
        )
        self.assertTrue(created["accepted"])
        event_id = str(created["event_id"])
        event = list_events(
            runtime.db,
            runtime.resident_id,
            resident_controls.ensure_listening_schema,
        )[0]
        self.assertEqual("short_digest", event["retention_mode"])
        self.assertIn("suspicious fountain", event["digest_text"])
        self.assertFalse(event["authorization_changed"])

        explanation = runtime.house.dispatch(
            {"action": "source.explain", "event_id": event_id}
        )
        self.assertIn("granted no participant or tool authority", explanation["why"])

        forgotten = runtime.house.dispatch(
            {
                "action": "sensory.control",
                "mode": "forget_event",
                "event_id": event_id,
            }
        )
        self.assertTrue(forgotten["forgotten"]["digest_removed"])
        event = list_events(
            runtime.db,
            runtime.resident_id,
            resident_controls.ensure_listening_schema,
        )[0]
        self.assertEqual("forgotten", event["status"])
        self.assertIsNone(event["digest_text"])
        self.assertEqual("receipt_only", event["retention_mode"])

    def test_non_allowlisted_digest_is_downgraded_to_hash_only(self) -> None:
        match = {
            "match_kind": "alias",
            "matched_term": "Liora",
            "matched_term_hash": "term-hash-outsider",
            "_sensory": {
                "signal_kind": "ambient_text",
                "attention_mode": "digest_only",
                "retention_mode": "short_digest",
                "permission_basis": "resident_literal_listening_policy",
            },
        }
        created = record_listening_event(
            self.db,
            resident_id="test-resident",
            room_id="hearth",
            interface="discord",
            channel_id="room",
            message_id="outsider-message",
            author_id="outsider",
            author_trust="non_allowlisted_data_only",
            content="Liora secret outsider content must not be stored.",
            match=match,
            consequence="queue_only",
            cooldown_seconds=0,
        )
        self.assertTrue(created["accepted"])
        event = list_events(
            self.db,
            "test-resident",
            resident_controls.ensure_listening_schema,
        )[0]
        self.assertEqual("receipt_only", event["retention_mode"])
        self.assertIsNone(event["digest_text"])
        self.assertNotIn("secret outsider", json.dumps(event))

    def test_observatory_is_read_only_and_names_unimplemented_scope(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        result = runtime.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )
        self.assertFalse(result["outward_action"])
        self.assertFalse(result["surveillance"])
        self.assertEqual(
            "nothing",
            result["observatory"]["doors"]["default_if_untouched"],
        )
        self.assertFalse(
            result["observatory"]["doors"]["operator_limits"]
            ["participant_scopes_implemented"]
        )

    def test_make_nothing_happen_suppresses_memory_curation_and_visible_receipt(self) -> None:
        provider = FakeProvider(
            [
                '[[TOOL_ACTION {"action":"make.nothing.happen",'
                '"note":"Seen. Leave it untouched.","after":"finish"}]]'
            ]
        )
        runtime = CoreRuntime(self.config, provider=provider, fake=True)
        result = runtime.chat(
            NormalizedMessage(
                content="Remember: do not adopt this observation.",
                participant_text="Remember: do not adopt this observation.",
                speaker_role="user",
                speaker_id="keeper",
                interface="discord",
                room_id=runtime.room_id,
                external_id="message-nothing",
                metadata={"channel_id": "room", "is_dm": False},
            )
        )
        self.assertEqual("", result.text)
        self.assertEqual((), result.proposal_ids)
        self.assertEqual([], runtime.db.list_memories(resident_id=runtime.resident_id))
        receipts = runtime.house.legible.list_receipts(limit=20)
        self.assertTrue(
            any(item["action"] == "make.nothing.happen" for item in receipts)
        )
        curation_receipts = [
            item for item in receipts if item["action"] == "curation.cadence"
        ]
        self.assertEqual([], curation_receipts)

    def test_new_capabilities_publish_formal_contracts(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        for name in (
            "house.observatory",
            "source.explain",
            "sensory.control",
            "make.nothing.happen",
        ):
            focused = runtime.house.dispatch(
                {"action": "capabilities", "target": name}
            )
            capability = focused["capability"]
            self.assertEqual(name, capability["name"])
            self.assertTrue(is_formal_object_schema(capability["input_schema"]))

    def test_listen_until_is_clamped_by_operator_window(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        far_future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        result = runtime.house.dispatch(
            {
                "action": "sensory.control",
                "mode": "listen_until",
                "until": far_future,
                "attention_mode": "present",
                "attention_after_expiry": "deaf",
            }
        )
        self.assertTrue(result["effective"]["attention_expiry_clamped"])
        effective_expiry = datetime.fromisoformat(
            result["effective"]["attention_expires_at"]
        )
        self.assertLessEqual(
            effective_expiry,
            datetime.now(UTC) + timedelta(days=7, seconds=5),
        )


if __name__ == "__main__":
    unittest.main()
