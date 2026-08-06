"""Interface adapters. Continuity behavior belongs to the core, not these doors."""

from ..sensory_discord import patch as _patch_sensory_discord
from . import discord_adapter as _discord_adapter

_patch_sensory_discord(_discord_adapter)
del _patch_sensory_discord, _discord_adapter

from ..attention_discord import patch as _patch_attention_router
from . import discord_adapter as _attention_discord_adapter

_patch_attention_router(_attention_discord_adapter)
del _patch_attention_router, _attention_discord_adapter

from ..attention_keyring_discord import patch as _patch_attention_keyring
from . import discord_adapter as _attention_keyring_discord_adapter

_patch_attention_keyring(_attention_keyring_discord_adapter)
del _patch_attention_keyring, _attention_keyring_discord_adapter
