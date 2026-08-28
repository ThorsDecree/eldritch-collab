# Deterministic Discord Recovery Harness

Issue: #39

This harness is an **offline Discord-shaped crash-test range** for VESTIGIA Runtime. It is not a second Discord adapter and it does not claim to reproduce Discord's servers or `discord.py` internals.

Its purpose is to make adapter-facing state, fault timing, and restart behavior deterministic enough that maintainers can repeatedly exercise failure windows that are hard to reproduce against the live service.

## What it models

The harness provides:

- deterministic users, guilds, channels, messages, mentions, replies, attachments, and channel history;
- the real VESTIGIA pure policy helpers for platform ingress rejection, guild addressing, listening-trigger classification, and output chunking;
- direct tests against the real `discord_recent_context()` helper using fake channel history;
- synthetic delivery outcomes: `success`, `reject`, `duplicate`, `ambiguous`, and `delay`;
- a deterministic clock;
- stable synthetic message IDs and operation IDs;
- explicit crash checkpoints around Runtime commit and outward delivery;
- a durable harness ledger that survives restart;
- ephemeral state that is reset on restart;
- content-minimal snapshots by default;
- JSON scenario playback for manual maintainer testing.

## What it does not model

It does **not** claim:

- exactly-once Discord delivery;
- that a fake HTTP/gateway outcome perfectly represents Discord behavior;
- that synthetic delay represents real Discord timing;
- that it validates bot-token permissions, intents, gateway reconnects, Discord rate-limit headers, or Discord availability;
- that a simulated message proves a visible Discord client rendered the effect;
- that this first harness mock closes all of #39.

Live Discord remains the authority for platform behavior. This harness exists to make the Runtime side reproducible.

## Focused automated tests

From `VESTIGIA_Runtime/`:

```powershell
py -3.11 -m pytest tests/test_discord_harness.py -q
```

No Discord token or network access is required.

The focused suite currently probes:

- allowed/disallowed DMs;
- bot/self/channel rejection;
- mention and reply addressing;
- channel-history ordering;
- success, rejection, duplicate, and ambiguous outward delivery;
- explicit retry after ambiguity;
- crash before Runtime commit;
- crash after Runtime commit but before delivery;
- crash after external delivery but before success receipt;
- durable-versus-ephemeral restart behavior;
- DM/guild history separation;
- content-redacted diagnostic snapshots;
- JSON scenario playback;
- real `discord_recent_context()` behavior for `allowlisted_only`, `mentions_only`, `hidden`, and character budgets.

## Manual scenario deck

Successful restart:

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/restart_success.json
```

Ingress/addressing matrix:

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/policy_matrix.json
```

Chunking plus synthetic delay:

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/chunked_delayed_delivery.json
```

By default the printed snapshot stores hashes rather than synthetic message bodies. For local fixture debugging only:

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/restart_success.json --include-content
```

## Expected non-zero scenarios

### Ambiguous external effect

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/ambiguous_delivery.json
$LASTEXITCODE
```

Expected exit code: `75`.

This means the simulated external message **was committed by Discord**, but acknowledgement was lost. The durable ledger preserves that uncertainty, and the harness blocks automatic retry after restart unless the caller explicitly opts into an ambiguous retry.

### Crash after external delivery, before receipt

```powershell
py -3.11 tools/discord_harness.py tests/fixtures/discord_harness/crash_after_delivery.json
$LASTEXITCODE
```

Expected exit code: `86`.

This means an external effect exists, but the process stopped before the corresponding success receipt could be persisted.

## Failpoints

The current deterministic checkpoints are:

```text
before_runtime_commit
after_runtime_commit_before_delivery
after_delivery_before_receipt
after_receipt
before_restart_resume
```

The key sequence is:

```text
participant event
→ Runtime commit
→ outward Discord effect
→ success/failure receipt
```

Crash at every edge and inspect what remains after restart.

## Delivery semantics

Each fake channel has a FIFO delivery script. Python example:

```python
channel.queue_delivery("success")
channel.queue_delivery("reject")
channel.queue_delivery("ambiguous")
channel.queue_delivery("duplicate")
channel.queue_delivery("delay", delay_seconds=5)
```

If the script is empty, delivery succeeds.

### `ambiguous` is not `failed`

`reject` means the fake platform did not commit an external message.

`ambiguous` means the fake platform **did** commit the effect, but VESTIGIA cannot prove success because acknowledgement was lost.

Automatically retrying an ambiguous outward effect can create a duplicate visible message or reaction. The harness therefore refuses that retry unless the test explicitly authorizes it.

# Thor test matrix

The matrix below is intentionally wider than the first automated suite. It is the maintainer punch-list for evolving #39.

## Ingress and addressing

- allowed DM;
- disallowed DM;
- allowlisted guild user in allowed channel;
- non-allowlisted guild author;
- bot-authored event;
- self-authored echo;
- webhook message;
- guild message in wrong channel;
- mention-addressed message;
- reply-addressed message;
- unaddressed ambient message;
- `!` command when mention-or-reply is required;
- repeated identical external message ID;
- out-of-order message IDs;
- delayed gateway replay after restart.

## Ambient context

- `hidden`;
- `allowlisted_only`;
- `mentions_only`;
- `all_channel`;
- bot messages;
- resident's own prior reply;
- webhook history;
- non-allowlisted history remains data-only;
- maximum-character truncation;
- maximum-message count;
- failed channel-history read;
- DM history never appearing in guild history;
- guild history never appearing in DM history.

## Text delivery

- one chunk;
- exact max-length chunk;
- multiple chunks;
- newline-preferred split;
- space-preferred split;
- hard split;
- first chunk is a reply;
- later chunks are channel sends;
- rejection before any external effect;
- rejection on chunk 2 after chunk 1 succeeded;
- synthetic delay;
- duplicate platform effect;
- acknowledgement loss;
- crash after chunk N before receipt N;
- restart before retry;
- explicit retry of an ambiguous effect.

## Reactions

`FakeChannel.fetch_message()` and `FakeSentMessage.add_reaction()` / `remove_reaction()` are intentionally Discord-shaped enough to help drive focused reaction tests.

Probe:

- add Unicode emoji;
- remove Unicode emoji;
- target missing;
- wrong destination channel;
- duplicate add;
- remove after restart;
- resident/client identity missing;
- platform exception;
- success receipt versus failure receipt.

## Attachments

`FakeAttachment` provides deterministic bytes, filename, size, and `read()`.

Probe:

- accepted text suffixes;
- ignored binary suffix;
- 1 MB boundary;
- malformed UTF-8 replacement;
- duplicate content under different filenames;
- same filename with different bytes;
- image attachment provenance;
- failed read;
- attachment present during restart;
- attachment data absent from content-minimal diagnostic snapshots.

## Bells

Use the real `BellService` with a fake destination and place harness checkpoints around the production sequence.

Priority cases:

- crash before `mark_fired`;
- crash after `mark_fired`, before Runtime turn;
- crash after Runtime answer, before Discord send;
- ambiguous Discord send;
- crash after Discord send, before `answered` event;
- quiet-hours deferral survives restart;
- dormant Runtime does not ring;
- one-time bell does not duplicate after restart.

## Image jobs

Pair the real image-job store and fake image provider with fake channels.

Priority cases:

- job claimed, process dies before execute;
- execute succeeds, process dies before activity completion;
- job completed, process dies before resident notification;
- notification accepted but acknowledgement lost;
- notification succeeds, process dies before `mark_job_notified`;
- restart does not generate a second image;
- restart does not silently share a private generated artifact.

## Confirmation challenges

Exercise the existing durable challenge implementation across restart:

- pending challenge survives restart;
- expired challenge remains expired;
- consumed challenge remains consumed;
- wrong participant;
- wrong channel;
- wrong interface;
- wrong image/content hash;
- replay after success;
- challenge is not widened from DM to guild or vice versa.

## Listening controls

Probe:

- literal alias match;
- watch-phrase match;
- cooldown;
- queue-only consequence;
- invite-turn consequence;
- non-allowlisted author;
- listening event persisted before restart;
- accepted event is not replayed as a new invitation after restart.

## Rate limiting

Probe:

- just below limit;
- exact limit;
- one over limit;
- independent users;
- deterministic clock advance releases the window;
- restart behavior is documented honestly if limiter state is intentionally in-memory;
- contextual-listening event records `rate_limited` where required.

## Activity window

Probe:

- activity message created;
- edits succeed;
- edit fails but final reply survives;
- restart while work is active;
- old activity message is not mistaken for authoritative job state;
- resident-authored chalkboard note remains labeled as resident-authored status, not hidden reasoning.

# Recommended next integration seam

This mock deliberately stays outside production code. The next useful #39 refactor would make the production Discord event handling constructible instead of defining all handlers inside `run_discord()`.

A target shape could be:

```python
door = DiscordDoor(runtime=runtime, client=fake_or_real_client, config=config)
await door.on_message(fake_message)
await door.poll_bells_once()
await door.poll_image_jobs_once()
```

Then this harness can drive the **actual production handlers** while `run_discord()` remains only the thin `discord.Client` bootstrap.

That refactor should preserve existing behavior and be reviewed as a testability change. The mock itself does not justify silently changing production semantics.

# Acceptance philosophy

A recovery test should never infer success from silence.

Every interrupted operation should end in one of these inspectable states:

```text
not committed
committed, not delivered
delivered and receipted
failed and receipted
externally ambiguous
still pending by contract
```

If a test cannot determine which state applies, that uncertainty is itself a contract gap worth exposing.
