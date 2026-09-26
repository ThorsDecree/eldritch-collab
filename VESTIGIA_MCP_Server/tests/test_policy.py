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


def test_sense_registry_capabilities_are_read_only() -> None:
    engine = PolicyEngine()
    for name in ("sense.list", "sense.show", "sense.can_perceive"):
        capability = engine.require_allowed(name)
        assert capability.effect is EffectClass.PERCEIVE
        assert capability.default is Decision.ALLOW


def test_lanternslide_capabilities_have_bounded_effects() -> None:
    engine = PolicyEngine()
    for name in (
        "lanternslide.status",
        "lanternslide.find",
        "lanternslide.deal",
        "lanternslide.contact_sheet",
    ):
        capability = engine.require_allowed(name)
        assert capability.effect is EffectClass.PERCEIVE
        assert capability.default is Decision.ALLOW
    for name in ("lanternslide.scan", "lanternslide.stage_catalog"):
        capability = engine.require_allowed(name)
        assert capability.effect is EffectClass.PREPARE
        assert capability.default is Decision.ALLOW


def test_receipt_trace_is_read_only() -> None:
    capability = PolicyEngine().require_allowed("receipts.trace")
    assert capability.effect is EffectClass.PERCEIVE
    assert capability.default is Decision.ALLOW


def test_archive_stage_and_promotion_have_distinct_effect_classes() -> None:
    engine = PolicyEngine()
    assert engine.require_allowed("archive.stage_text").effect is EffectClass.PREPARE
    assert engine.require_allowed("archive.stage_directory").effect is EffectClass.PREPARE
    assert engine.require_allowed("archive.promote").effect is EffectClass.ACT
    assert engine.require_allowed("archive.promote_directory").effect is EffectClass.ACT


def test_porchlight_staging_is_prepare_only_and_not_canonical_write() -> None:
    engine = PolicyEngine()
    capability = engine.require_allowed("archive.stage_porchlight")
    assert capability.effect is EffectClass.PREPARE
    assert capability.default is Decision.ALLOW


def test_porchlight_direct_share_is_an_allowed_canonical_write() -> None:
    engine = PolicyEngine()
    capability = engine.require_allowed("archive.share_porchlight")
    assert capability.effect is EffectClass.ACT
    assert capability.default is Decision.ALLOW


def test_gametable_keeps_views_and_local_turn_state_distinct() -> None:
    engine = PolicyEngine()
    assert engine.require_allowed("game.view").effect is EffectClass.PERCEIVE
    assert engine.require_allowed("game.events").effect is EffectClass.PERCEIVE
    assert engine.require_allowed("game.create").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.load_deck").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.act").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.yield").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.shortcut_propose").effect is EffectClass.PREPARE
    assert engine.require_allowed("game.shortcut_respond").effect is EffectClass.PREPARE


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


def test_daemon_bridge_peer_capabilities_are_perceive_only() -> None:
    engine = PolicyEngine()
    for name in (
        "daemon_bridge.status",
        "daemon_bridge.residents",
        "daemon_bridge.capabilities",
        "daemon_bridge.query",
    ):
        capability = engine.require_allowed(name)
        assert capability.effect is EffectClass.PERCEIVE
        assert capability.default is Decision.ALLOW


def test_stable_dev_surface_has_one_mutation_capability() -> None:
    engine = PolicyEngine()
    expected = {
        "dev.capabilities": EffectClass.PERCEIVE,
        "dev.process": EffectClass.PERCEIVE,
        "dev.logs": EffectClass.PERCEIVE,
        "dev.call": EffectClass.ACT,
    }
    for name, effect in expected.items():
        capability = engine.require_allowed(name)
        assert capability.effect is effect
        assert capability.default is Decision.ALLOW
