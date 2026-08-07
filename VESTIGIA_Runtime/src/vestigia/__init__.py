"""VESTIGIA portable continuity runtime."""

__version__ = "0.7.0"

from .sensory_apparatus import install_core as _install_core

_install_core()
del _install_core

from .attention_apparatus import install_core as _install_attention_router

_install_attention_router()
del _install_attention_router

from .attention_keyring import install_core as _install_attention_keyring

_install_attention_keyring()
del _install_attention_keyring

from .image_drawer_continuation import install_core as _install_image_drawer_continuation

_install_image_drawer_continuation()
del _install_image_drawer_continuation

from .workshop_sandbox import install_core as _install_workshop_sandbox

_install_workshop_sandbox()
del _install_workshop_sandbox

from .workshop_script_shelf import install_core as _install_workshop_script_shelf

_install_workshop_script_shelf()
del _install_workshop_script_shelf
