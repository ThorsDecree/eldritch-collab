from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Final

from .composition import freeze_composition


_INSTALLATION_PLAN: Final[tuple[tuple[str, str], ...]] = (
    ("sensory_apparatus", "register_composition"),
    ("attention_apparatus", "register_composition"),
    ("attention_keyring", "register_composition"),
    ("image_drawer_continuation", "register_composition"),
    ("workshop_sandbox", "register_composition"),
    ("workshop_script_shelf", "register_composition"),
    ("workshop_microscope", "register_composition"),
)

_lock = RLock()
_registered = False


def installation_plan() -> tuple[tuple[str, str], ...]:
    return _INSTALLATION_PLAN


def bootstrap_runtime() -> None:
    """Register and freeze built-in runtime composition exactly once."""

    global _registered
    with _lock:
        if _registered:
            return
        seen: set[tuple[str, str]] = set()
        for module_name, registrar_name in _INSTALLATION_PLAN:
            key = (module_name, registrar_name)
            if key in seen:
                raise RuntimeError(
                    f"duplicate runtime composition entry: {module_name}.{registrar_name}"
                )
            seen.add(key)
            module = import_module(f".{module_name}", package=__package__)
            registrar = getattr(module, registrar_name, None)
            if not callable(registrar):
                raise RuntimeError(
                    f"required runtime registrar is unavailable: {module_name}.{registrar_name}"
                )
            registrar()
        freeze_composition()
        _registered = True
