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
        "archive.read_bytes",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read one bounded base64 byte page from a configured Archive source.",
    ),
    Capability(
        "archive.read_media",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read one bounded, signature-checked raster image from an Archive source.",
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
        "archive.write_capabilities",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect the deployment-scoped canonical Archive text-write boundary.",
    ),
    Capability(
        "archive.stage_text",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Create a durable MCP-owned text proposal without changing the canonical Archive.",
    ),
    Capability(
        "archive.stage_directory",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Create a durable MCP-owned directory proposal without changing the canonical Archive.",
    ),
    Capability(
        "archive.stage_list",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "List bounded metadata for durable canonical Archive proposals.",
    ),
    Capability(
        "archive.stage_inspect",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect and revalidate one canonical Archive proposal.",
    ),
    Capability(
        "archive.stage_discard",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Discard an unpromoted canonical Archive proposal without changing the Archive.",
    ),
    Capability(
        "archive.promote",
        EffectClass.ACT,
        Decision.ALLOW,
        "Atomically promote one digest-bound proposal after revalidating path and base hash.",
    ),
    Capability(
        "archive.promote_directory",
        EffectClass.ACT,
        Decision.ALLOW,
        "Promote one digest-bound directory proposal after revalidating every planned path.",
    ),
    Capability(
        "mount.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect operator-configured named read-only filesystem mounts.",
    ),
    Capability(
        "mount.list",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "List paged relative paths inside one named read-only mount.",
    ),
    Capability(
        "mount.read_text",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read one paged UTF-8 text file inside a named read-only mount.",
    ),
    Capability(
        "mount.read_media",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read one bounded raster image inside a named read-only mount.",
    ),
    Capability(
        "mount.search_text",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Search paged literal text evidence inside a named read-only mount.",
    ),
    Capability(
        "game.profiles",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect installed GameTable profiles and their explicit rules-enforcement limits.",
    ),
    Capability(
        "game.status",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect the enabled GameTable module and aggregate non-secret table state.",
    ),
    Capability(
        "game.view",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "View a public GameTable state or a seat-filtered private projection with a valid seat token.",
    ),
    Capability(
        "game.events",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Read paged public GameTable events plus only the caller seat's addressed private details.",
    ),
    Capability(
        "game.create",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Create an MCP-owned GameTable lobby and one private development seat token per configured seat.",
    ),
    Capability(
        "game.load_deck",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Load one seat's private deck into a GameTable lobby with that seat's development token.",
    ),
    Capability(
        "game.start",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Start a GameTable lobby with server-side shuffle commitments and private opening hands.",
    ),
    Capability("game.mulligan", EffectClass.PREPARE, Decision.ALLOW, "Privately replace one seat's opening hand while opening hands are being resolved."),
    Capability("game.keep", EffectClass.PREPARE, Decision.ALLOW, "Mark one seat's opening hand kept; activate play when all seats have kept."),
    Capability(
        "game.act",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Apply one revision-bound, profile-limited GameTable state action as the current priority seat.",
    ),
    Capability(
        "game.pass_priority",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Pass GameTable priority with revision binding and advance the profile turn step after all pass.",
    ),
    Capability(
        "game.yield",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Record a bounded standing GameTable yield; all active seats must consent before the earliest target is advanced.",
    ),
    Capability(
        "game.shortcut_propose",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Propose one explicit current-or-next-turn GameTable shortcut for unanimous active-seat consent.",
    ),
    Capability(
        "game.shortcut_respond",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Accept or decline a pending GameTable shortcut; unanimous acceptance executes one compact state transition.",
    ),
    Capability(
        "game.concede",
        EffectClass.PREPARE,
        Decision.ALLOW,
        "Record a seat's GameTable concession without erasing game history.",
    ),
    Capability(
        "runtime.list",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "List operator-named Runtime houses and their current linkage status.",
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
        "runtime.write_capabilities",
        EffectClass.PERCEIVE,
        Decision.ALLOW,
        "Inspect the Runtime-local mutations explicitly granted to this MCP deployment.",
    ),
    Capability(
        "runtime.write",
        EffectClass.ACT,
        Decision.ALLOW,
        "Dispatch one explicitly granted, non-outward Runtime-local mutation.",
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
