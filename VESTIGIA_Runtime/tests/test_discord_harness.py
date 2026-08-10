from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


HARNESS_PATH = Path(__file__).resolve().parents[1] / "tools" / "discord_harness.py"
_spec = importlib.util.spec_from_file_location("vestigia_discord_harness", HARNESS_PATH)
assert _spec is not None and _spec.loader is not None
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)

DeliveryAmbiguous = _harness.DeliveryAmbiguous
DeliveryError = _harness.DeliveryError
DeliveryOutcome = _harness.DeliveryOutcome
DiscordHarness = _harness.DiscordHarness
Failpoint = _harness.Failpoint
HarnessCrash = _harness.HarnessCrash
RuntimeResult = _harness.RuntimeResult
execute_scenario = _harness.execute_scenario


class _DictConfig:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


def test_dm_allowlist_and_guild_addressing_policy() -> None:
    harness = DiscordHarness(
        allowed_users=["1001"],
        allowed_channels=["2001"],
        allow_dms=True,
        require_mention_or_reply=True,
    )

    allowed_dm = harness.inbound(
        message_id=1,
        user_id=1001,
        channel_id=3001,
        content="hello from dm",
    )
    assert harness.policy_decision(allowed_dm).accepted

    denied_dm = harness.inbound(
        message_id=2,
        user_id=1002,
        channel_id=3002,
        content="hello from stranger",
    )
    assert harness.policy_decision(denied_dm).rejection == "user_not_allowed"

    unaddressed = harness.inbound(
        message_id=3,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="ambient guild text",
    )
    assert harness.policy_decision(unaddressed).trigger_kind == "ignored"

    mentioned = harness.inbound(
        message_id=4,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="resident?",
        mention_bot=True,
    )
    decision = harness.policy_decision(mentioned)
    assert decision.accepted
    assert decision.addressed is True
    assert decision.trigger_kind == "direct"


def test_platform_rejections_cover_bot_self_dm_and_channel_boundaries() -> None:
    harness = DiscordHarness(
        allowed_users=["1001"],
        allowed_channels=["2001"],
        allow_dms=False,
    )

    bot_message = harness.inbound(
        message_id=10,
        user_id=7000,
        channel_id=2001,
        guild_id=4001,
        content="bot echo",
        author_is_bot=True,
    )
    assert harness.policy_decision(bot_message).rejection == "bot_author"

    self_message = harness.inbound(
        message_id=11,
        user_id=9000,
        channel_id=2001,
        guild_id=4001,
        content="self echo",
        author_is_bot=True,
    )
    assert harness.policy_decision(self_message).rejection == "self_author"

    dm = harness.inbound(
        message_id=12,
        user_id=1001,
        channel_id=3001,
        content="dm disabled",
    )
    assert harness.policy_decision(dm).rejection == "dms_disabled"

    wrong_channel = harness.inbound(
        message_id=13,
        user_id=1001,
        channel_id=2999,
        guild_id=4001,
        content="wrong channel",
        mention_bot=True,
    )
    assert harness.policy_decision(wrong_channel).rejection == "guild_channel_not_allowed"


def test_reply_to_bot_counts_as_addressed() -> None:
    harness = DiscordHarness(
        allowed_users=["1001"],
        allowed_channels=["2001"],
        require_mention_or_reply=True,
    )
    message = harness.inbound(
        message_id=20,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="continuing thread",
        reply_to_bot=True,
    )
    decision = harness.policy_decision(message)
    assert decision.addressed is True
    assert decision.trigger_kind == "direct"


def test_channel_history_is_reverse_chronological_and_before_bounded() -> None:
    harness = DiscordHarness()
    channel = harness.channel(3001)
    harness.inbound(message_id=31, user_id=1001, channel_id=3001, content="first")
    harness.inbound(message_id=32, user_id=1001, channel_id=3001, content="second")
    third = harness.inbound(message_id=33, user_id=1001, channel_id=3001, content="third")

    async def collect() -> tuple[list[int], list[int]]:
        all_ids = [item.id async for item in channel.history(limit=10)]
        before_ids = [item.id async for item in channel.history(limit=10, before=third)]
        return all_ids, before_ids

    all_ids, before_ids = asyncio.run(collect())
    assert all_ids == [33, 32, 31]
    assert before_ids == [32, 31]


def test_successful_delivery_has_one_external_effect_and_receipt() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=40, user_id=1001, channel_id=3001, content="hello")
    harness.commit_runtime_result(
        operation_id="turn-40",
        message=message,
        result=RuntimeResult(text="resident reply"),
    )
    sent = asyncio.run(
        harness.deliver_runtime_text(
            operation_id="turn-40", message=message, text="resident reply"
        )
    )
    assert len(sent) == 1
    assert harness.ledger.receipts[-1]["status"] == "succeeded"
    fingerprint = harness.ledger.receipts[-1]["fingerprint"]
    assert harness.external_count(fingerprint) == 1


def test_rejected_delivery_has_failed_receipt_and_no_external_message() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=50, user_id=1001, channel_id=3001, content="hello")
    message.channel.queue_delivery(DeliveryOutcome.REJECT)
    with pytest.raises(DeliveryError):
        asyncio.run(
            harness.deliver_runtime_text(
                operation_id="turn-50", message=message, text="reply"
            )
        )
    assert harness.ledger.receipts[-1]["status"] == "failed"
    assert not [
        item for item in harness.ledger.external_effects if item["kind"] == "message.send"
    ]


def test_ambiguous_delivery_survives_restart_and_blocks_automatic_retry() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=60, user_id=1001, channel_id=3001, content="hello")
    message.channel.queue_delivery(DeliveryOutcome.AMBIGUOUS)
    with pytest.raises(DeliveryAmbiguous):
        asyncio.run(
            harness.deliver_runtime_text(
                operation_id="turn-60", message=message, text="reply"
            )
        )
    assert harness.ledger.receipts[-1]["status"] == "ambiguous"
    assert len(harness.ledger.ambiguous_effects) == 1
    external_before = len(harness.ledger.external_effects)
    harness.restart()
    with pytest.raises(DeliveryAmbiguous, match="refusing automatic retry"):
        asyncio.run(
            harness.deliver_runtime_text(
                operation_id="turn-60", message=message, text="reply"
            )
        )
    assert len(harness.ledger.external_effects) == external_before


def test_explicit_ambiguous_retry_is_possible_and_receipted() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=61, user_id=1001, channel_id=3001, content="hello")
    message.channel.queue_delivery(DeliveryOutcome.AMBIGUOUS)
    with pytest.raises(DeliveryAmbiguous):
        asyncio.run(
            harness.deliver_runtime_text(
                operation_id="turn-61", message=message, text="reply"
            )
        )
    message.channel.queue_delivery(DeliveryOutcome.SUCCESS)
    sent = asyncio.run(
        harness.deliver_runtime_text(
            operation_id="turn-61",
            message=message,
            text="reply",
            allow_ambiguous_retry=True,
        )
    )
    assert len(sent) == 1
    assert harness.ledger.receipts[-1]["status"] == "succeeded"


def test_duplicate_platform_effect_is_observable() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=70, user_id=1001, channel_id=3001, content="hello")
    message.channel.queue_delivery(DeliveryOutcome.DUPLICATE)
    asyncio.run(
        harness.deliver_runtime_text(
            operation_id="turn-70", message=message, text="reply"
        )
    )
    fingerprint = harness.ledger.receipts[-1]["fingerprint"]
    assert harness.external_count(fingerprint) == 2


@pytest.mark.parametrize(
    "failpoint",
    [Failpoint.BEFORE_RUNTIME_COMMIT, Failpoint.AFTER_RUNTIME_COMMIT_BEFORE_DELIVERY],
)
def test_runtime_commit_failpoints_are_deterministic(failpoint: object) -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=80, user_id=1001, channel_id=3001, content="hello")
    harness.arm_failpoint(failpoint)
    with pytest.raises(HarnessCrash):
        harness.commit_runtime_result(
            operation_id="turn-80",
            message=message,
            result=RuntimeResult(text="reply"),
        )
    if failpoint is Failpoint.BEFORE_RUNTIME_COMMIT:
        assert "turn-80" not in harness.ledger.runtime_commits
    else:
        assert "turn-80" in harness.ledger.runtime_commits


def test_after_delivery_before_receipt_models_external_effect_crash_window() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=90, user_id=1001, channel_id=3001, content="hello")
    harness.arm_failpoint(Failpoint.AFTER_DELIVERY_BEFORE_RECEIPT)
    with pytest.raises(HarnessCrash):
        asyncio.run(
            harness.deliver_runtime_text(
                operation_id="turn-90", message=message, text="reply"
            )
        )
    sends = [
        item for item in harness.ledger.external_effects if item["kind"] == "message.send"
    ]
    assert len(sends) == 1
    assert harness.ledger.receipts == []


def test_restart_preserves_durable_state_and_clears_prior_ephemeral_marker() -> None:
    harness = DiscordHarness()
    message = harness.inbound(message_id=100, user_id=1001, channel_id=3001, content="hello")
    harness.ledger.ephemeral.append({"kind": "transient"})
    harness.commit_runtime_result(
        operation_id="turn-100", message=message, result=RuntimeResult(text="reply")
    )
    harness.restart()
    assert harness.epoch == 2
    assert "turn-100" in harness.ledger.runtime_commits
    assert {"kind": "transient"} not in harness.ledger.ephemeral
    assert harness.ledger.durable_events[-1]["kind"] == "runtime.restart"


def test_dm_and_guild_history_do_not_cross_channels() -> None:
    harness = DiscordHarness(allowed_channels=["2001"])
    dm = harness.inbound(message_id=110, user_id=1001, channel_id=3001, content="private marker")
    guild = harness.inbound(
        message_id=111,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="shared marker",
        mention_bot=True,
    )

    async def collect() -> tuple[list[str], list[str]]:
        dm_values = [item.content async for item in dm.channel.history(limit=10)]
        guild_values = [item.content async for item in guild.channel.history(limit=10)]
        return dm_values, guild_values

    dm_values, guild_values = asyncio.run(collect())
    assert "private marker" in dm_values and "private marker" not in guild_values
    assert "shared marker" in guild_values and "shared marker" not in dm_values


def test_snapshot_redacts_message_content_by_default() -> None:
    harness = DiscordHarness()
    message = harness.inbound(
        message_id=130, user_id=1001, channel_id=3001, content="synthetic private text"
    )
    asyncio.run(
        harness.deliver_runtime_text(
            operation_id="turn-130", message=message, text="synthetic resident response"
        )
    )
    snapshot = harness.snapshot()
    sent = snapshot["channels"]["3001"]["sent"][0]
    assert sent["content"] is None
    encoded = json.dumps(snapshot)
    assert "synthetic resident response" not in encoded
    assert "content_hash" in sent


def test_scenario_runner_exercises_policy_delivery_and_restart() -> None:
    scenario = {
        "config": {"allowed_users": ["1001"], "allowed_channels": ["2001"]},
        "steps": [
            {
                "op": "message",
                "message_id": 140,
                "user_id": 1001,
                "channel_id": 2001,
                "guild_id": 4001,
                "content": "ping",
                "mention_bot": True,
                "expect": {"accepted": True, "trigger_kind": "direct"},
            },
            {
                "op": "runtime_commit",
                "message_id": 140,
                "operation_id": "turn-140",
                "text": "pong",
            },
            {
                "op": "deliver_text",
                "message_id": 140,
                "operation_id": "turn-140",
                "text": "pong",
            },
            {"op": "restart"},
            {"op": "expect", "path": "epoch", "equals": 2},
            {
                "op": "expect",
                "path": "runtime_commits.turn-140.message_id",
                "equals": "140",
            },
        ],
    }
    result = asyncio.run(execute_scenario(scenario))
    assert result["epoch"] == 2
    assert result["receipts"][0]["status"] == "succeeded"


def test_real_recent_context_helper_respects_allowlisted_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from vestigia.adapters import discord_adapter as adapter

    harness = DiscordHarness(allowed_users=["1001"], allowed_channels=["2001"])
    harness.inbound(
        message_id=201,
        user_id=1002,
        channel_id=2001,
        guild_id=4001,
        content="nonallowlisted secret marker",
    )
    harness.inbound(
        message_id=202,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="allowlisted marker",
    )
    current = harness.inbound(
        message_id=203,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="current",
        mention_bot=True,
    )
    monkeypatch.setattr(
        adapter,
        "load_context_controls",
        lambda config, db, resident_id: {"ambient_visibility": "allowlisted_only"},
    )
    text, ids = asyncio.run(
        adapter.discord_recent_context(
            current,
            _DictConfig(
                {"discord.recent_messages": 10, "discord.recent_max_chars": 2200}
            ),
            None,
            "resident-1",
            {"1001"},
            harness.client,
        )
    )
    assert "allowlisted marker" in text
    assert "nonallowlisted secret marker" not in text
    assert ids == [202]


def test_real_recent_context_helper_mentions_only_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from vestigia.adapters import discord_adapter as adapter

    harness = DiscordHarness(allowed_users=["1001"], allowed_channels=["2001"])
    harness.inbound(
        message_id=211,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="ordinary prior",
    )
    harness.inbound(
        message_id=212,
        user_id=1002,
        channel_id=2001,
        guild_id=4001,
        content="<@9000> relevant ambient " + ("x" * 1000),
    )
    current = harness.inbound(
        message_id=213,
        user_id=1001,
        channel_id=2001,
        guild_id=4001,
        content="current",
        mention_bot=True,
    )
    monkeypatch.setattr(
        adapter,
        "load_context_controls",
        lambda config, db, resident_id: {"ambient_visibility": "mentions_only"},
    )
    text, ids = asyncio.run(
        adapter.discord_recent_context(
            current,
            _DictConfig({"discord.recent_messages": 10, "discord.recent_max_chars": 420}),
            None,
            "resident-1",
            {"1001"},
            harness.client,
        )
    )
    assert ids == [212]
    assert "ordinary prior" not in text
    assert "<@9000>" in text
    assert len(text) <= 420


def test_real_recent_context_hidden_returns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from vestigia.adapters import discord_adapter as adapter

    harness = DiscordHarness()
    current = harness.inbound(message_id=220, user_id=1001, channel_id=3001, content="current")
    monkeypatch.setattr(
        adapter,
        "load_context_controls",
        lambda config, db, resident_id: {"ambient_visibility": "hidden"},
    )
    text, ids = asyncio.run(
        adapter.discord_recent_context(
            current,
            _DictConfig({}),
            None,
            "resident-1",
            {"1001"},
            harness.client,
        )
    )
    assert text == ""
    assert ids == []
