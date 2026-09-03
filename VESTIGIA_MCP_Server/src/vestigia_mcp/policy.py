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
        "archive.search_text",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Search bounded UTF-8 Archive text literally and return line-level evidence.",
    ),
    Capability(
        "archive.diff",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Compare live and snapshot Archive content by relative path and SHA-256.",
    ),
    Capability(
        "archive.diff_detail",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Compare one live/snapshot Archive path by size and SHA-256 without hashing unrelated files.",
    ),
    Capability(
        "archive.registry_status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect canonical house_index registry targets against the selected Archive source.",
    ),
    Capability(
        "receipts.recent",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read recent capability receipts without exposing raw tool arguments.",
    ),
    Capability(
        "vestigia.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect this deployment's version, policy surface, Archive configuration, and audit health.",
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

    def capabilities(self) -> tuple[Capability, ...]:
        return tuple(self._capabilities[name] for name in sorted(self._capabilities))

    def require_allowed(self, name: str) -> Capability:
        capability = self.capability(name)
        if capability is None:
            raise PolicyDenied(f"Unknown capability denied: {name}")
        if capability.default is not Decision.ALLOW:
            raise PolicyDenied(
                f"Capability requires {capability.default.value}: {name}"
            )
        return capability
