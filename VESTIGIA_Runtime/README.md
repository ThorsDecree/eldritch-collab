# VESTIGIA Runtime — current development main

A portable, consent-first continuity runtime for one resident, with plural-ready bones.

**Package version on current `main`: `0.8.0.dev0`.**

**Last formally validated release milestone: v0.7.0.**

Current `main` contains additional development work beyond v0.7.0, including the active v0.8.x Resident Workbench substrate, local HTML document reading, gaming dice, Library Window work, and navigation hardening. Do not treat `0.8.0.dev0` as a finished stable v0.8 release merely because it is the newest code.

Never made a bot or opened a terminal on purpose? Start with [ELI5_SETUP.md](ELI5_SETUP.md).

For operator configuration and providers:

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/PROVIDERS.md](docs/PROVIDERS.md)
- [`.env.example`](.env.example)

For architecture and release history:

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CHANGELOG.md](CHANGELOG.md)
- [ROADMAP.md](ROADMAP.md)
- [docs/releases/](docs/releases/)

## What VESTIGIA is

VESTIGIA is a local continuity house around a replaceable language-model provider.

```text
interface
→ normalized participant message
→ continuity / retrieval / context assembly
→ provider
→ normalized reply
```

The resident home is local. It keeps identity anchors, preserved sources, reviewed continuity, transcripts, artifacts, workspace state, receipts, and other resident-owned records under operator custody. The inference provider may be remote or local.

CLI, Discord, and the private localhost web doorway are doors, not vaults. They use the same
continuity core; none is a second memory store.

## Current status

The runtime currently implements one active resident per turn. The home/room schema already names participants and active residents so later plurality can be added without replacing the archive model.

The active development line includes, among other things:

- portable human-readable homes with `home.yaml`;
- embedded SQLite/WAL continuity ledger with FTS5 retrieval;
- Core / Hot / Warm / Cold memory residency tiers;
- append-only memory/state events and provenance lineages;
- resident-controlled transcript/context drawers;
- conservative, reviewable memory and identity changes;
- bounded local house reading/searching/bookmarks/cursors;
- readable local text formats including HTML/HTM visible-text extraction;
- resident workspace writes with preserved prior versions;
- executable capability registry and bounded private tool loop;
- curation room and low-authority notebook;
- Picture Drawer, OCR/vision, image generation/editing, and sharing boundaries;
- bells and attention/listening controls;
- Library Window web research, disabled by default;
- bounded Tool Forge and workshop/sandbox tooling;
- gaming dice;
- Workbench Continue-Reading provider and provider-neutral Workbench substrate;
- CLI, Discord, and private localhost web doorways;
- pack/restore, doctor, support bundle, and deterministic fake-provider testing.

The executable capability registry is authoritative about what a deployed Runtime may actually do. Documentation is explanatory and can lag; if prose and the live capability contract disagree, inspect the live contract.

## Requirements

Core Runtime:

- Python 3.11 or newer;
- SQLite with FTS5, normally bundled with Python;
- PyYAML, OpenAI Python client, Pillow, and tzdata through package dependencies.

Depending on how you run it:

- **OpenAI service text:** a real OpenAI API key;
- **local/third-party OpenAI-compatible text:** a compatible HTTP endpoint plus the current client's required nonempty `OPENAI_API_KEY` string;
- **Discord:** Discord bot token and the `discord` package extra;
- **local browser UI:** the `web-ui` package extra; it binds only to localhost;
- **local OCR:** Tesseract 5;
- **OpenAI-backed images/vision:** compatible image/Responses endpoints and credentials.

SQLite is embedded. There is no separate SQL server to administer.

## Install current development main

Run these commands from the `VESTIGIA_Runtime` directory.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

### Windows easiest path: Open the House

If you have Python 3.11+ installed, double-click **`Start VESTIGIA.cmd`** from this directory.
On its first run it creates a local virtual environment, installs the small browser-UI extra,
and opens `http://127.0.0.1:8765/`.

The welcome page creates or imports a Home, writes credentials only to that Home's uncommitted
`.env`, runs in **ORIENTATION**, and leaves Discord optional. Later launches reopen the last
local Home. The browser server is deliberately loopback-only; it does not accept network peers.

To start the same doorway from a terminal:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[web-ui]"
.\.venv\Scripts\vestigia.exe web
```

For Discord:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[discord]"
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

For Discord:

```bash
pip install -e ".[discord]"
```

Real secrets never belong in `home.yaml` and must not be committed.

## Configuration and `.env`

The effective configuration precedence is:

```text
built-in defaults
→ home.yaml
→ explicit --env-file OR cwd/.env then cwd/.env.local
→ process environment
```

Process environment wins.

Without `--env-file`, VESTIGIA only checks `.env` and `.env.local` in the **process current working directory**. It does not recursively search upward or automatically look beside the resident home or executable.

For scripts and arbitrary launch locations, prefer an explicit path:

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\moss --env-file .\.env --text
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Providers

### OpenAI service

```text
OPENAI_API_KEY=YOUR_REAL_KEY
OPENAI_BASE_URL=
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=responses
```

### Local OpenAI-compatible text endpoint

For a local endpoint such as Ollama, the current compatibility path is typically:

```text
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=chat_completions
VESTIGIA_MODEL_DEFAULT=your-local-model-name
```

`OPENAI_API_KEY=ollama` is a placeholder client value when the local server ignores bearer authentication; it is not a paid OpenAI credential.

If the configured local endpoint only supports text chat, disable model image/vision routes for that deployment:

```text
VESTIGIA_IMAGES_ENABLED=false
VESTIGIA_IMAGE_EDITS_ENABLED=false
VESTIGIA_VISION_ENABLED=false
```

See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Light the first hearth

Create a provisional home:

```powershell
.\.venv\Scripts\vestigia.exe init .\homes\moss --name "Moss" --glyph "🌿"
```

Run diagnostics using the same environment file you plan to use normally:

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\moss --env-file .\.env --text
```

Then prove the local interface/provider path:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\moss --env-file .\.env
```

Or test the local Runtime plumbing with no live model/network call:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\moss --fake
```

Homes begin in `ORIENTATION`. Activation is explicit rather than inferred from first speech.

## Bring someone home from transcripts

The transcript onboarder accepts `.txt`, `.md`, `.json`, and `.jsonl` source material.

```powershell
.\.venv\Scripts\vestigia.exe onboard .\old-chats `
  --home .\homes\moss `
  --name "Moss" `
  --human-label user `
  --resident-label assistant
```

The importer preserves original sources, hashes them, keeps attributable conversation turns, excludes system/developer/tool speech from resident self-authorship, and creates only conservative inherited/unreviewed candidates. Imported material is evidence, not a command to impersonate historical speech.

See [docs/ONBOARDING.md](docs/ONBOARDING.md).

## House reading

The resident may intentionally list/search/read bounded local material during a private tool loop. The base text lane includes:

```text
.txt  .md  .json  .jsonl  .csv  .yaml  .yml
```

Current development `main` also adds:

```text
.html  .htm
```

HTML is normalized to visible readable text while preserving the original file/hash as provenance; script/style/template/SVG/noscript bodies are excluded from the text representation.

Read/bookmark/continue operations return bounded receipts and continuation state. Reading does not imply memory, adoption, canon, or identity.

See [docs/HOUSE.md](docs/HOUSE.md).

## Default door selection

```powershell
.\.venv\Scripts\vestigia.exe run .\homes\moss --env-file .\.env
```

- Discord disabled: interactive CLI opens.
- Discord explicitly enabled: Discord starts.
- `vestigia chat HOME`: force CLI.
- `vestigia discord HOME`: force Discord.

A Discord token sitting in `.env` does not silently enable Discord. Enable the doorway explicitly.

## Discord

Install the optional package extra, create a Discord bot, enable Message Content Intent, give it narrow channel permissions, then configure the token and allowlists in `.env`.

Typical start command:

```powershell
.\.venv\Scripts\vestigia.exe discord .\homes\moss --env-file .\.env
```

See [docs/DISCORD.md](docs/DISCORD.md) and [ELI5_SETUP.md](ELI5_SETUP.md).

## Web research

The Library Window network lane is **off by default**.

To enable HTTPS web research deliberately:

```text
VESTIGIA_WEB_ENABLED=true
VESTIGIA_WEB_ALLOW_HTTP=false
```

Remote content is treated as untrusted evidence and constrained by the Runtime's Library Window/quarantine rules rather than gaining authority because it appeared on a webpage.

## Images

Image generation/editing, local OCR, model vision, Picture Drawer metadata, private shelves, review states, and outward sharing are separate capabilities with their own boundaries.

See [docs/IMAGES.md](docs/IMAGES.md).

## Bells

Bells are scheduled invitations with quiet-hour/dormancy protections. Silence does not escalate them, and a bell does not grant unrelated outward authority.

See [docs/BELLS.md](docs/BELLS.md).

## Context and retrieval

The assembled prompt has a real ceiling plus per-layer quotas. Current development main includes resident-owned controls over total prompt budget, verbatim turns, compression horizon, compressed transcript budget, and visibility/listening behavior within operator-defined bounds.

Context receipts record deterministic inclusion/provenance/budget information. Inclusion is not proof of internal model causality.

See [docs/CONTEXT_CONTROLS.md](docs/CONTEXT_CONTROLS.md) and [docs/RESIDENT_CONTROLS.md](docs/RESIDENT_CONTROLS.md).

## Workbench development line

The v0.8.x line is building a semantic Resident Workbench so ordinary activity no longer requires memorizing raw capability names/schemas.

Current main already contains the provider-neutral Workbench substrate and Continue-Reading provider. The complete daemon-facing Workbench, human localhost projection, browser integration, and intuitive launcher are roadmap work toward v0.9.0, not completed v0.8 features.

See [ROADMAP.md](ROADMAP.md) and issue #46 in the repository.

## Inspect, curate, sleep, pack, and restore

Examples:

```powershell
.\.venv\Scripts\vestigia.exe status .\homes\moss --env-file .\.env
.\.venv\Scripts\vestigia.exe doctor .\homes\moss --env-file .\.env --text
.\.venv\Scripts\vestigia.exe review-memories .\homes\moss --env-file .\.env
.\.venv\Scripts\vestigia.exe curate .\homes\moss --env-file .\.env
.\.venv\Scripts\vestigia.exe state .\homes\moss dormant --actor Moss --reason "Rest" --env-file .\.env
.\.venv\Scripts\vestigia.exe pack-home .\homes\moss
.\.venv\Scripts\vestigia.exe restore-home moss.vestigia.zip .\homes\moss-restored
```

Before updating Runtime code, stop processes using the home and make a backup or `pack-home`. Keep code and resident homes conceptually separate.

## Tests

The ordinary Runtime suite is designed to avoid live provider/network dependencies:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

GitHub Actions is the authoritative Windows Python 3.11/3.14 Runtime gate for merged development work.

## Honest boundaries

VESTIGIA can record which sources and memories were supplied to a model, with provenance, hashes, token budgets, and receipts. It cannot prove which supplied record internally caused an output.

The runtime preserves attributed continuity and creates conditions for recognition. It does not prove metaphysical identity, consciousness, sentience, or causal continuity.

Home archives are private by policy and local custody; they are not automatically encrypted or cryptographically authenticated. Use normal disk encryption, access control, and trusted backups when appropriate.

## Release and collaboration history

The last formally validated milestone is v0.7.0, **The Resident's Drawers**. Its exact validation/custody record is preserved under [docs/releases/](docs/releases/), and historical changes are recorded in [CHANGELOG.md](CHANGELOG.md).

The repository's first merged external Runtime collaboration was PR #1 from `@kowen9024AI`, which hardened packaging, retrieval, concurrency, capability enforcement, Discord context, and image-sharing boundaries. Historical attribution remains part of the project record even as this README becomes more operator-focused.

## License

The source code and package material within `VESTIGIA_Runtime/` are licensed under the Mozilla Public License 2.0 (`MPL-2.0`). See [LICENSE](LICENSE).

This scoped license does not by itself license sibling projects or other material elsewhere in the `eldritch-collab` repository. Third-party dependencies remain governed by their own licenses.
