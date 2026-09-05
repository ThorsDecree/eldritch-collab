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
        "archive.health",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect mechanical Archive health, local links, normalization ambiguity, and routing coverage candidates.",
    ),
    Capability(
        "runtime.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect optional VESTIGIA Runtime linkage and its read-only projection status.",
    ),
    Capability(
        "runtime.capabilities",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect the read-only projection derived from Runtime's executable CapabilityRegistry.",
    ),
    Capability(
        "runtime.call",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Dispatch one Runtime capability only when Runtime itself classifies it as a safe read projection.",
    ),
    Capability(
        "receipts.recent",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read recent capability receipts without exposing raw tool arguments.",
    ),
    Capability(
        "audit.show",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect one MCP audit receipt by durable event ID without treating it as memory.",
    ),
    Capability(
        "system.identity",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect the exact MCP deployment identity, fingerprints, Archive witnesses, Runtime linkage, and qualification limits.",
    ),
    Capability(
        "house.glance",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Return a compact descriptive house-state digest for bells and autonomous orientation.",
    ),
    Capability(
        "vestigia.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect this deployment's version, policy surface, Archive configuration, Runtime linkage, and audit health.",
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
