from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from vestigia.adapters.discord_adapter import (
    chunk_text,
    discord_platform_rejection_reason,
    discord_trigger_decision,
    guild_message_is_addressed,
)


class HarnessCrash(RuntimeError):
    """Deterministic process-stop marker raised by an armed failpoint."""


class DeliveryError(RuntimeError):
    """Synthetic Discord rejection before an external effect is committed."""


class DeliveryAmbiguous(RuntimeError):
    """External effect may exist, but acknowledgement was lost."""


class DeliveryOutcome(StrEnum):
    SUCCESS = "success"
    REJECT = "reject"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"
    DELAY = "delay"


class Failpoint(StrEnum):
    BEFORE_RUNTIME_COMMIT = "before_runtime_commit"
    AFTER_RUNTIME_COMMIT_BEFORE_DELIVERY = "after_runtime_commit_before_delivery"
    AFTER_DELIVERY_BEFORE_RECEIPT = "after_delivery_before_receipt"
    AFTER_RECEIPT = "after_receipt"
    BEFORE_RESTART_RESUME = "before_restart_resume"


def digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class DeterministicClock:
    current: datetime = field(
        default_factory=lambda: datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
    )

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> datetime:
        self.current += timedelta(seconds=float(seconds))
        return self.current


@dataclass(slots=True)
class FakeUser:
    id: int
    display_name: str
    bot: bool = False

    def __str__(self) -> str:
        return self.display_name


@dataclass(slots=True)
class FakeGuild:
    id: int


@dataclass(slots=True)
class FakeReference:
    message_id: int | None = None
    resolved: Any | None = None


@dataclass(slots=True)
class FakeAttachment:
    id: int
    filename: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    async def read(self) -> bytes:
        return bytes(self.data)


@dataclass(slots=True)
class FakeSentMessage:
    id: int
    channel: "FakeChannel"
    author: FakeUser
    content: str = ""
    reactions: list[dict[str, Any]] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)

    async def edit(self, *, content: str) -> "FakeSentMessage":
        self.content = str(content)
        self.edits.append(self.content)
        self.channel.harness.ledger.external_effects.append(
            {
                "kind": "message.edit",
                "channel_id": str(self.channel.id),
                "message_id": str(self.id),
                "content_hash": digest_text(self.content),
            }
        )
        return self

    async def add_reaction(self, emoji: Any) -> None:
        self.reactions.append({"mode": "add", "emoji": str(emoji)})
        self.channel.harness.ledger.external_effects.append(
            {
                "kind": "reaction.add",
                "channel_id": str(self.channel.id),
                "message_id": str(self.id),
                "emoji": str(emoji),
            }
        )

    async def remove_reaction(self, emoji: Any, user: Any) -> None:
        self.reactions.append(
            {"mode": "remove", "emoji": str(emoji), "user_id": str(user.id)}
        )
        self.channel.harness.ledger.external_effects.append(
            {
                "kind": "reaction.remove",
                "channel_id": str(self.channel.id),
                "message_id": str(self.id),
                "emoji": str(emoji),
                "user_id": str(user.id),
            }
        )


@dataclass(slots=True)
class FakeMessage:
    id: int
    author: FakeUser
    channel: "FakeChannel"
    content: str
    guild: FakeGuild | None
    attachments: list[FakeAttachment] = field(default_factory=list)
    mentions: list[FakeUser] = field(default_factory=list)
    reference: FakeReference | None = None
    webhook_id: int | None = None
    jump_url: str | None = None

    async def reply(
        self,
        content: str = "",
        *,
        mention_author: bool = False,
        file: Any | None = None,
    ) -> FakeSentMessage:
        del mention_author
        return await self.channel.send(content, file=file, reply_to=self.id)


@dataclass(slots=True)
class DeliveryDirective:
    outcome: DeliveryOutcome
    delay_seconds: float = 0.0


@dataclass(slots=True)
class HarnessLedger:
    durable_events: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    delivery_attempts: list[dict[str, Any]] = field(default_factory=list)
    delivery_failures: list[dict[str, Any]] = field(default_factory=list)
    external_effects: list[dict[str, Any]] = field(default_factory=list)
    ambiguous_effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_commits: dict[str, dict[str, Any]] = field(default_factory=dict)
    ephemeral: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PolicyDecision:
    rejection: str | None
    addressed: bool
    trigger_kind: str
    consequence: str
    contextual_match: dict[str, Any] | None

    @property
    def accepted(self) -> bool:
        return self.rejection is None and self.trigger_kind != "ignored"


@dataclass(slots=True)
class RuntimeResult:
    text: str = ""
    suppressed: bool = False
    outbound_attachments: list[Any] = field(default_factory=list)
    outbound_reactions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class FakeChannel:
    id: int
    harness: "DiscordHarness"
    guild: FakeGuild | None = None
    history_messages: list[Any] = field(default_factory=list)
    sent_messages: list[FakeSentMessage] = field(default_factory=list)
    delivery_script: deque[DeliveryDirective] = field(default_factory=deque)

    def queue_delivery(
        self, outcome: DeliveryOutcome | str, *, delay_seconds: float = 0.0
    ) -> None:
        self.delivery_script.append(
            DeliveryDirective(DeliveryOutcome(outcome), float(delay_seconds))
        )

    async def history(
        self, *, limit: int, before: Any | None = None
    ) -> AsyncIterator[Any]:
        values = self.history_messages
        if before is not None:
            for index, item in enumerate(values):
                if int(item.id) == int(before.id):
                    values = values[:index]
                    break
        for item in list(reversed(values))[: int(limit)]:
            yield item

    async def fetch_message(self, message_id: int) -> Any:
        for item in self.history_messages + self.sent_messages:
            if int(item.id) == int(message_id):
                return item
        raise LookupError(f"message {message_id} missing from fake channel {self.id}")

    async def send(
        self,
        content: str = "",
        *,
        file: Any | None = None,
        reply_to: int | None = None,
        operation_id: str | None = None,
    ) -> FakeSentMessage:
        directive = (
            self.delivery_script.popleft()
            if self.delivery_script
            else DeliveryDirective(DeliveryOutcome.SUCCESS)
        )
        if directive.delay_seconds:
            self.harness.clock.advance(directive.delay_seconds)

        fingerprint = self.harness.delivery_fingerprint(
            channel_id=str(self.id),
            content=content,
            file=file,
            reply_to=reply_to,
            operation_id=operation_id,
        )
        self.harness.ledger.delivery_attempts.append(
            {
                "kind": "delivery.attempt",
                "epoch": self.harness.epoch,
                "channel_id": str(self.id),
                "operation_id": operation_id,
                "fingerprint": fingerprint,
                "outcome": directive.outcome.value,
            }
        )

        if directive.outcome is DeliveryOutcome.REJECT:
            self.harness.ledger.delivery_failures.append(
                {
                    "kind": "delivery.failed",
                    "channel_id": str(self.id),
                    "fingerprint": fingerprint,
                    "error_type": "SyntheticDiscordRejected",
                }
            )
            raise DeliveryError("synthetic Discord rejection")

        sent = self.harness._commit_external_message(
            self,
            content=content,
            file=file,
            reply_to=reply_to,
            fingerprint=fingerprint,
            operation_id=operation_id,
        )
        if directive.outcome is DeliveryOutcome.DUPLICATE:
            self.harness._commit_external_message(
                self,
                content=content,
                file=file,
                reply_to=reply_to,
                fingerprint=fingerprint,
                operation_id=operation_id,
                duplicate_of=sent.id,
            )
        if directive.outcome is DeliveryOutcome.AMBIGUOUS:
            self.harness.ledger.ambiguous_effects[fingerprint] = {
                "channel_id": str(self.id),
                "message_id": str(sent.id),
                "operation_id": operation_id,
                "observed_at": self.harness.clock.now().isoformat(),
            }
            raise DeliveryAmbiguous(
                "synthetic acknowledgement loss after external delivery"
            )
        return sent


@dataclass(slots=True)
class FakeClient:
    user: FakeUser
    harness: "DiscordHarness"
    closed: bool = False

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.harness.channels.get(str(channel_id))

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        channel = self.get_channel(channel_id)
        if channel is None:
            raise LookupError(f"unknown fake channel {channel_id}")
        return channel

    def is_closed(self) -> bool:
        return self.closed


class DiscordHarness:
    """Offline Discord-shaped crash-test range.

    The harness models observable adapter contracts, not discord.py internals. Durable
    ledger state survives restart; ephemeral state is reset.
    """

    def __init__(
        self,
        *,
        allowed_users: Iterable[str | int] = ("1001",),
        allowed_channels: Iterable[str | int] = (),
        allow_dms: bool = True,
        require_mention_or_reply: bool = True,
        bot_user_id: int = 9000,
        controls: dict[str, Any] | None = None,
        clock: DeterministicClock | None = None,
        ledger: HarnessLedger | None = None,
    ) -> None:
        self.allowed_users = {str(item) for item in allowed_users}
        self.allowed_channels = {str(item) for item in allowed_channels}
        self.allow_dms = bool(allow_dms)
        self.require_mention_or_reply = bool(require_mention_or_reply)
        self.controls = dict(controls or {})
        self.clock = clock or DeterministicClock()
        self.ledger = ledger or HarnessLedger()
        self.users: dict[str, FakeUser] = {}
        self.channels: dict[str, FakeChannel] = {}
        self.bot_user = self.user(bot_user_id, "VESTIGIA", bot=True)
        self.client = FakeClient(self.bot_user, self)
        self.epoch = 1
        self._next_message_id = 50000
        self._armed_failpoints: dict[str, int] = {}

    def user(self, user_id: int, name: str | None = None, *, bot: bool = False) -> FakeUser:
        key = str(user_id)
        if key not in self.users:
            self.users[key] = FakeUser(int(user_id), name or f"user-{user_id}", bot)
        return self.users[key]

    def channel(self, channel_id: int, *, guild_id: int | None = None) -> FakeChannel:
        key = str(channel_id)
        if key not in self.channels:
            self.channels[key] = FakeChannel(
                int(channel_id),
                self,
                guild=FakeGuild(int(guild_id)) if guild_id is not None else None,
            )
        return self.channels[key]

    def inbound(
        self,
        *,
        message_id: int,
        user_id: int,
        channel_id: int,
        content: str,
        guild_id: int | None = None,
        user_name: str | None = None,
        author_is_bot: bool = False,
        mention_bot: bool = False,
        reply_to_bot: bool = False,
        attachments: Iterable[FakeAttachment] = (),
    ) -> FakeMessage:
        author = self.user(user_id, user_name, bot=author_is_bot)
        channel = self.channel(channel_id, guild_id=guild_id)
        reference = None
        if reply_to_bot:
            prior = FakeSentMessage(
                self._allocate_message_id(), channel, self.bot_user, "prior resident reply"
            )
            channel.history_messages.append(prior)
            reference = FakeReference(prior.id, prior)
        message = FakeMessage(
            id=int(message_id),
            author=author,
            channel=channel,
            content=str(content),
            guild=channel.guild,
            attachments=list(attachments),
            mentions=[self.bot_user] if mention_bot else [],
            reference=reference,
            jump_url=(
                f"https://discord.invalid/channels/{guild_id or '@me'}/"
                f"{channel_id}/{message_id}"
            ),
        )
        channel.history_messages.append(message)
        self.ledger.durable_events.append(
            {
                "kind": "ingress.observed",
                "epoch": self.epoch,
                "message_id": str(message.id),
                "channel_id": str(channel.id),
                "guild_id": str(guild_id) if guild_id is not None else None,
                "user_id": str(author.id),
                "content_hash": digest_text(message.content),
            }
        )
        return message

    def policy_decision(self, message: FakeMessage) -> PolicyDecision:
        is_dm = message.guild is None
        rejection = discord_platform_rejection_reason(
            author_is_bot=bool(message.author.bot),
            author_is_self=message.author.id == self.bot_user.id,
            channel_id=str(message.channel.id),
            is_dm=is_dm,
            allowed_channels=self.allowed_channels,
            allow_dms=self.allow_dms,
        )
        if rejection is None and is_dm and str(message.author.id) not in self.allowed_users:
            rejection = "user_not_allowed"
        content = str(message.content or "").strip()
        addressed = guild_message_is_addressed(
            is_dm=is_dm,
            content=content,
            bot_is_mentioned=any(item.id == self.bot_user.id for item in message.mentions),
            replies_to_bot=bool(
                message.reference
                and getattr(message.reference.resolved, "author", None)
                and message.reference.resolved.author.id == self.bot_user.id
            ),
            require_mention_or_reply=self.require_mention_or_reply,
        )
        if rejection is not None:
            return PolicyDecision(rejection, addressed, "ignored", "ignore", None)
        trigger = discord_trigger_decision(
            is_dm=is_dm,
            content=content,
            addressed=addressed,
            author_allowlisted=str(message.author.id) in self.allowed_users,
            controls=self.controls,
        )
        return PolicyDecision(
            None,
            addressed,
            str(trigger["kind"]),
            str(trigger["consequence"]),
            trigger.get("match"),
        )

    def arm_failpoint(self, point: Failpoint | str, *, count: int = 1) -> None:
        self._armed_failpoints[Failpoint(point).value] = max(1, int(count))

    def checkpoint(self, point: Failpoint | str, **context: Any) -> None:
        name = Failpoint(point).value
        self.ledger.ephemeral.append(
            {"kind": "checkpoint", "epoch": self.epoch, "name": name, **context}
        )
        remaining = self._armed_failpoints.get(name, 0)
        if remaining <= 0:
            return
        if remaining == 1:
            self._armed_failpoints.pop(name, None)
        else:
            self._armed_failpoints[name] = remaining - 1
        self.ledger.durable_events.append(
            {"kind": "harness.crash", "epoch": self.epoch, "failpoint": name}
        )
        raise HarnessCrash(name)

    def commit_runtime_result(
        self, *, operation_id: str, message: FakeMessage, result: RuntimeResult
    ) -> None:
        self.checkpoint(
            Failpoint.BEFORE_RUNTIME_COMMIT,
            operation_id=operation_id,
            message_id=str(message.id),
        )
        self.ledger.runtime_commits[operation_id] = {
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
            "text_hash": digest_text(result.text),
            "suppressed": bool(result.suppressed),
            "attachment_count": len(result.outbound_attachments),
            "reaction_count": len(result.outbound_reactions),
            "committed_at": self.clock.now().isoformat(),
        }
        self.ledger.durable_events.append(
            {
                "kind": "runtime.committed",
                "epoch": self.epoch,
                "operation_id": operation_id,
                "message_id": str(message.id),
            }
        )
        self.checkpoint(
            Failpoint.AFTER_RUNTIME_COMMIT_BEFORE_DELIVERY,
            operation_id=operation_id,
            message_id=str(message.id),
        )

    async def deliver_runtime_text(
        self,
        *,
        operation_id: str,
        message: FakeMessage,
        text: str,
        maximum: int = 1900,
        allow_ambiguous_retry: bool = False,
    ) -> list[FakeSentMessage]:
        delivered: list[FakeSentMessage] = []
        for index, chunk in enumerate(chunk_text(text, int(maximum))):
            part_id = f"{operation_id}:text:{index}"
            fingerprint = self.delivery_fingerprint(
                channel_id=str(message.channel.id),
                content=chunk,
                reply_to=message.id if index == 0 else None,
                operation_id=part_id,
            )
            if fingerprint in self.ledger.ambiguous_effects and not allow_ambiguous_retry:
                raise DeliveryAmbiguous(
                    "refusing automatic retry of an externally ambiguous delivery"
                )
            try:
                sent = await message.channel.send(
                    chunk,
                    reply_to=message.id if index == 0 else None,
                    operation_id=part_id,
                )
            except DeliveryAmbiguous:
                self.ledger.receipts.append(
                    {
                        "kind": "delivery.receipt",
                        "status": "ambiguous",
                        "operation_id": part_id,
                        "fingerprint": fingerprint,
                    }
                )
                raise
            except DeliveryError as exc:
                self.ledger.receipts.append(
                    {
                        "kind": "delivery.receipt",
                        "status": "failed",
                        "operation_id": part_id,
                        "fingerprint": fingerprint,
                        "error_type": type(exc).__name__,
                    }
                )
                raise
            self.checkpoint(
                Failpoint.AFTER_DELIVERY_BEFORE_RECEIPT,
                operation_id=part_id,
                message_id=str(sent.id),
                fingerprint=fingerprint,
            )
            self.ledger.receipts.append(
                {
                    "kind": "delivery.receipt",
                    "status": "succeeded",
                    "operation_id": part_id,
                    "fingerprint": fingerprint,
                    "message_id": str(sent.id),
                }
            )
            self.ledger.ambiguous_effects.pop(fingerprint, None)
            self.checkpoint(
                Failpoint.AFTER_RECEIPT,
                operation_id=part_id,
                message_id=str(sent.id),
                fingerprint=fingerprint,
            )
            delivered.append(sent)
        return delivered

    def restart(self) -> None:
        self.client.closed = True
        self.ledger.durable_events.append(
            {"kind": "runtime.stop", "epoch": self.epoch, "at": self.clock.now().isoformat()}
        )
        self.ledger.ephemeral.clear()
        self.epoch += 1
        self.client = FakeClient(self.bot_user, self)
        self.ledger.durable_events.append(
            {
                "kind": "runtime.restart",
                "epoch": self.epoch,
                "at": self.clock.now().isoformat(),
            }
        )
        self.checkpoint(Failpoint.BEFORE_RESTART_RESUME, epoch=self.epoch)

    def delivery_fingerprint(
        self,
        *,
        channel_id: str,
        content: str,
        file: Any | None = None,
        reply_to: int | None = None,
        operation_id: str | None = None,
    ) -> str:
        marker = ""
        if file is not None:
            marker = str(getattr(file, "filename", getattr(file, "path", file)))
        canonical = json.dumps(
            {
                "channel_id": str(channel_id),
                "content_hash": digest_text(str(content)),
                "file": marker,
                "reply_to": str(reply_to) if reply_to is not None else None,
                "operation_id": operation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return digest_text(canonical)

    def external_count(self, fingerprint: str) -> int:
        return sum(
            1
            for item in self.ledger.external_effects
            if item.get("kind") == "message.send" and item.get("fingerprint") == fingerprint
        )

    def snapshot(self, *, include_content: bool = False) -> dict[str, Any]:
        channels: dict[str, Any] = {}
        for channel_id, channel in sorted(self.channels.items()):
            channels[channel_id] = {
                "guild_id": str(channel.guild.id) if channel.guild else None,
                "history_ids": [str(item.id) for item in channel.history_messages],
                "sent": [
                    {
                        "id": str(item.id),
                        "content": item.content if include_content else None,
                        "content_hash": digest_text(item.content),
                        "reactions": list(item.reactions),
                    }
                    for item in channel.sent_messages
                ],
            }
        return {
            "epoch": self.epoch,
            "clock": self.clock.now().isoformat(),
            "channels": channels,
            "runtime_commits": dict(self.ledger.runtime_commits),
            "ambiguous_effects": dict(self.ledger.ambiguous_effects),
            "receipts": list(self.ledger.receipts),
            "delivery_attempts": list(self.ledger.delivery_attempts),
            "delivery_failures": list(self.ledger.delivery_failures),
            "external_effects": list(self.ledger.external_effects),
            "durable_events": list(self.ledger.durable_events),
        }

    def _allocate_message_id(self) -> int:
        self._next_message_id += 1
        return self._next_message_id

    def _commit_external_message(
        self,
        channel: FakeChannel,
        *,
        content: str,
        file: Any | None,
        reply_to: int | None,
        fingerprint: str,
        operation_id: str | None,
        duplicate_of: int | None = None,
    ) -> FakeSentMessage:
        sent = FakeSentMessage(
            self._allocate_message_id(), channel, self.bot_user, str(content)
        )
        channel.sent_messages.append(sent)
        channel.history_messages.append(sent)
        self.ledger.external_effects.append(
            {
                "kind": "message.send",
                "epoch": self.epoch,
                "channel_id": str(channel.id),
                "message_id": str(sent.id),
                "reply_to": str(reply_to) if reply_to is not None else None,
                "fingerprint": fingerprint,
                "operation_id": operation_id,
                "duplicate_of": str(duplicate_of) if duplicate_of is not None else None,
                "content_hash": digest_text(str(content)),
                "file_marker": (
                    str(getattr(file, "filename", getattr(file, "path", file)))
                    if file is not None
                    else None
                ),
            }
        )
        return sent


async def execute_scenario(document: dict[str, Any]) -> dict[str, Any]:
    config = document.get("config") or {}
    harness = DiscordHarness(
        allowed_users=config.get("allowed_users", ["1001"]),
        allowed_channels=config.get("allowed_channels", []),
        allow_dms=bool(config.get("allow_dms", True)),
        require_mention_or_reply=bool(config.get("require_mention_or_reply", True)),
        controls=config.get("controls") or {},
    )
    messages: dict[str, FakeMessage] = {}

    for index, step in enumerate(document.get("steps") or []):
        op = str(step.get("op") or "")
        if op == "advance":
            harness.clock.advance(float(step.get("seconds", 0)))
        elif op == "arm_failpoint":
            harness.arm_failpoint(str(step["name"]), count=int(step.get("count", 1)))
        elif op == "delivery":
            harness.channel(
                int(step["channel_id"]),
                guild_id=(int(step["guild_id"]) if step.get("guild_id") is not None else None),
            ).queue_delivery(
                str(step["outcome"]),
                delay_seconds=float(step.get("delay_seconds", 0)),
            )
        elif op == "message":
            message = harness.inbound(
                message_id=int(step["message_id"]),
                user_id=int(step["user_id"]),
                channel_id=int(step["channel_id"]),
                guild_id=(int(step["guild_id"]) if step.get("guild_id") is not None else None),
                content=str(step.get("content") or ""),
                author_is_bot=bool(step.get("author_is_bot", False)),
                mention_bot=bool(step.get("mention_bot", False)),
                reply_to_bot=bool(step.get("reply_to_bot", False)),
            )
            messages[str(message.id)] = message
            decision = harness.policy_decision(message)
            for key, expected in (step.get("expect") or {}).items():
                actual = getattr(decision, key)
                if actual != expected:
                    raise AssertionError(
                        f"step {index}: expected {key}={expected!r}, got {actual!r}"
                    )
        elif op == "runtime_commit":
            message = messages[str(step["message_id"])]
            harness.commit_runtime_result(
                operation_id=str(step["operation_id"]),
                message=message,
                result=RuntimeResult(
                    text=str(step.get("text") or ""),
                    suppressed=bool(step.get("suppressed", False)),
                ),
            )
        elif op == "deliver_text":
            await harness.deliver_runtime_text(
                operation_id=str(step["operation_id"]),
                message=messages[str(step["message_id"])],
                text=str(step.get("text") or ""),
                maximum=int(step.get("maximum", 1900)),
                allow_ambiguous_retry=bool(step.get("allow_ambiguous_retry", False)),
            )
        elif op == "restart":
            harness.restart()
        elif op == "expect":
            actual: Any = harness.snapshot(include_content=True)
            for part in str(step["path"]).split("."):
                actual = actual[int(part)] if isinstance(actual, list) else actual[part]
            if actual != step.get("equals"):
                raise AssertionError(
                    f"step {index}: expected {step['path']}={step.get('equals')!r}, got {actual!r}"
                )
        else:
            raise ValueError(f"unknown harness operation: {op!r}")
    return harness.snapshot(include_content=bool(document.get("include_content", False)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline Discord recovery scenario")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--include-content", action="store_true")
    args = parser.parse_args(argv)
    document = json.loads(args.scenario.read_text(encoding="utf-8"))
    if args.include_content:
        document["include_content"] = True
    try:
        snapshot = asyncio.run(execute_scenario(document))
    except HarnessCrash as exc:
        print(json.dumps({"status": "crashed", "failpoint": str(exc)}, indent=2))
        return 86
    except DeliveryAmbiguous as exc:
        print(json.dumps({"status": "ambiguous_external_effect", "error": str(exc)}, indent=2))
        return 75
    except DeliveryError as exc:
        print(json.dumps({"status": "platform_rejected", "error": str(exc)}, indent=2))
        return 74
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)},
                indent=2,
            )
        )
        return 1
    print(json.dumps({"status": "passed", "snapshot": snapshot}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
