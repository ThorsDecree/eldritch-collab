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
DISCORD_BOT_TOKEN
```

The current text provider uses the OpenAI Python client for both OpenAI service calls and OpenAI-compatible endpoints. Consequently, live text mode currently requires a nonempty `OPENAI_API_KEY` client value even when the configured endpoint itself does not authenticate that value.

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
