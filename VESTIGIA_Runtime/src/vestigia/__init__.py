"""VESTIGIA portable continuity runtime."""

__version__ = "0.8.0.dev0"

from .bootstrap import bootstrap_runtime as _bootstrap_runtime

_bootstrap_runtime()
del _bootstrap_runtime
