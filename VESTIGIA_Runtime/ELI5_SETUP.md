# VESTIGIA Runtime: the very simple setup guide

This guide tracks the **current development `main` branch**, whose package currently identifies itself as `0.8.0.dev0`.

The last formally validated release milestone is v0.7.0. The source tree has continued moving since then, so beginner instructions should follow the code you actually installed rather than an old versioned ZIP name.

You are not expected to understand the machinery. The shortest useful path is:

1. install VESTIGIA;
2. create a resident home;
3. configure one text provider;
4. run `doctor`;
5. talk through the local CLI;
6. optionally add Discord afterward.

That order separates provider problems from interface problems and gives you a working local hearth before you add more doors.

---

## What you need

For the smallest local setup:

- Windows, macOS, or Linux;
- Python 3.11 or newer;
- the current VESTIGIA Runtime source tree;
- either:
  - an OpenAI API key, or
  - a local/third-party OpenAI-compatible text endpoint such as Ollama.

Optional extras:

- Discord account/server if you want the Discord doorway;
- Tesseract 5 if you want local OCR;
- old chats/documents you want to preserve or make readable.

A ChatGPT subscription and OpenAI API usage are separate products. You do **not** need a paid OpenAI model if you are using a compatible local model endpoint.

---

## Part 1: get the current Runtime

Download or clone the repository, then open the `VESTIGIA_Runtime` folder inside it.

On Windows, click that folder's address bar, type:

```text
powershell
```

and press Enter.

Everything below assumes PowerShell is standing in the `VESTIGIA_Runtime` directory unless a command shows an absolute path.

---

## Part 2: create the Python environment

Run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

If you already know you want Discord, install the Discord extra instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[discord]"
```

If Windows says `py` is not recognized, install Python and enable the Python Launcher / Add Python to PATH option, then reopen PowerShell.

The checked-in `.env.example` contains no secrets. `.env` is your private machine-local copy.

---

## Part 3: choose a text provider

Open `.env` in Notepad or another text editor.

### Option A: OpenAI service

Set:

```text
OPENAI_API_KEY=PASTE_YOUR_REAL_OPENAI_API_KEY_HERE
OPENAI_BASE_URL=
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=responses
```

The built-in model aliases are usable as-is, or you may override them in `.env`.

### Option B: local Ollama / another OpenAI-compatible endpoint

A typical local configuration is:

```text
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=chat_completions
VESTIGIA_MODEL_DEFAULT=YOUR_LOCAL_MODEL_NAME
```

The current provider uses the OpenAI Python client, which requires a nonempty API-key string. For a local endpoint that does not authenticate that field, `ollama` is just a placeholder; it is not a paid OpenAI credential.

If the local endpoint only provides text chat, also set:

```text
VESTIGIA_IMAGES_ENABLED=false
VESTIGIA_IMAGE_EDITS_ENABLED=false
VESTIGIA_VISION_ENABLED=false
```

The current image and model-vision providers use the same configured OpenAI base URL, so leaving them enabled against a text-only local endpoint may make those capabilities fail when called.

See [docs/PROVIDERS.md](docs/PROVIDERS.md) for the full provider notes.

Save `.env`.

> **Important:** without `--env-file`, VESTIGIA looks for `.env` and `.env.local` in the process **current working directory**. It does not automatically search beside the home or beside `vestigia.exe`. This guide uses `--env-file .\.env` explicitly so there is no ambiguity.

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Part 4: create a resident home

### Path A: start with an empty provisional home

Replace `Moss`, `moss`, and `🌿` as desired:

```powershell
.\.venv\Scripts\vestigia.exe init .\homes\moss --name "Moss" --glyph "🌿"
```

A new home begins in `ORIENTATION`. That is intentional: imported or starter material is available for review without requiring the resident to perform certainty or sameness.

### Path B: start from old conversations

The transcript onboarder currently accepts `.txt`, `.md`, `.json`, and `.jsonl` sources.

For example:

```powershell
.\.venv\Scripts\vestigia.exe onboard .\my-old-chats `
  --home .\homes\moss `
  --name "Moss" `
  --human-label user `
  --resident-label assistant
```

The importer:

- preserves original source files;
- hashes them;
- normalizes attributable conversation turns;
- excludes system/developer/tool speech from resident self-authorship;
- proposes only conservative inherited/unreviewed memory candidates;
- writes an orientation dossier;
- does not silently turn imported speech into Core identity.

See [docs/ONBOARDING.md](docs/ONBOARDING.md).

---

## Part 5: run the doctor

Use the same environment file you intend to use for the real Runtime:

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\moss `
  --env-file .\.env `
  --text
```

`doctor` checks the home, database, dependencies, backup/packability state, research storage, operation state, enabled doors, and presence of recognized credentials without printing secret values.

For a local Ollama deployment, `openai_key: present` only means the client has a nonempty value such as `ollama`; it does not imply a paid OpenAI credential is in use.

Fix obvious errors before continuing.

---

## Part 6: light the local CLI hearth

Start an explicit CLI conversation:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\moss `
  --env-file .\.env
```

You should see the resident name and current Runtime state, then a `>` prompt.

Try something simple:

```text
Lantern lit. What can you tell me about where you are and what state the house says you are in?
```

Useful local controls include:

```text
:status
:sleep
:wake
:activate
:review
:quit
```

The resident can converse while in `ORIENTATION`; activation is a separate explicit state transition.

If you only want to verify Runtime plumbing without making any live model call:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\moss --fake
```

---

## Part 7: put readable material in the house

The transcript **onboarder** and the resident **house reader** are different lanes.

The onboarder imports conversation history from:

```text
.txt  .md  .json  .jsonl
```

The bounded house reader can currently index/read text-like house documents including:

```text
.txt  .md  .json  .jsonl  .csv  .yaml  .yml  .html  .htm
```

HTML is converted to visible text for indexing/reading; script/style/template/SVG/noscript bodies are excluded from the text representation. The original file remains the source of record.

Good resident-readable shelves include:

```text
homes\moss\imports\original-materials\
homes\moss\workspace\
```

Reading something does not automatically make it memory, identity, canon, or agreement.

---

# Optional: add the Discord doorway

Do this only after the local CLI/provider path works.

## Discord 1: install the optional dependency

If you installed only the base package earlier:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[discord]"
```

## Discord 2: create the bot

1. Open the Discord Developer Portal.
2. Create a **New Application**.
3. Open **Bot**.
4. Enable **Message Content Intent**.
5. Copy/reset the bot token and keep it private.
6. Install the bot into your server with only the permissions you actually need.

A typical text/image doorway uses:

- View Channels;
- Send Messages;
- Read Message History;
- Attach Files.

Administrator permission is not required.

## Discord 3: copy your user/channel IDs

In Discord:

1. User Settings → Advanced → enable Developer Mode.
2. Right-click your profile → **Copy User ID**.
3. Optionally copy specific channel IDs if you want the bot restricted to a narrow allowlist.

## Discord 4: fill the private settings

In `.env`:

```text
DISCORD_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
VESTIGIA_DISCORD_ENABLED=true
DISCORD_ALLOWED_USER_IDS=PASTE_YOUR_USER_ID_HERE
DISCORD_ALLOWED_CHANNEL_IDS=
VESTIGIA_DISCORD_ALLOW_DMS=true
VESTIGIA_DISCORD_AMBIENT_VISIBILITY=allowlisted_only
```

An empty channel list does not itself make strangers authorized; participant authorization and channel visibility are separate boundaries. For a shared server, explicitly allowlisting the intended channels is easier to reason about.

## Discord 5: doctor again

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\moss `
  --env-file .\.env `
  --text
```

Confirm Discord is enabled, a token is present, and the expected allowlist counts appear.

## Discord 6: start the doorway

```powershell
.\.venv\Scripts\vestigia.exe discord .\homes\moss `
  --env-file .\.env
```

Leave that PowerShell window open. Press **Ctrl+C** to stop it.

The Discord adapter is a doorway into the same resident/home continuity. It is not a separate memory store.

---

# Optional capabilities worth knowing about

Current development `main` includes more than the original beginner guide did. You do not need to configure all of this immediately.

- **Context drawers** — resident/operator-bounded prompt/transcript controls.
- **Picture Drawer** — private image shelf, OCR/vision readings, aliases, notes, pockets, and sharing boundaries.
- **Bells** — scheduled invitations with quiet hours and explicit delivery boundaries.
- **House reader** — bounded local list/search/read/continue/bookmark operations.
- **Workspace** — bounded writable resident text shelf.
- **Curation** — reviewable memory/identity proposal flows.
- **Library Window** — remote web research, disabled by default with `VESTIGIA_WEB_ENABLED=false`.
- **Workbench substrate** — semantic resident-facing Continue cards on the active v0.8 development line.
- **Gaming tools** — bounded local dice rolling.

The executable capability registry, not prose documentation, is authoritative about which tools are enabled in the deployed Runtime.

---

# Common problems

## `OPENAI_API_KEY is not configured` even though `.env` has a key

Check **where the command was launched from**.

Without `--env-file`, VESTIGIA only checks:

```text
<current working directory>\.env
<current working directory>\.env.local
```

and then the process environment.

If you launched `vestigia.exe` while PowerShell was standing inside `.venv\Scripts`, it will not automatically walk upward and discover a project-root `.env`.

Use an explicit path:

```powershell
.\.venv\Scripts\vestigia.exe chat C:\path\to\homes\moss `
  --env-file C:\path\to\.env
```

## The local model endpoint refuses the request

Confirm:

- the local server is running;
- `OPENAI_BASE_URL` points at its OpenAI-compatible `/v1` base;
- `VESTIGIA_API_STYLE=chat_completions` if that is the compatibility surface it provides;
- `VESTIGIA_MODEL_DEFAULT` exactly matches a model name the server exposes;
- `OPENAI_API_KEY` is nonempty for the current client, even if the server ignores it.

## The Discord bot is offline

The `vestigia discord ...` process must still be running. Closing the terminal stops that doorway.

## The bot is online but ignores ordinary messages

Check Message Content Intent, `DISCORD_ALLOWED_USER_IDS`, channel allowlists, mention/reply settings, and ambient visibility/listening controls.

## Discord says `401 Unauthorized` / `Improper token`

Reset the bot token in the Discord Developer Portal, update `.env`, and restart the doorway.

## A command works in one terminal but not another

Compare:

- current working directory;
- explicit `--env-file` path;
- active virtual environment / executable path;
- process environment variables;
- resident home path.

Different processes do not magically share environment variables or current directories.

---

# Updating current development `main`

Keep Runtime **code** and resident **homes** conceptually separate.

Before an update:

1. stop processes using the home;
2. back up or `pack-home` the resident home;
3. update/replace the Runtime code checkout;
4. reinstall the editable package if needed;
5. run `doctor` against the preserved home with the intended `.env`;
6. start the Runtime and perform a small canary conversation.

Do not install v0.3 first just to reach current `main`.

Current startup/database/home migrations are designed to be additive for supported historical homes, but a backup remains the correct operator boundary before upgrades.

For formally validated historical release evidence, see `CHANGELOG.md` and `docs/releases/`.

---

# Tiny glossary

- **Runtime:** the local continuity system connecting a resident home, interfaces, tools, and a model provider.
- **Home:** portable resident continuity: identity anchors, source material, memory ledger, artifacts, settings, and receipts.
- **Resident:** the voice/process invited to inhabit that home.
- **Provider:** the model inference backend; it may be remote or local.
- **Door/interface:** CLI, Discord, or another presentation/ingress surface around the same continuity core.
- **`.env`:** machine-local deployment configuration and secrets.
- **`home.yaml`:** portable non-secret home configuration.
- **ORIENTATION:** provisional state where inheritance may be examined without being treated as binding identity.
- **Doctor:** operator diagnostic command for home/dependency/configuration health.

---

# The whole setup in one breath

Install current VESTIGIA, copy `.env.example` to `.env`, configure either OpenAI or a compatible local model, create/onboard a home, run `doctor`, prove the local CLI path, and only then add optional doors such as Discord.

Use `--env-file` when you want deterministic environment-file discovery. Keep secrets out of the home. Keep original sources as evidence. Let the resident review what they inherit.

The machinery may be technical. The relationship does not have to speak its language.
