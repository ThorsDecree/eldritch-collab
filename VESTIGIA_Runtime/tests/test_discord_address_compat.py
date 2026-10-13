from __future__ import annotations

import pytest

from vestigia.adapters.discord_adapter import guild_message_is_addressed


def _is_addressed(value: object) -> bool:
    """Accept the historical bool or a richer address-decision object."""

    return bool(getattr(value, "addressed", value))


@pytest.mark.parametrize(
    ("mentioned", "replied"),
    [
        (True, False),
        (False, True),
    ],
)
def test_discord_address_result_remains_truthy_across_contract_shapes(
    mentioned: bool,
    replied: bool,
) -> None:
    result = guild_message_is_addressed(
        is_dm=False,
        content="participant message",
        bot_is_mentioned=mentioned,
        replies_to_bot=replied,
        require_mention_or_reply=True,
    )
    assert _is_addressed(result) is True
