from __future__ import annotations

import pytest


@pytest.fixture
def private_message_test_clock(monkeypatch):
    """Keep dated grant fixtures stable while honoring explicit expiry times."""
    import vestigia.private_messages as private_messages

    real_now = private_messages._now
    monkeypatch.setattr(
        private_messages, "_now",
        lambda value=None: real_now(value or "2026-11-01T00:00:00+00:00"),
    )


def pytest_configure() -> None:
    """Use constructor-stable Discord error doubles in adapter unit tests."""
    try:
        import discord
    except ImportError:
        return

    class NotFound(Exception):
        pass

    class Forbidden(Exception):
        pass

    discord.NotFound = NotFound
    discord.Forbidden = Forbidden
