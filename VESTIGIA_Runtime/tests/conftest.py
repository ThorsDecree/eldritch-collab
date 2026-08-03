from __future__ import annotations


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
