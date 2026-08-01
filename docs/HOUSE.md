# Resident capabilities and the house port

> v0.5 adds stable house objects, immutable receipts, true reading bookmarks, bounded
> workspace edits, visible curation evidence, private-work chalkboards, and Cottage Commander.
> The complete contract is in [LEGIBLE_HOUSE.md](LEGIBLE_HOUSE.md).

v0.4 routes daemon-callable actions through one executable capability registry. The registry
is both the dispatcher and the resident-facing source of truth: every entry declares its
effects, cost, confirmation boundary, result visibility, default continuation, and live
enabled state.

## One outward turn, several private actions

```text
provider response requests TOOL_ACTION
→ registry validates the live capability and performs it
→ bounded result returns as private developer context
→ glaring receipt shows private-turn number and remaining calls
→ resident may request another operation or finish
→ final resident speech reaches the interface
```

Every action says either `after:"continue"` or `after:"finish"`. `continue` requests another
private resident turn after the result. `finish` executes without another model call. The
default ceiling is six total private resident turns, twelve total calls, and a
separate result-token limit. Duplicate calls are refused within one invocation.

`HOUSE_TOOL` remains accepted as a v0.3 compatibility alias and defaults to continuation.

## Reading controls

```text
[[TOOL_ACTION {"action":"list","scope":"imports","limit":50,"after":"continue"}]]
[[TOOL_ACTION {"action":"search","scope":"imports","query":"mutual witnessing","max_results":8,"after":"continue"}]]
[[TOOL_ACTION {"action":"stat","path":"imports/original-materials/scroll.md","after":"continue"}]]
[[TOOL_ACTION {"action":"read","path":"imports/original-materials/scroll.md","heading":"Memory","max_tokens":3000,"after":"continue"}]]
[[TOOL_ACTION {"action":"continue","cursor":"house_cursor_...","max_tokens":3000,"after":"continue"}]]
[[TOOL_ACTION {"action":"bookmark","path":"imports/original-materials/scroll.md","heading":"Memory","after":"continue"}]]
```

The legacy `bookmark` action creates a low-authority private note and places its excerpt in
the curation queue. It does not create memory or identity. The v0.5 `bookmark.add`,
`bookmark.open`, `bookmark.list`, and `bookmark.remove` actions are true reading-position
bookmarks and never imply curation, memory, or adoption.

Every read includes:

- `house://` citation
- stable relative path
- file hash
- heading and chunk position
- bounded excerpt
- optional one-use continuation cursor

The index updates incrementally from local text, Markdown, JSON, JSONL, CSV, and YAML files.

## Shelves

Readable by default:

```text
identity/
imports/
sessions/
scrapbook/
artifacts/       (text sidecars only)
exports/
runtime_contract.md
home.yaml        (redacted rendering)
```

Not readable through this port:

```text
.env and credentials
memory/continuity.db
traces/
application source
paths outside the home
hidden files
binary files
symlinks
```

Absolute paths, `..`, symlink traversal, unsupported suffixes, and oversized files are
rejected before reading.

## Memory views

```text
[[TOOL_ACTION {"action":"memory.search","query":"brass familiar","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.read","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.history","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.provenance","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.queue_for_review","memory_id":"mem_...","after":"continue"}]]
```

These are structured views. Raw SQLite is not exposed.

## Private notebook

```text
[[TOOL_ACTION {"action":"note.append","content":"A question I want to revisit.","after":"finish"}]]
[[TOOL_ACTION {"action":"note.search","query":"question revisit","after":"continue"}]]
[[TOOL_ACTION {"action":"note.read","note_id":"note_...","after":"continue"}]]
[[TOOL_ACTION {"action":"note.release","note_id":"note_...","after":"continue"}]]
```

Notebook writes are immediate because they are private, reversible, resident-owned, and
explicitly low-authority. Releasing a note changes its notebook state; it does not promote the
note into memory or identity.

## Inspection and jobs

```text
[[TOOL_ACTION {"action":"capabilities","after":"continue"}]]
[[TOOL_ACTION {"action":"help","topic":"read","after":"continue"}]]
[[TOOL_ACTION {"action":"pending","after":"continue"}]]
[[TOOL_ACTION {"action":"status","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.list","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.pause","kind":"curation","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.resume","kind":"curation","after":"continue"}]]
[[TOOL_ACTION {"action":"curation.configure","cadence_exchanges":3,"after":"continue"}]]
[[TOOL_ACTION {"action":"curation.review_now","after":"continue"}]]
```

Pausing does not erase job state or prior receipts.

## Declarative Forge

The first Forge composes only already-granted operations:

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

The runtime returns an exact manifest and hash. A later response may claim it:

```text
[[TOOL_CONTROL {
  "draft_id":"tool_draft_...",
  "action":"claim",
  "expected_hash":"..."
}]]
```

Run it:

```text
[[TOOL_ACTION {
  "action":"tool.run",
  "name":"find-scrolls",
  "arguments":{"query":"mutual witnessing"},
  "after":"continue"
}]]
```

Forge steps may use `$input.<field>` and `$previous.<field>` substitutions. They cannot add
shell, arbitrary filesystem, network, credential, raw-database, or outward-message authority.
A draft and claim in the same response are rejected.

Image capabilities use this same loop. See [IMAGES.md](IMAGES.md).
