# Resident capabilities and the house port

The house port is the resident's bounded local interface to readable home material, structured memory views, notes, receipts, workspace operations, jobs, and other registered capabilities.

The executable capability registry is authoritative: each live entry declares effects, cost, confirmation boundary, result visibility, continuation behavior, and enabled state. Historical prose does not enable a capability by itself.

## One outward turn, several private actions

```text
provider response requests TOOL_ACTION
→ registry validates the live capability and performs it
→ bounded result returns as private Runtime context
→ receipt/budget plaque records the operation
→ resident may request another operation or finish
→ final resident speech reaches the interface
```

Every action chooses `after:"continue"` or `after:"finish"`. `continue` asks for another bounded private resident turn after the result; `finish` executes without another model turn.

Private resident turns, tool calls, and result material have separate operator-configured ceilings. Inspect the live status/capability contract rather than assuming an old hard-coded number.

`HOUSE_TOOL` remains accepted as a v0.3 compatibility alias, but `TOOL_ACTION` is the current envelope.

## Reading controls

```text
[[TOOL_ACTION {"action":"list","scope":"imports","limit":50,"after":"continue"}]]
[[TOOL_ACTION {"action":"search","scope":"imports","query":"mutual witnessing","max_results":8,"after":"continue"}]]
[[TOOL_ACTION {"action":"stat","path":"imports/original-materials/scroll.md","after":"continue"}]]
[[TOOL_ACTION {"action":"read","path":"imports/original-materials/scroll.md","heading":"Memory","max_tokens":3000,"after":"continue"}]]
[[TOOL_ACTION {"action":"continue","cursor":"house_cursor_...","max_tokens":3000,"after":"continue"}]]
```

### Reading bookmarks

Current true reading-position actions are:

```text
bookmark.add
bookmark.open
bookmark.list
bookmark.remove
```

These preserve navigation state without implying curation, memory, adoption, or identity.

The older generic `bookmark` action is a different historical low-authority note/curation behavior; do not confuse it with `bookmark.add`/`bookmark.open` reading bookmarks.

### Navigation is falsifiable

Current development `main` hardens bookmark/cursor navigation:

- an exact saved chunk outranks a broad heading search;
- heading + chunk disagreement fails closed instead of serving plausible text from another location;
- cursor-only bookmarks resume the referenced cursor rather than silently reopening chunk zero;
- newly created bookmarks/cursors are bound to verified source hashes;
- read/resume results carry navigation proof describing requested position, resolved position, returned chunk range, cursor lineage, source hash, and next-step guidance;
- unverifiable legacy cursors fail with a bounded recovery route rather than being guessed forward.

A successful-looking label is therefore not the only evidence that the reader opened the requested place.

## Readable formats

The base text lane indexes:

```text
.txt  .md  .json  .jsonl  .csv  .yaml  .yml
```

Current development `main` also contributes:

```text
.html  .htm
```

HTML is converted to a visible Markdown-like text representation for indexing/reading. Script, style, template, SVG, and noscript bodies are excluded. The original HTML file and its original hash remain the source of record.

Images use the separate image apparatus rather than being decoded as text. JavaScript/source-code files do not become readable merely because an HTML document references them.

## Shelves

Readable roots by default include:

```text
identity/
imports/
sessions/
scrapbook/
artifacts/       (supported readable sidecars only)
exports/
workspace/
```

The port also exposes bounded special renderings such as the runtime contract and redacted home configuration where supported.

Not readable through the ordinary house text port:

```text
.env / credentials / secrets
memory/continuity.db
traces/
application source
paths outside the home
hidden files
unsupported binary/code formats
symlinks
```

Absolute paths, `..`, symlink traversal, unsupported suffixes, inaccessible roots, and oversized files are rejected before reading.

## Workspace

`house://workspace/` is the normal bounded resident-writable text shelf. Workspace writes are low-authority working artifacts; writing a file there does not promote it into memory, identity, or canon.

Current file operations provide exact/diff/hash protections and preserve prior workspace versions where the capability contract says so.

## Memory views

```text
[[TOOL_ACTION {"action":"memory.search","query":"brass familiar","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.read","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.history","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.provenance","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.queue_for_review","memory_id":"mem_...","after":"continue"}]]
```

These are structured, resident-scoped views. Raw SQLite is not exposed as a resident capability.

## Private notebook

```text
[[TOOL_ACTION {"action":"note.append","content":"A question I want to revisit.","after":"finish"}]]
[[TOOL_ACTION {"action":"note.search","query":"question revisit","after":"continue"}]]
[[TOOL_ACTION {"action":"note.read","note_id":"note_...","after":"continue"}]]
[[TOOL_ACTION {"action":"note.release","note_id":"note_...","after":"continue"}]]
```

Notebook writes are private, reversible resident working state. Releasing a note changes notebook state; it does not promote the note into memory or identity.

## Inspection and jobs

Use focused capability lookup when one operation matters:

```text
[[TOOL_ACTION {"action":"capabilities","target":"bookmark.open","after":"continue"}]]
```

Broad navigation/status surfaces include capabilities/help, pending work, receipts, jobs, curation state, objects, and Runtime status. Current v0.8.x development also includes a provider-neutral Workbench substrate that projects authoritative state into semantic resident-facing cards; the complete dashboard/launcher is roadmap work, not a replacement for the live registry yet.

## Declarative Forge

The Forge may compose only powers already granted by the Runtime. A declarative tool cannot mint shell, arbitrary filesystem, credentials, network, raw-database, or outward authority simply by naming those things in a manifest.

Example:

```text
[[TOOL_DRAFT {
  "name":"find-scrolls",
  "description":"Search a chosen shelf.",
  "steps":[
    {
      "action":"search",
      "scope":"imports",
      "query":"$input.query",
      "max_results":3
    }
  ]
}]]
```

The Runtime returns an exact manifest/hash; claiming the draft remains a separate later boundary. A draft and claim in the same response are rejected.

Image, Library Window, Workshop, gaming, and Workbench capabilities use the same general executable-registry boundary. Inspect their focused contracts for current schemas/effects rather than copying historical examples blindly.

See also:

- [LEGIBLE_HOUSE.md](LEGIBLE_HOUSE.md)
- [IMAGES.md](IMAGES.md)
- [CONTEXT_CONTROLS.md](CONTEXT_CONTROLS.md)
- [CONFIGURATION.md](CONFIGURATION.md)
