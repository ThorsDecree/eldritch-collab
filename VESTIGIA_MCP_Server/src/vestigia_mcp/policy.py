from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EffectClass(StrEnum):
    PERCEIVE = "perceive"
    PREPARE = "prepare"
    ACT = "act"


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class Capability:
    name: str
    effect: EffectClass
    default: Decision
    description: str


DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "archive.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect configured Archive sources and their basic metadata.",
    ),
    Capability(
        "archive.list",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "List relative files in a configured Archive source.",
    ),
    Capability(
        "archive.read_text",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read one bounded UTF-8 text file from a configured Archive source.",
    ),
    Capability(
        "archive.diff",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Compare live and snapshot Archive content by relative path and SHA-256.",
    ),
)


class PolicyDenied(PermissionError):
    pass


class PolicyEngine:
    """Small executable policy spine. Unknown capability names deny by default."""

    def __init__(self, capabilities: tuple[Capability, ...] = DEFAULT_CAPABILITIES):
        self._capabilities = {cap.name: cap for cap in capabilities}

    def capability(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def require_allowed(self, name: str) -> Capability:
        capability = self.capability(name)
        if capability is None:
            raise PolicyDenied(f"Unknown capability denied: {name}")
        if capability.default is not Decision.ALLOW:
            raise PolicyDenied(
                f"Capability requires {capability.default.value}: {name}"
            )
        return capability
