"""Interface adapters. Continuity behavior belongs to the core, not these doors."""

from ..sensory_discord import patch as _patch_sensory_discord
from . import discord_adapter as _discord_adapter

_patch_sensory_discord(_discord_adapter)
del _patch_sensory_discord, _discord_adapter
