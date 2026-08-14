# Discord door

Discord is an optional interface adapter. It does not own resident memory, context assembly, or provider behavior.

For a first install, prove the local CLI/provider path before adding Discord. See [../ELI5_SETUP.md](../ELI5_SETUP.md).

## Install

From the `VESTIGIA_Runtime` directory:

```bash
pip install -e ".[discord]"
```

On Windows without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[discord]"
```

## Configure

Put the bot token and participant/channel allowlists in your private `.env` (or another explicit environment file):

```dotenv
DISCORD_BOT_TOKEN=
VESTIGIA_DISCORD_ENABLED=true
DISCORD_ALLOWED_USER_IDS=123456789012345678
DISCORD_ALLOWED_CHANNEL_IDS=
VESTIGIA_DISCORD_ALLOW_DMS=true
VESTIGIA_DISCORD_AMBIENT_VISIBILITY=allowlisted_only
VESTIGIA_DISCORD_LOG_REJECTIONS=false
```

Multiple allowed user/channel IDs are comma-separated.

Discord may also be enabled portably in `home.yaml`:

```yaml
interface:
  default: auto
  cli:
    enabled: true
  discord:
    enabled: true
```

An environment value such as `VESTIGIA_DISCORD_ENABLED=true` overrides the corresponding `home.yaml` value for that process.

An empty user allowlist refuses startup. Guild messages obey the optional channel allowlist; DMs obey `VESTIGIA_DISCORD_ALLOW_DMS` independently. An empty channel allowlist permits an allowed participant in any guild channel the bot can read.

For shared servers, explicitly allowlisting the intended channels is easier to reason about than relying on visibility alone.

### `.env` location

Without `--env-file`, VESTIGIA reads `.env` / `.env.local` from the process current working directory. It does not automatically look beside the resident home or executable.

For deterministic launches, prefer:

```powershell
.\.venv\Scripts\vestigia.exe discord .\homes\moss --env-file .\.env
```

See [CONFIGURATION.md](CONFIGURATION.md).

## Run

```bash
vestigia run HOME --env-file PATH_TO_ENV
```

When Discord is enabled, `run` starts Discord instead of opening a competing interactive CLI prompt. Administrative CLI commands remain available from another terminal, but avoid running multiple long-lived Runtime processes that independently mutate the same home.

Explicit Discord ignores the default-door switch:

```bash
vestigia discord HOME --env-file PATH_TO_ENV
```

## Behavior

- allowlisted participant ingress;
- optional guild-channel allowlist;
- optional DMs;
- resident-controlled ambient visibility/listening within operator boundaries;
- recent Discord messages normalized as bounded ambient context;
- typing indicator during work;
- long-response chunking;
- small text-document uploads preserved under `imports/discord/`;
- addressed image uploads stored once on the private content-addressed image shelf;
- persistent resident image jobs with completion continuations;
- per-user sliding-window rate limiting;
- compact resident emoji reactions with separate delivery receipts.

Participant/operator controls include:

```text
!status
!sleep
!wake
!activate
!image <prompt>
!bells
!bell show|add|pause|resume|revise|reschedule|defer|delete|ack ...
```

Attach one or more images to `!image <prompt>` for a reference-aware edit when image editing is enabled. Image calls are explicit; the adapter does not generate art merely because it predicts that art would increase engagement.

The Discord process also hosts the consent-aware bell scheduler. See [BELLS.md](BELLS.md).

It also runs the image-job worker. A queued resident generation/edit survives restart, completes privately, and opens a new resident continuation with the result. Completion does not itself attach the image to Discord. Sharing remains a separate resident action/boundary.

## Intent requirement

The Discord bot must have Message Content Intent enabled to read ordinary message content.

## Shared-room conversational trigger

By default, authorized participants can open an ordinary conversational turn in a guild channel only by mentioning the bot or directly replying to one of its messages. DMs remain conversational, and `!` operator commands remain available in allowed guild channels.

Ambient room posts can still appear in the bounded recent-context packet when the resident is later addressed, depending on the resident's active source-visibility/listening controls. Ambient posts do not trigger provider calls merely by existing.

```text
VESTIGIA_DISCORD_REQUIRE_MENTION_OR_REPLY=true
```

Set this to `false` only when a room intentionally wants every authorized participant post to invoke the resident.

## Privacy and authority

Visibility is not authorization. Non-allowlisted ambient material may be visible under some resident-selected modes as untrusted/data-only context without gaining permission to trigger the resident or call capabilities.

Received/generated images are private artifacts by default. Discord delivery does not automatically make an image canonical, remembered, or broadly shareable; those are separate review/authority events.

## Routing diagnostics

Set:

```text
VESTIGIA_DISCORD_LOG_REJECTIONS=true
```

and restart the doorway. Rejected ingress prints a reason plus routing IDs, not message content. Turn it back off afterward if you do not want that metadata in the terminal.
