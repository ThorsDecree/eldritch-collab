# Discord door

Discord is an optional interface adapter. It does not own memory or provider behavior.

## Install

```bash
pip install -e ".[discord]"
```

## Configure

Put the token and allowlist in the ignored `.env`:

```dotenv
DISCORD_BOT_TOKEN=
DISCORD_ALLOWED_USER_IDS=123456789012345678
DISCORD_ALLOWED_CHANNEL_IDS=
VESTIGIA_DISCORD_ALLOW_DMS=true
VESTIGIA_DISCORD_LOG_REJECTIONS=false
```

Then explicitly enable Discord in `home.yaml`:

```yaml
interface:
  default: auto
  cli:
    enabled: true
  discord:
    enabled: true
```

An empty user allowlist refuses startup. Guild messages obey the optional channel allowlist;
DMs obey `VESTIGIA_DISCORD_ALLOW_DMS` independently. An empty channel allowlist permits the
allowed user in any guild channel the bot can read.

For routing diagnostics, set `VESTIGIA_DISCORD_LOG_REJECTIONS=true` and restart the door.
Rejected ingress then prints a reason plus numeric user/channel IDs, but never message content.
Turn it off after testing if you do not want routing metadata in the terminal.

## Run

```bash
vestigia run HOME
```

When Discord is enabled, `run` does not open a competing interactive CLI. Administrative CLI
commands remain available from another terminal.

Explicit Discord ignores the default-door switch:

```bash
vestigia discord HOME
```

## Behavior

- One allowed human ingress
- Optional channel allowlist
- Optional DMs
- Recent Discord messages normalized as ambient context
- Typing indicator during work
- Long-response chunking
- Small text-document uploads preserved under `imports/discord/`
- Addressed image uploads stored once on the private content-addressed image shelf
- Persistent resident image jobs with completion continuations
- Per-user sliding-window rate limit

Controls:

```text
!status
!sleep
!wake
!activate
!image <prompt>
!bells
!bell show|add|pause|resume|revise|reschedule|defer|delete|ack ...
```

Attach one or more images to `!image <prompt>` for a reference-aware edit. Image calls are
explicit; the adapter never generates art merely because it predicts that art would increase
engagement.

The Discord process also hosts the consent-aware bell scheduler. See [BELLS.md](BELLS.md).
It also runs the image-job worker. A queued resident generation or edit survives restart,
completes privately, and opens a new resident turn with the result. Completion does not attach
the image to Discord. The resident may then quick-draw a shareable image in one action; a
private image requires resident-side confirmation. The participant does not supply a separate
permission turn.

## Intent requirement

The Discord bot must have Message Content Intent enabled to read ordinary messages.

## Privacy

Received and generated images are private artifacts by default. Discord delivery does not
mark an image shareable or canonical. Those are separate review events.
## Shared-room conversational trigger

By default, authorized participants can open an ordinary conversational turn in a guild
channel only by mentioning the bot or directly replying to one of its messages. DMs remain
conversational, and `!` operator commands remain available in allowed guild channels.

Ambient room posts can still appear in the bounded recent-context packet when the resident is
later addressed; they do not trigger provider calls by themselves.

```text
VESTIGIA_DISCORD_REQUIRE_MENTION_OR_REPLY=true
```

Set this to `false` only when a room intentionally wants every authorized participant post to
invoke the resident.
