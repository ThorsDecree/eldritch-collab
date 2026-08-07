from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Final


# Transitional bootstrap containment for v0.8.0.dev0.
#
# Existing feature modules still expose legacy install_core() adapters. Keeping the
# complete order here removes package-__init__ ordering drift and gives the registry
# migration one auditable choke point. New production monkey patches are forbidden;
# migrate each entry to explicit composition registries under issue #25.
_INSTALLATION_PLAN: Final[tuple[tuple[str, str], ...]] = (
    ("sensory_apparatus", "install_core"),
    ("attention_apparatus", "install_core"),
    ("attention_keyring", "install_core"),
    ("image_drawer_continuation", "install_core"),
    ("workshop_sandbox", "install_core"),
)

_lock = RLock()
_installed = False


def installation_plan() -> tuple[tuple[str, str], ...]:
    """Return the immutable transitional bootstrap plan."""

    return _INSTALLATION_PLAN


def bootstrap_runtime() -> None:
    """Install built-in runtime contributions exactly once.

    This function intentionally centralizes the remaining legacy installers. It is
    not the final composition architecture. The v0.8 migration replaces these
    adapters with collision-checked capability, Observatory, runtime-hook, drawer,
    and contract registries.
    """

    global _installed
    with _lock:
        if _installed:
            return
        seen: set[tuple[str, str]] = set()
        for module_name, installer_name in _INSTALLATION_PLAN:
            key = (module_name, installer_name)
            if key in seen:
                raise RuntimeError(f"duplicate runtime bootstrap entry: {module_name}.{installer_name}")
            seen.add(key)
            module = import_module(f".{module_name}", package=__package__)
            installer = getattr(module, installer_name, None)
            if not callable(installer):
                raise RuntimeError(
                    f"required runtime installer is unavailable: {module_name}.{installer_name}"
                )
            installer()
        _installed = True
