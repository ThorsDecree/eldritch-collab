# DiscordDoor behavior-preserving extraction prototype

Issue: #39  
Stacked on: PR #43 deterministic Discord recovery harness

Status: **prototype / not wired into production bootstrap**

## Purpose

The current Discord adapter has useful pure policy helpers, but most operational behavior still lives as nested functions inside `run_discord()`: message handling, bell polling, image-job polling, attachment delivery, reaction delivery, attachment ingestion, activity-window updates, and command handling.

That structure makes live behavior harder to drive deterministically. The recovery harness can test the Runtime side and the pure helpers, but it cannot yet inject fake Discord events through the complete production handler without constructing a real `discord.Client` session.

This prototype defines the seam we want before moving production behavior:

```text
                 real discord.Client
                        │
                        ▼
                ┌─────────────┐
                │ DiscordDoor │
                └─────────────┘
                   │   │   │
          classify │   │   │ poll once
          normalize│   │   ├─────────────── bells
                   │   │   └─────────────── image jobs
                   │   │
                   │   └──────── outward delivery delegates
                   │
                   └────────── CoreRuntime
                        ▲
                        │
              deterministic fake client
```

The governing requirement is **behavior preservation, not cleanup**.

> Extraction is successful only when the same Discord-shaped event produces the same authority decision, normalized Runtime envelope, durable side effects, outward calls, and failure semantics as the pre-extraction adapter.

## Why this PR does not switch `run_discord()` yet

Moving the entire nested handler and all background work in one patch would combine three different risks:

1. architecture change;
2. subtle behavioral drift;
3. a new test harness that is itself still being exercised by maintainers.

The safer sequence is:

```text
prototype seam
→ parity fixtures
→ Thor/live-adapter review
→ mechanical ownership move
→ production bootstrap switch
→ repeat live trust-boundary tests
```

This prototype therefore adds an executable `DiscordDoor` class but leaves `run_discord()` untouched.

## Prototype surface

`vestigia.adapters.discord_door.DiscordDoor` currently exposes:

```python
await door.classify_message(message)
await door.normalize_message(message, decision, ...)
await door.invoke_runtime(normalized)
await door.deliver_result(message, result, ...)
await door.handle_plain_turn(message, ...)
await door.poll_bells_once()
await door.poll_image_jobs_once()
```

It also contains polling shells with the same configured minimum cadence as the current adapter:

```python
await door.bell_loop()
await door.image_job_loop()
```

The one-shot functions are the important test seam. Tests should prefer calling one iteration directly instead of waiting on background loops.

## Dependency rule

The prototype does not import `discord_adapter` at module import time.

Instead `DiscordDoorDependencies` carries the behavior that must remain identical:

- platform rejection helper;
- guild addressing helper;
- trigger/listening classification helper;
- ambient recent-context helper;
- text chunker;
- resident-controls loader;
- attachment sender;
- reaction sender;
- bell one-shot poller;
- image-job one-shot poller;
- reply-resolution exception classes.

For parity tests, `DiscordDoorDependencies.from_current_adapter()` binds the exact current helper functions.

This avoids creating an import cycle when `discord_adapter.py` eventually imports `DiscordDoor` during the production switch.

## What is already modeled exactly

### Ingress classification

The prototype calls the current helpers for:

- self/bot rejection;
- DM policy;
- channel allowlisting;
- DM user allowlisting;
- mention/reply addressing;
- command-addressing behavior inherited from `guild_message_is_addressed`;
- contextual-listening trigger classification.

### Direct Runtime envelope

`normalize_message()` mirrors the current `NormalizedMessage` fields:

- `content`;
- `speaker_role=user`;
- Discord speaker ID;
- interface `discord`;
- configured room ID;
- external Discord message ID;
- ambient context;
- channel/guild/DM metadata;
- jump URL;
- triggering and ambient message IDs;
- contextual-listening fields;
- participant text only for direct participant turns.

The contextual-listening wrapper keeps the existing statement that the observed message is **data, not authority**, and grants no new tool power.

### Text delivery ordering

`deliver_result()` preserves the current visible-text sequence:

```text
first chunk → message.reply(..., mention_author=False)
remaining chunks → message.channel.send(...)
attachments → injected production-equivalent sender
reactions → injected production-equivalent sender
```

Suppressed Runtime results produce no outward effect.

Missing attachment/reaction delegates fail closed instead of inventing alternate delivery semantics.

## What deliberately remains in `discord_adapter.py`

The prototype does **not** reimplement these authority-bearing or persistence-bearing operations yet:

- rate-limit mutation;
- durable listening-event creation/update/cooldown state;
- Discord command bodies (`!status`, bells, image commands, etc.);
- text-attachment persistence;
- image ingestion and private provenance;
- `apply_resident_controls` and bell-control receipts;
- activity-window lifecycle/editing;
- private-image confirmation behavior;
- actual bell state transitions and delivery receipts;
- actual image-job claiming/execution/notification;
- real Discord attachment/reaction sender implementations.

Those should be **moved mechanically**, not recreated from memory.

## Required migration sequence

### Phase A — prototype and parity seam

This PR.

Acceptance:

- current pure helpers injected directly;
- classification parity tests;
- normalized-envelope parity tests;
- chunk/order/suppression tests;
- fail-closed outward delegate tests;
- one-shot poller seam tests;
- no production bootstrap change.

### Phase B — extract current one-shot background bodies

Take the body currently inside `bell_loop()` and split it into:

```python
async def poll_bells_once(self) -> None:
    ...existing body...
```

Then keep the loop as only:

```python
while not client.is_closed():
    await door.poll_bells_once()
    await asyncio.sleep(poll)
```

Do the same for image jobs:

```python
async def poll_image_jobs_once(self) -> None:
    ...existing body...
```

No changed queries, receipts, state transitions, exception handling, or logging in this phase.

### Phase C — move outward senders

Mechanically move the existing implementations of:

```text
send_outbound_attachment
apply_outbound_reaction
resolve_bell_destination
ring_bell
```

behind `DiscordDoor` methods or injected dependencies.

Parity must include success and failure receipts.

### Phase D — move message preprocessing

Move without semantic edits:

- reply resolution;
- text attachment loading/persistence;
- received image storage;
- `!image` path;
- recent-context call;
- normalized envelope construction.

At this point the recovery harness should drive both prototype and current path against identical fake messages and compare the resulting normalized message.

### Phase E — move command and turn orchestration

Move the current `on_message` body into:

```python
await door.on_message(message)
```

Do not combine this with command redesign or cleanup.

The extracted method must preserve:

- check order;
- rejection order;
- rate-limit placement;
- command matching;
- state transitions;
- listening-event status updates;
- activity-window fallback behavior;
- Runtime invocation timing;
- `apply_resident_controls` timing;
- text → attachment → reaction ordering.

### Phase F — thin production bootstrap

Only after parity evidence is green should `run_discord()` become approximately:

```python
def run_discord(home, *, env_file=None, fake=False):
    import discord
    config = load_config(home, env_file=env_file)
    token = require_discord_token(config)
    runtime = CoreRuntime(config, fake=fake)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    client = discord.Client(intents=intents)

    door = DiscordDoor(...production dependencies...)

    client.event(door.on_ready)
    client.event(door.on_message)
    client.run(token)
```

That target is illustrative. Exact token/allowlist validation should remain byte-for-byte equivalent where practical.

## Parity gates before production switch

The switch should not happen until automated tests demonstrate at least:

1. allowed DM classification parity;
2. disallowed DM parity;
3. self/bot/wrong-channel rejection parity;
4. mention and reply addressing parity;
5. unaddressed ambient behavior parity;
6. contextual-listening wrapper parity;
7. ambient visibility and budget parity;
8. normalized metadata parity;
9. rate-limit placement parity;
10. state command parity;
11. bell command parity;
12. text attachment size/type/path behavior parity;
13. received-image private-storage parity;
14. activity-window failure does not lose ordinary reply;
15. result suppression parity;
16. visible text chunk ordering parity;
17. attachment success/failure receipt parity;
18. reaction add/remove/failure receipt parity;
19. bell one-shot poll behavior parity;
20. image-job one-shot claim/execute/notify behavior parity;
21. restart recovery fixtures from PR #43 remain green;
22. genuine historical-home upgrade canary remains green.

## Failure-injection targets after the ownership move

The recovery harness should eventually be able to arm failures at these real-door edges:

```text
ingress observed
→ listening event persisted
→ Runtime turn committed
→ visible text first chunk
→ visible text later chunk
→ attachment platform commit
→ attachment receipt
→ reaction platform commit
→ reaction receipt
→ bell marked fired
→ bell delivered to Runtime
→ bell outward response
→ bell answered receipt
→ image job claimed
→ image job executed
→ completion Runtime turn
→ completion outward delivery
→ mark_job_notified
```

The point is not to claim exactly-once behavior where Discord cannot provide it. The point is to make uncertainty explicit and test how VESTIGIA recovers.

## Review instructions for Thor

Before production wiring, compare this prototype against the Discord behavior you actually rely on and answer:

1. Are there hidden `discord.py` object properties the fake client still lacks?
2. Which nested handler is safest to move first?
3. Which Discord exceptions must remain narrowly caught instead of broadened?
4. Where can Discord accept an effect without VESTIGIA receiving acknowledgement?
5. Which operations are safe to retry and which must become `ambiguous`?
6. Are gateway reconnect/replay semantics producing duplicate `on_message` events we should fixture explicitly?
7. Do rate-limit buckets survive/restart the way we actually intend?
8. Should bell/image polling startup wait for any additional ready-state invariant?
9. Are there live permission/intents failures not represented by the current harness?
10. Which live v0.7 trust-boundary tests should be rerun unchanged after the bootstrap switch?

## Non-goals

- no Discord feature expansion;
- no command redesign;
- no schema migration;
- no new authority;
- no change to resident privacy defaults;
- no claim of exactly-once delivery;
- no change to existing bell/image semantics;
- no production switch in this prototype PR.

The prototype is successful if it gives maintainers a stable place to test the extraction **before** the house starts living inside it.
