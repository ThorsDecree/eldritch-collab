# Runtime Context Sources

VESTIGIA Runtime can assemble ephemeral turn context from more than one read-only source without
turning those sources into memory stores or new authority domains.

The load-bearing invariant is:

```text
source evidence -> attributed prompt layer -> context receipt

not

source evidence -> automatic Runtime memory / identity / canon
```

## Built-in source: Runtime memory

The existing SQLite continuity retriever is now expressed as the required `runtime_memory`
ContextSource. Its scoring, accepted/inherited filtering, Core omission from the ordinary retrieval
layer, and evidence envelope remain the same compatibility behavior.

It produces the familiar `retrieved_continuity` layer and the context receipt continues to include
`retrieved_details` for Runtime-memory-specific inspection.

## Source contract

A ContextSource receives a bounded `ContextSourceRequest` containing the current query, resident,
room, runtime state, model route, turn ID, retrieval limit, and inherited-memory visibility state.

It returns a `ContextSourceResult` with:

- a normalized source name;
- a distinct prompt-layer name;
- the actual retrieval query used;
- attributed `ContextSourceItem` records;
- requested token budget;
- required vs optional status;
- source-level authority/advisory classification;
- explicit availability;
- explicit truncation state (`true`, `false`, or unknown);
- warnings and safe metadata.

Each item carries:

- stable item ID within the source result;
- bounded prompt text;
- provenance class;
- authority class;
- optional content hash / source reference;
- optional score / reasons;
- source-specific safe metadata.

ContextSource is a read/retrieval contract. It grants no memory write, adoption, canonicalization,
identity edit, or outward action authority.

## Runtime-owned ceilings

Optional/external sources are clamped by Runtime even when a source asks for more. Current default
per-source ceilings are:

```text
8 items
2400 tokens
```

The resulting context receipt records when Runtime applied an item or token ceiling.

Under the whole-turn context ceiling, advisory source layers are discarded/trimmed before the
required Runtime-memory retrieval layer. Protected identity/runtime/current-message layers retain
their existing treatment.

## Failure behavior

Required sources fail the context assembly if they cannot satisfy their contract.

Optional sources fail soft:

- the turn may continue;
- the source is recorded as `available=false`;
- the error type/warning is bounded into the context receipt;
- no absent result is silently presented as successful retrieval.

## Context receipt v0.2

Turn context receipts now include a source-neutral `context_sources` section in addition to the
existing layer and Runtime-memory details.

For each source it records:

- source/layer name;
- required/optional and available state;
- authority/advisory classification;
- query used;
- token budget;
- item count;
- included/omitted IDs where determinable;
- source truncation state and reason;
- warnings;
- per-item provenance and authority;
- safe source metadata;
- `memory_write_performed_by_assembler=false`;
- `adoption_or_canon_change=false`.

If the final whole-turn token cap trims a joined layer at a point where the exact item boundary is
no longer reconstructable, the receipt says so rather than guessing which source item crossed.

Prompt inclusion proves only that text was supplied to the provider context. It does not prove
model attention or causal use.

## Optional `VestigiaArchiveMcpSource`

Runtime includes an optional source named:

```text
vestigia_archive_mcp
```

It is disabled by default.

When enabled, Runtime starts the installed VESTIGIA MCP server as a local stdio child using:

```text
<current Python interpreter> -m vestigia_mcp.cli
```

The source uses only read-only Archive MCP tools. Current retrieval routes are:

- canonical resident-anchor lookup through `00_Bootloader/house_index.json`;
- bounded `archive.read_text` for registered resident breathprint/index anchors;
- deterministic query-term extraction;
- bounded literal `archive.search_text` calls.

The Archive MCP source is intentionally not a semantic-search claim. Literal search remains literal.

### Child-process environment boundary

The stdio child receives a newly constructed environment containing only what it needs for Archive
reads and MCP-owned receipts:

- live Archive root;
- optional snapshot root;
- dedicated MCP receipt directory under Runtime `traces/mcp-context-source`;
- deployment label;
- optional Archive read byte ceiling.

It does **not** inherit the parent environment wholesale. In particular the source does not forward:

- Runtime-home bridge configuration;
- OpenAI/provider credentials;
- Discord credentials;
- tunnel/control-plane credentials;
- arbitrary parent secrets.

This prevents the Archive context child from recursively acquiring Runtime capabilities merely
because the parent Runtime happens to have them.

The MCP child does write its own operational audit JSONL under the Runtime trace directory. Receipt
creation is not resident memory creation.

### Installation

Install Runtime with the optional client extra and install the VESTIGIA MCP package into the same
Python environment:

```powershell
cd VESTIGIA_Runtime
.\.venv\Scripts\python.exe -m pip install -e ".[mcp-context]"
.\.venv\Scripts\python.exe -m pip install -e "..\VESTIGIA_MCP_Server"
```

### Configuration

Minimum configuration:

```text
VESTIGIA_CONTEXT_MCP_ENABLED=true
VESTIGIA_CONTEXT_MCP_LIVE_ARCHIVE_ROOT=C:\path\to\VESTIGIA
```

To retrieve canonical resident anchors, supply the resident registry key exactly as it appears in
`00_Bootloader/house_index.json`:

```text
VESTIGIA_CONTEXT_MCP_RESIDENT_KEY=Liora
```

Optional controls:

```text
VESTIGIA_CONTEXT_MCP_SNAPSHOT_ARCHIVE_ROOT=C:\path\to\VESTIGIA\Anima.zip
VESTIGIA_CONTEXT_MCP_PREFIX=
VESTIGIA_CONTEXT_MCP_MAX_ITEMS=8
VESTIGIA_CONTEXT_MCP_MAX_TERMS=5
VESTIGIA_CONTEXT_MCP_TOKENS=2200
VESTIGIA_CONTEXT_MCP_ANCHOR_CHARS=12000
VESTIGIA_CONTEXT_MCP_TIMEOUT_SECONDS=30
VESTIGIA_CONTEXT_MCP_ARCHIVE_TEXT_MAX_BYTES=1000000
```

The source itself can request fewer than Runtime's external-source ceilings. Runtime may further
clamp it.

## Future `/context` introspection

The receipt substrate now contains the information needed for a resident-facing context view:

- which sources were configured/available;
- which query each source used;
- what each source offered;
- what crossed source and Runtime ceilings;
- provenance/authority classes;
- truncation and failure state;
- which layer each source occupied.

A dedicated resident-facing projection should read these receipts. It should not claim access to
hidden model attention or chain-of-thought.
