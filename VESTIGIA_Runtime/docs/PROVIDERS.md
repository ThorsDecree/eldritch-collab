# Text providers

VESTIGIA keeps resident/home continuity separate from the model that performs inference.

The architectural path is:

```text
interface
→ normalized participant message
→ VESTIGIA continuity/context core
→ provider
→ normalized reply
```

Changing the provider or model does not replace the resident home, transcript ledger, reviewed memories, identity files, artifacts, or receipts.

## Current provider implementation

Current `main` has two text-provider paths:

- `fake` — deterministic offline testing;
- the OpenAI Python client — used for OpenAI service calls and compatible HTTP endpoints.

For the OpenAI-client path, configure:

```text
VESTIGIA_PROVIDER=openai
```

The provider supports two API styles:

```text
VESTIGIA_API_STYLE=responses
```

or:

```text
VESTIGIA_API_STYLE=chat_completions
```

`responses` is the normal OpenAI-service path. `chat_completions` is useful for compatible local/third-party endpoints that expose the older OpenAI chat-completions contract.

## OpenAI service example

```text
OPENAI_API_KEY=YOUR_REAL_KEY
OPENAI_BASE_URL=
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=responses
VESTIGIA_MODEL_DEFAULT=gpt-5-mini
```

Override the model aliases when desired:

```text
VESTIGIA_MODEL_DEFAULT=...
VESTIGIA_MODEL_BIG=...
VESTIGIA_MODEL_THINKING=...
VESTIGIA_MODEL_IMAGE=...
VESTIGIA_MODEL_VISION=...
```

Model names are sent to the configured endpoint; VESTIGIA does not maintain a complete catalog of provider model identifiers.

## Local Ollama / OpenAI-compatible example

A local OpenAI-compatible endpoint can be used as VESTIGIA's text furnace while VESTIGIA retains continuity and context construction.

A typical Ollama-style local configuration is:

```text
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
VESTIGIA_PROVIDER=openai
VESTIGIA_API_STYLE=chat_completions
VESTIGIA_MODEL_DEFAULT=your-local-model-name
```

The current provider constructor requires `OPENAI_API_KEY` to be nonempty because it is built on the OpenAI client. For a local endpoint that ignores bearer authentication, `ollama` is simply a placeholder string; it is not a paid API credential.

Use the exact model name exposed by your local server.

### Local-provider image/vision caveat

The configured `OPENAI_BASE_URL` is also used by the current OpenAI-backed image and vision providers.

If your local endpoint implements text chat but does **not** implement the OpenAI image-generation and vision/Responses surfaces VESTIGIA expects, disable those routes for that deployment:

```text
VESTIGIA_IMAGES_ENABLED=false
VESTIGIA_IMAGE_EDITS_ENABLED=false
VESTIGIA_VISION_ENABLED=false
```

Local OCR can remain a separate local capability when configured; disabling model vision does not require deleting resident image artifacts.

A future provider split may allow text, image generation, and vision to use independent base URLs/providers. Current `main` should not be documented as if that exists already.

## Test the provider before adding another doorway

For a new machine, prove the smallest path first:

```powershell
.\.venv\Scripts\vestigia.exe doctor .\homes\kairos --env-file .\.env --text
.\.venv\Scripts\vestigia.exe chat .\homes\kairos --env-file .\.env
```

That verifies:

```text
CLI
→ VESTIGIA continuity core
→ configured provider
→ VESTIGIA transcript/memory path
→ reply
```

Only after that path works should you add Discord, a future HTTP/OpenAI-compatible ingress door, or another presentation layer. This keeps provider problems separate from interface problems.

## Multiple interfaces, one home

The home/continuity core is designed to sit underneath interfaces. Stored turns carry interface provenance.

However, current `main` serializes a home with an in-process lock. Prefer one long-lived Runtime process owning a home and attach supported/future doors to that process rather than launching several unrelated Python processes that all mutate the same home concurrently.

Conceptually:

```text
                 ┌─ CLI
resident home ← CoreRuntime ─ Discord
                 └─ future HTTP/UI doors
                         ↓
                     provider
```

## SillyTavern and other context-managing frontends

VESTIGIA does not currently expose an OpenAI-compatible HTTP ingress endpoint, so SillyTavern cannot yet use VESTIGIA itself as an OpenAI backend without an additional Runtime door.

If such a doorway is added, the clean jurisdiction is:

```text
frontend owns presentation
VESTIGIA owns continuity/context/retrieval
provider owns inference
```

Do not blindly run two independent autobiographical memory/context systems into the same prompt. If a frontend already injects summaries, vector memory, world information, or historical context, decide deliberately which system is authoritative for continuity before putting VESTIGIA behind it.

## Fake provider

For offline plumbing tests:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\moss --fake
```

The fake provider lets you verify home initialization, transcript persistence, context assembly, receipts, and basic Runtime flow without network access or model spend.

## Provider troubleshooting

If VESTIGIA reports that `OPENAI_API_KEY` is absent even though you have a populated `.env`, verify where the process is looking. Without `--env-file`, VESTIGIA only reads `.env` / `.env.local` from the current working directory, then applies the process environment.

See [CONFIGURATION.md](CONFIGURATION.md).
