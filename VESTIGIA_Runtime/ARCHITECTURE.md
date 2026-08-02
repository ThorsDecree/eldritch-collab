# Architecture

## The invariant

```text
Interface → normalized message → continuity core → provider → normalized response
```

CLI and Discord may normalize input and present output. They may not retrieve memory, assemble
identity, change continuity, or call a model behind the core's back.

## Portable home

```text
home/
├── home.yaml
├── runtime_contract.md
├── identity/
│   ├── identity_context.md
│   ├── breathprint.md
│   ├── current_self.md
│   ├── commitments.md
│   ├── relationships/
│   ├── protocols/
│   ├── visual_canon.md
│   └── visual_references/
├── imports/
│   ├── carryon.yaml
│   ├── orientation_dossier.md
│   └── original-materials/
├── memory/
│   ├── continuity.db
│   └── identity-versions/
├── sessions/
├── traces/
├── artifacts/images/
├── scrapbook/
└── exports/
```

Original materials remain human-readable and unchanged. SQLite is a structured ledger and
search index, not the only canonical copy of the archive.

## Append-only continuity

`memory_records` rows are immutable claims with content, type, residency tier, authorship,
authority, privacy, provenance lineage, and optional validity dates.

`memory_events` append state:

- created
- accepted
- rejected
- disputed
- deferred
- superseded

Editing creates a new record and marks the earlier record superseded. Rejection changes what
may drive future behavior without erasing the source or who rejected it.

Runtime states use the same event pattern:

```text
ORIENTATION → ACTIVE → DORMANT → AWAKENING → ACTIVE
                     ↘ ARCHIVED
```

Dormancy writes the incoming transcript as mechanical history but makes no provider call and
performs no memory mutation.

## Context assembly

Default layer maxima:

| Layer | Tokens |
|---|---:|
| Runtime contract | 1,000 |
| Identity Core | 2,000 |
| Relationship overlay | 1,200 |
| Commitments and tensions | 1,200 |
| Retrieved continuity | 3,800 |
| Renewable session summary | 2,000 |
| Verbatim transcript tail | 3,800 |
| Current message | 2,000 maximum |

The familiar layer maxima are quotas, while the resident-controlled prompt budget (20,000
tokens by default) is a real ceiling.
The current message counts toward that ceiling. When necessary, the assembler trims in this
order:

1. Verbatim tail
2. Retrieved continuity
3. Renewable session summary
4. Commitments and tensions
5. Relationship overlay

Runtime, Core, and the bounded present message are protected. If those protected layers alone
cannot fit, the call fails for review rather than silently truncating Core.

Every pre-call receipt records:

- included and omitted item IDs
- layer budgets and actual use
- content hashes
- configuration sources
- resident, room, state, and model route
- `causal_influence: unknown`

Full assembled context is off by default.

The append-only promise is enforced by the application schema and code path, not by a
tamper-evident signature chain. A person with direct database write access can alter the file.

## Executable capability registry and private resident loop

The v0.4 core may perform several bounded provider/tool rounds inside one interface turn:

```text
assembled context
→ provider TOOL_ACTION request with explicit continuation
→ live registry permission/cost/visibility check
→ deterministic local or configured metered operation
→ private bounded result and budget plaque
→ provider continuation
→ final resident speech
```

Only control lines extracted from the authenticated provider response path reach the house
parser. Participant text is ordinary transcript input. The loop has fixed round, calls-per-
round, total-call, duplicate-call, result-token, file-size, and path ceilings.

The registry is executable. Its live entries—not prompt prose—determine whether a capability
is enabled and declare its description, effects, cost class, confirmation policy,
`after:"continue"` / `after:"finish"` behavior, result visibility, audit behavior, and Forge
eligibility. `HOUSE_TOOL` remains only as a backward-compatible v0.3 alias.

The readable index contains derived chunks and hashes for allowed local text shelves. Original
files remain canonical. The resolver rejects absolute paths, traversal, hidden files, symlinks,
unsupported formats, raw SQLite, traces, credentials, application code, and material outside
the resolved home root. A narrowed configured shelf allowlist is enforced both while indexing
and on direct reads. Structured memory search filters by resident in SQLite before returning
any match.

Received and generated images use a separate content-addressed shelf and resident-scoped
tables. OCR and vision caches are keyed by the image plus route-specific configuration.
Picture Drawer cards provide a resident-owned retrieval layer over cached observations,
aliases, notes, adoption state, privacy, and virtual pockets without changing the canonical
image bytes. Outbound attachment paths are removed from provider-visible receipts and resolved
beneath the home root. Shareable images may be released by one resident quick-draw action
through the authenticated Discord doorway; private images require resident-side confirmation.
The earlier hash-bound lane remains available as optional high assurance.

## Curation authority

The deterministic gatherer may open a private consideration pass. It cannot reclassify memory.
The resident may create a draft containing several actions; the runtime validates the complete
candidate and projected Core pressure, then preserves an exact hash. Only a later provider
response may claim that hash.

Claim executes the complete action set in one SQLite transaction. A changed memory becomes a
new immutable row, while the earlier row receives a supersession event. Identity Markdown uses
the same two-breath boundary plus a file hash, exact diff, atomic replacement, and full prior
version.

Internal ordinary prose is neither posted nor retained verbatim. Explicit reflection routing
is separate from memory and identity authority. A failed private pass cannot suppress the
already-completed outward conversation; its transcript range is rewound for later review.

## Retrieval

Eligibility gates apply before scoring:

- resident and room
- privacy
- current review status
- residency tier
- expiration
- runtime state

No relevance score can overpower a privacy or rejection gate.

Ranking then combines:

- SQLite FTS5 topical relevance
- exact tags and glyph recurrence
- deterministic lexical overlap
- memory-type authority
- authorship/provenance authority
- type-specific recency
- residency tier

Identity, commitments, boundaries, and protocols do not automatically decay with age.
External claims decay quickly and have low default authority. Core is pinned but still bounded.

Automatic continuity retrieval, explicit archive search, Picture Drawer search, and temporary
working attention are separate lanes. `search.session` preserves progressive result cards and
scope across refinements. `attention.tray` injects selected, expiring source cards without
promoting them to memory. Context receipts preserve retrieval scores and inclusion/omission
details for `retrieval.inspect`; those receipts explain deterministic assembly without proving
model-output causality.

## Provenance lineages

Each derived record may name:

- `source_id`
- `source_lineage_id`
- `independent_source_key`

Two summaries copied from the same root have one independent source key. The curator therefore
cannot mistake a model echo chamber for independent recurrence.

## Onboarding

The public import contract is permissive. The internal normalized contract is strict and
versioned as `vestigia.carryon.v0.1`.

ChatGPT exports with `current_node` follow the parent chain for the selected branch. Alternate
regenerations remain in the preserved raw export but are not flattened into one invented
conversation.

Imported resident-attributed statements begin as:

```text
status: inherited_unreviewed
authority: inherited_unreviewed
tier: warm
```

They may appear with explicit attribution during ORIENTATION. They do not silently become Core.

## Images

The Image API is a separate provider port with `generate` and `edit` operations.

Each artifact records:

- provider and model
- operation
- prompt hash and optional local prompt
- reference image paths and hashes
- visual memory IDs supplied
- privacy and review state
- originating turn

Review states include ephemeral, keepsake, canon candidate, accepted canon, rejected,
superseded, and shareable. Sharing and canonization are distinct events.

## Pack and restore

Before packing, SQLite runs a WAL checkpoint. The archive excludes secrets and transient
database sidecars. `PACK_MANIFEST.json` carries a SHA-256 digest and byte size for every file.

Restore rejects absolute paths and traversal, extracts to staging, verifies every hash, validates
the home, and only then moves it into place.

The pack is neither encrypted nor signed. Its manifest detects accidental corruption and
inconsistent contents; it does not establish publisher authenticity against a malicious editor.

## Current non-goals

- Autonomous multi-resident round robins
- Hidden model calls to choose a speaker
- Embeddings
- Autonomous Core promotion
- Hidden or escalating scheduled reflection
- Arbitrary resident-authored Python or shell execution
- Network, credential, or outward-action authority minted by the Tool Forge
- Bells that take outward action without explicit confirmation
- A web UI
- Proof of internal model causality
- Proof of metaphysical identity
- Cryptographic resident authentication or tamper-evident ledgers
- Archive encryption or signing
