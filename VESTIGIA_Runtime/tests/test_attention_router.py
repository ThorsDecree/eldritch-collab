from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestigia.adapters.discord_adapter import (
    discord_platform_rejection_reason,
    discord_trigger_decision,
    guild_message_is_addressed,
    load_resident_controls,
    record_listening_event,
)
from vestigia.attention_router import (
    LexicalDecision,
    correct,
    lexical_decision,
    list_events,
    metrics,
    record_evaluation,
)
from vestigia.capabilities import is_formal_object_schema
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


class AttentionRouterCase(unittest.TestCase):
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


class AttentionRouterTests(AttentionRouterCase):
    def test_lexical_gate_has_boundaries_and_suppression(self) -> None:
        controls = {
            "enabled": True,
            "hard_wake_terms": ["Liora"],
            "soft_signal_terms": ["mall emergency"],
            "suppress_terms": ["quoted log"],
            "queue_threshold": 1,
            "semantic_threshold": 2,
        }
        self.assertEqual(
            "ignore",
            lexical_decision(
                "A liorama poster", controls, author_allowlisted=True
            ).route,
        )
        self.assertEqual(
            "invite",
            lexical_decision(
                "Liora would love this", controls, author_allowlisted=True
            ).route,
        )
        self.assertEqual(
            "semantic_check",
            lexical_decision(
                "There is a mall emergency", controls, author_allowlisted=True
            ).route,
        )
        suppressed = lexical_decision(
            "quoted log: mall emergency", controls, author_allowlisted=True
        )
        self.assertEqual("ignore", suppressed.route)
        self.assertIn("suppress_term", suppressed.reasons)

    def test_shadow_candidate_never_wakes_the_resident(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        runtime.house.dispatch(
            {
                "action": "attention.router.control",
                "mode": "configure",
                "include_resident_name": False,
                "include_listening_aliases": False,
                "include_watch_phrases": False,
                "hard_wake_terms": ["Gutterstar"],
            }
        )
        discord_platform_rejection_reason(
            author_is_bot=False,
            author_is_self=False,
            channel_id="room",
            is_dm=False,
            allowed_channels={"room"},
            allow_dms=True,
        )
        addressed = guild_message_is_addressed(
            is_dm=False,
            content="Gutterstar would love this",
            bot_is_mentioned=False,
            replies_to_bot=False,
            require_mention_or_reply=True,
        )
        controls = load_resident_controls(
            self.config, runtime.db, runtime.resident_id
        )
        decision = discord_trigger_decision(
            is_dm=False,
            content="Gutterstar would love this",
            addressed=addressed,
            author_allowlisted=True,
            controls=controls,
        )
        self.assertEqual("contextual_listening", decision["kind"])
        self.assertEqual("queue_only", decision["consequence"])
        self.assertEqual(
            "invite", decision["match"]["_attention_router"]["lexical"]["route"]
        )
        created = record_listening_event(
            runtime.db,
            resident_id=runtime.resident_id,
            room_id=runtime.room_id,
            interface="discord",
            channel_id="room",
            message_id="shadow-one",
            author_id="keeper",
            author_trust="allowlisted",
            content="Gutterstar would love this",
            match=decision["match"],
            consequence=decision["consequence"],
            cooldown_seconds=0,
        )
        self.assertTrue(created["accepted"])
        self.assertTrue(created["attention_router_shadow_mode"])
        event = list_events(runtime.db, runtime.resident_id)[0]
        self.assertEqual("ignore", event["live_route"])
        self.assertEqual("invite", event["effective_route"])
        self.assertEqual("not_needed", event["semantic_status"])
        self.assertFalse(event["raw_content_stored"])
        self.assertNotIn("Gutterstar would love this", json.dumps(event))

    def test_semantic_gate_is_bounded_cached_and_shadow_only(self) -> None:
        self.config.data.setdefault("attention_router", {})[
            "semantic_enabled"
        ] = True
        calls: list[str] = []

        def fake_gate(_config, content, metadata):
            calls.append(content)
            self.assertEqual("ambient_text", metadata["signal_kind"])
            return {
                "route": "queue",
                "confidence": 0.93,
                "addressed_to_resident": False,
                "resident_relevance": "meaningful",
                "reason_code": "meaningful_relevance",
                "model": "gpt-5-nano",
                "usage": {"input_tokens": 31, "output_tokens": 12},
            }

        lexical = LexicalDecision(
            route="semantic_check",
            score=2,
            reasons=("soft_term",),
            matched_term_hashes=("term-hash",),
            soft_hits=1,
        )
        first = record_evaluation(
            self.db,
            self.config,
            resident_id="test-resident",
            room_id="hearth",
            listening_event_id="listen-one",
            interface="discord",
            channel_id="room",
            message_id="semantic-one",
            author_trust="allowlisted",
            content="This seems important to her, but nobody asked her to join.",
            lexical=lexical,
            live_route="ignore",
            semantic_evaluator=fake_gate,
        )
        second = record_evaluation(
            self.db,
            self.config,
            resident_id="test-resident",
            room_id="hearth",
            listening_event_id="listen-two",
            interface="discord",
            channel_id="room",
            message_id="semantic-two",
            author_trust="allowlisted",
            content="This seems important to her, but nobody asked her to join.",
            lexical=lexical,
            live_route="ignore",
            semantic_evaluator=fake_gate,
        )
        self.assertEqual(1, len(calls))
        self.assertEqual("succeeded", first["semantic_status"])
        self.assertEqual("cached", second["semantic_status"])
        self.assertEqual("queue", first["effective_route"])
        self.assertEqual("ignore", first["live_route"])
        self.assertEqual(31, first["actual_input_tokens"])
        self.assertTrue(first["shadow_mode"])

    def test_non_allowlisted_content_never_reaches_semantic_gate(self) -> None:
        self.config.data.setdefault("attention_router", {})[
            "semantic_enabled"
        ] = True
        called = False

        def forbidden(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("semantic gate must not receive outsider content")

        event = record_evaluation(
            self.db,
            self.config,
            resident_id="test-resident",
            room_id="hearth",
            listening_event_id="listen-outsider",
            interface="discord",
            channel_id="room",
            message_id="outsider-router",
            author_trust="non_allowlisted_data_only",
            content="This content must remain local and hash-only.",
            lexical=LexicalDecision(
                route="semantic_check",
                score=2,
                reasons=("soft_term",),
                matched_term_hashes=("outsider-term",),
                soft_hits=1,
            ),
            live_route="queue",
            semantic_evaluator=forbidden,
        )
        self.assertFalse(called)
        self.assertEqual("refused_untrusted", event["semantic_status"])
        self.assertFalse(event["semantic_requested"])
        self.assertEqual("queue", event["effective_route"])
        self.assertNotIn("must remain local", json.dumps(event))

    def test_exhausted_semantic_budget_fails_closed_without_call(self) -> None:
        self.config.data.setdefault("attention_router", {}).update(
            {"semantic_enabled": True, "max_calls_per_hour": 0}
        )
        called = False

        def forbidden(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("budgeted-out gate must not be called")

        event = record_evaluation(
            self.db,
            self.config,
            resident_id="test-resident",
            room_id="hearth",
            listening_event_id="listen-budget",
            interface="discord",
            channel_id="room",
            message_id="budget-one",
            author_trust="allowlisted",
            content="A soft ambiguous candidate.",
            lexical=LexicalDecision(
                route="semantic_check",
                score=2,
                reasons=("soft_term",),
                matched_term_hashes=("budget-term",),
                soft_hits=1,
            ),
            live_route="ignore",
            semantic_evaluator=forbidden,
        )
        self.assertFalse(called)
        self.assertEqual("budget_blocked", event["semantic_status"])
        self.assertEqual("queue", event["effective_route"])
        self.assertEqual("hourly_call_budget", event["error_type"])

    def test_resident_correction_is_hashed_labeled_evidence(self) -> None:
        event = record_evaluation(
            self.db,
            self.config,
            resident_id="test-resident",
            room_id="hearth",
            listening_event_id="listen-correction",
            interface="discord",
            channel_id="room",
            message_id="correction-one",
            author_trust="allowlisted",
            content="Test Resident was mentioned in passing.",
            lexical=LexicalDecision(
                route="invite",
                score=10,
                reasons=("hard_term",),
                matched_term_hashes=("resident-name",),
                hard_hits=1,
            ),
            live_route="ignore",
        )
        corrected = correct(
            self.db,
            "test-resident",
            event["id"],
            route="ignore",
            note="Merely mentioned; not invited.",
        )
        self.assertEqual("ignore", corrected["corrected_route"])
        self.assertIsNotNone(corrected["correction_note_hash"])
        self.assertNotIn("Merely mentioned", json.dumps(corrected))
        summary = metrics(self.db, "test-resident", hours=24)
        self.assertEqual(1, summary["resident_corrections"])
        self.assertTrue(summary["shadow_mode"])
        self.assertFalse(summary["live_routing_changed"])

    def test_observatory_and_capability_contracts_are_legible(self) -> None:
        runtime = CoreRuntime(self.config, provider=FakeProvider(), fake=True)
        observatory = runtime.house.dispatch(
            {"action": "house.observatory", "section": "all"}
        )
        panel = observatory["observatory"]["attention_router"]
        self.assertTrue(panel["shadow_mode"])
        self.assertFalse(panel["live_routing_changed"])
        self.assertFalse(panel["semantic_gate_is_authority"])

        for name in (
            "attention.router.control",
            "attention.router.decisions",
            "attention.router.correct",
        ):
            focused = runtime.house.dispatch(
                {"action": "capabilities", "target": name}
            )
            capability = focused["capability"]
            self.assertEqual(name, capability["name"])
            self.assertTrue(is_formal_object_schema(capability["input_schema"]))


if __name__ == "__main__":
    unittest.main()
