import pytest

from vestigia_mcp.policy import (
    Capability,
    Decision,
    EffectClass,
    PolicyDenied,
    PolicyEngine,
)


def test_unknown_capability_denies_by_default() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyDenied):
        engine.require_allowed("social.publish_reply")


def test_read_only_archive_capability_is_allowed() -> None:
    engine = PolicyEngine()
    capability = engine.require_allowed("archive.read_text")
    assert capability.effect is EffectClass.PERCEIVE
    assert capability.default is Decision.ALLOW


def test_archive_stage_and_promotion_have_distinct_effect_classes() -> None:
    engine = PolicyEngine()
    assert engine.require_allowed("archive.stage_text").effect is EffectClass.PREPARE
    assert engine.require_allowed("archive.stage_directory").effect is EffectClass.PREPARE
    assert engine.require_allowed("archive.promote").effect is EffectClass.ACT
    assert engine.require_allowed("archive.promote_directory").effect is EffectClass.ACT


def test_gametable_keeps_views_and_local_turn_state_distinct() -> None:
    engine = PolicyEngine()
    assert engine.require_allowed("game.view").effect is EffectClass.PERCEIVE
    assert engine.require_allowed("game.events").effect is EffectClass.PERCEIVE
    assert engine.require_allowed("game.create").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.load_deck").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.act").effect is EffectClass.PREPARE


def test_confirm_or_deny_is_not_treated_as_allow() -> None:
    engine = PolicyEngine(
        (
            Capability(
                "social.publish_reply",
                EffectClass.ACT,
                Decision.CONFIRM,
                "Future write boundary.",
            ),
        )
    )
    with pytest.raises(PolicyDenied):
        engine.require_allowed("social.publish_reply")
