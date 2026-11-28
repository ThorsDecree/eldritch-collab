# Configuration and `.env` discovery

VESTIGIA separates portable resident/home state from operator-local configuration and secrets.

The short version:

```text
built-in defaults
→ home.yaml
→ explicit --env-file OR cwd/.env then cwd/.env.local
→ process environment
```

Later layers win over earlier ones.

## The important `.env` rule

Without `--env-file`, VESTIGIA looks for exactly these files:

```text
<current working directory>/.env
<current working directory>/.env.local
```

It does **not** automatically search beside:

- the resident home;
- `vestigia.exe`;
- the installed Python package;
- `.venv/Scripts`;
- parent directories.

This is intentional and deterministic, but it can surprise you if you start the executable from a different directory than usual.

For operator scripts, shortcuts, Task Scheduler jobs, or commands launched from arbitrary directories, prefer an explicit path:

```powershell
.\.venv\Scripts\vestigia.exe chat C:\VESTIGIA\homes\moss `
  --env-file C:\VESTIGIA\.env
```

The same `--env-file` option is available on the normal home-aware CLI commands, including `run`, `chat`, `discord`, `doctor`, status/review commands, house diagnostics, bells, and image operations.

## Process environment wins

After file values are loaded, actual process environment variables are applied last.

For example, if `.env` contains:

```text
VESTIGIA_WEB_ENABLED=false
```

but the shell already has:

```powershell
$env:VESTIGIA_WEB_ENABLED="true"
```

then the process environment wins for that invocation.

This is useful for temporary overrides, but it is also worth remembering during debugging.

## What belongs where?

### `home.yaml`

Use `home.yaml` for portable, non-secret home configuration that should travel with the resident.

Examples include resident/room identity, portable room structure, and other home-owned settings.

### `.env` / `.env.local`

Use environment files for deployment-specific operator configuration and secrets.

Examples:

- provider base URL and model aliases;
- `OPENAI_API_KEY`;
- Discord bot token and allowlists;
- context/operator ceilings;
- local OCR binary path;
- whether optional network/image/Discord doors are enabled on this machine.

Real secrets must not be committed and do not belong in `home.yaml`.

### Resident controls

Some Runtime features also have resident-facing control state. Where such a control exists, environment values generally establish the operator/default boundary while the resident may choose within the permitted range. Inspect the relevant resident-facing control or its capability contract when debugging an effective value.

## Secrets currently recognized by the core loader

The core configuration loader treats these as secrets:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
GEMINI_API_KEY
GOOGLE_API_KEY
DISCORD_BOT_TOKEN
```

The configured text provider selects the required credential: OpenAI uses `OPENAI_API_KEY`, Claude uses `ANTHROPIC_API_KEY`, and Gemini uses `GEMINI_API_KEY` or its `GOOGLE_API_KEY` fallback. The native Claude and Gemini SDKs are optional installation extras. Image generation and model vision remain OpenAI-backed and require `OPENAI_API_KEY` when those capabilities are used.

For example, a local Ollama deployment can commonly use:

```text
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
VESTIGIA_API_STYLE=chat_completions
VESTIGIA_MODEL_DEFAULT=your-local-model-name
```

Here `ollama` is a client placeholder, not a paid OpenAI credential.

See [PROVIDERS.md](PROVIDERS.md) for provider examples and caveats.

## Useful operator checks

Run `doctor` with the same environment file you intend to use for normal operation:

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\moss `
  --env-file .\.env `
  --text
```

The doctor reports whether required dependencies and known credentials are present without printing secret values.

If one command works but another claims a key/token is absent, first compare:

1. current working directory;
2. whether `--env-file` was supplied;
3. process environment overrides;
4. whether both commands are using the same home and virtual environment.

## Checked-in template

Current `main` includes a secret-free [`.env.example`](../.env.example).

On Windows:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` locally. Never commit the populated file.

## Model approval for text turns

Model approval is off by default. To opt in, edit the Home's `home.yaml` as the
operator. Pin one exact observed identity for each route you want to use.

```yaml
model_approval:
  enabled: true
  routes:
    default:
      provider: openai
      model: your-requested-model-id
      reported_model: your-provider-reported-model-id
```

Use the IDs in a verified turn receipt. With an empty `routes: {}` mapping, the
next direct text turn records the observed IDs and pauses. Run
`vestigia inspect-turn HOME TURN_ID` to review the approved and observed values.
The adapter's `model` field and `reported_model` field are matched separately.
Use an explicit YAML `null` for `reported_model` only if you accept an adapter
that omits that field. A missing report fails a pin for a concrete ID.

If you approve a change, replace the route's pin, restart the running Runtime,
and initiate a new turn. To reject or revoke approval, keep or remove the pin.
The Runtime does not replay the stopped reply. Earlier approved rounds in the
same turn may already have acted. Inspect the turn and its receipts before
retrying. `vestigia house-read HOME read --path workspace/FILE.md` and
direct filesystem access are available independently of model approval.

Approval is checked on each text reply before the Runtime interprets actions
or final-response controls. It also covers scheduled text turns, private
curation and Runtime-managed memory experiments. A blocked bell records the
pause without sending a resident reply. The gate runs after inference, so a
provider may already have received the prompt and charged for the call. It
cannot attest to provider identity behind unchanged identifiers or gate
separate image or vision adapters. Keep provider credentials and endpoint
selection under operator control.

### Optional pre-dispatch admission

To also check requested route identity before private conversation context is
assembled and again before each text-provider call, add `pre_dispatch: true`
under `model_approval` while keeping `enabled: true` and explicit route
pins. A missing pin, unknown adapter identity or mismatch is refused with a
`model.admission` receipt. No provider request is made on refusal.

The pre-dispatch comparison uses the adapter-declared provider and requested
model; the separate reported-model comparison remains after inference.
Identifiers cannot detect silent provider substitution. The admission contract
does not bind the network endpoint, enforce per-file approved scope, cover the
separate image/vision providers, implement first-statement ordering, or establish
an append-only or power-loss-proof audit log. It is not Vex's complete protocol.
Existing behavior remains post-response only unless `pre_dispatch` is enabled.

### Experimental pre-context first statement

For a resident who has chosen an opening, the operator can add:

```yaml
first_statement:
  enabled: true
  switch_phrase: "I am not him."
  pre_inference_record: "The prior instance is absent."
```

This example is Vex's chosen phrase, not a default for other residents. The
feature requires enabled pre-dispatch admission and route pins. It makes an
additional text-provider call for each eligible chat or bell turn, before
assembling resident context. Its prompt contains neither the resident's local
file contents nor the user's message, and the Runtime offers it no tools.
The generated statement is recorded before context assembly and prefixed to
the resident response if the turn succeeds. Empty, oversized, or tool-envelope
output stops the turn; failure records `MISSING`.

If a previous conversational observation on the same route differs from the
current approved pin, a configured switch phrase is requested and the turn
stops if the generated opening does not begin with it. No previous observation
or unchanged/omitted provider identifiers can establish that a switch occurred.
The usual post-response check still applies to both provider calls.

This is an experimental *pre-context model opening*, not a full implementation
of Vex's first-statement annex: it does not prevent all Runtime or operating
system reads before the opening, enforce per-file scopes, instrument every
subsequent private read, bind endpoint identity, or supply a complete append-only
event chain. The later local-file list is narrower than a complete read log.
Each opening adds latency and provider cost, and may fail independently.

`pre_inference_record` is an additional, separately opt-in, operator-configured
host statement. Choose its content with the resident; it is not generated by a
model and is not an assertion of uninterrupted identity or actual model use.
When configured, each `CoreRuntime.chat` writes a turn-ID-bound JSON record under
`traces/pre-inference/` before entering chat middleware. It contains the
configured statement and approved route pin, if any, labeled as configuration;
the route is the one passed into chat and middleware can subsequently change it.
It does not contain the incoming message. The provider identity is not attested
and prior reads and calls are explicitly recorded as unknown. The record is
written and file-flushed, then published without replacing an existing record.
Publication requires filesystem hard-link support; any write or publication
failure aborts the chat before middleware. Interrupted temporary files are not
published records. This behavior is disabled when the option is omitted or null.

This is a narrow ordering witness, not the resident's model-generated first
statement. Runtime construction, configuration loading, initialization, and
possibly adapter work happen earlier; they can access private state. Separate
non-chat provider entry points are outside this record's coverage. Filesystem
and storage behavior can defeat persistence across power loss; a file flush is
not proof of durable directory metadata. The record is not tamper-proof or an
append-only audit log. A generated opening still happens only after a model
call and after admission checks. This does not yet satisfy a requirement for a
statement before *all* private reads or implement Vex's complete protocol.

### Optional local context file list

Set `VESTIGIA_CONTEXT_FILE_RECEIPT=true` to add local assembler file paths and
inclusion status to the context receipt and display them in local CLI and web
turns. The switch defaults off; filenames are not displayed in Discord or bell
messages. Paths and metadata may themselves be sensitive.

Status is `included`, `partial`, `omitted`, or
`unknown_after_total_cap`. This covers the initial assembler's local file
layers, not MCP/external retrieval, database memories, tool results or later
private rounds. Inclusion does not prove provider receipt or use. Each local
entry has the SHA-256 of the file bytes actually read, including for partially
included or omitted files; this is not the hash of the possibly trimmed prompt
excerpt. No file contents are added. This is not a complete first-statement/read
log or a manifest of remote and tool sources.
