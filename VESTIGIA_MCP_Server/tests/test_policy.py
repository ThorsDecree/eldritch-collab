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
