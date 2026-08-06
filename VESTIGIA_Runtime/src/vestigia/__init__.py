"""VESTIGIA portable continuity runtime."""

__version__ = "0.7.0"

from .sensory_apparatus import install_core as _install_core

_install_core()
del _install_core
