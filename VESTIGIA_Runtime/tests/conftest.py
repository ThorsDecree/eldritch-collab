from __future__ import annotations

import importlib.util
import sys
from typing import Any


def pytest_configure() -> None:
    """Install narrow test doubles and dynamic-harness import compatibility."""
    original_module_from_spec = importlib.util.module_from_spec

    def registered_module_from_spec(spec: Any) -> Any:
        module = original_module_from_spec(spec)
        if getattr(spec, "name", None) == "vestigia_discord_harness":
            # dataclasses on Python 3.11 resolves postponed annotations through
            # sys.modules while the dynamically loaded harness is executing.
            # Register only this test-support module before exec_module().
            sys.modules[spec.name] = module
        return module

    importlib.util.module_from_spec = registered_module_from_spec

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
