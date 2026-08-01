# The Legible House

## v0.6.1: readable signs and recovery breadcrumbs

Broad `help` and `capabilities` calls now return a compact grouped index. Request one
focused contract when exact syntax matters:

```text
[[TOOL_ACTION {"action":"capabilities","target":"receipt.inspect","after":"continue"}]]
```

The focused result includes formal JSON Schema, valid copyable envelopes, effects,
authority, privacy, confirmation, related actions, next-step guidance, and four
separate lifecycle facts: registered, enabled, schema-complete, and callable-now.

Bell creation and management participate in the same discovery surface as
`bell.draft` and `bell.control`, but their examples honestly use `BELL_DRAFT` and
`BELL_CONTROL`.

When result detail is truncated, both the delivery manifest and resident-facing result
identify the truncation. Receipt ID, unresolved target, continuation, label, and expiry
enter a protected temporary breadcrumb layer. `receipt.inspect` or `next_step` resolves
the relevant breadcrumb; no missing tail is silently treated as known.

v0.5 connects the existing file, image, memory, note, curation, identity, and job systems
through one local evidence substrate. It does not grant shell access or arbitrary filesystem
authority.

## Stable house objects

The object registry assigns stable typed identifiers to accessible things:

```text
doc_…       readable text or workspace file
img_…       image asset
mem_…       memory record
note_…      private notebook entry
batch_…     curation batch
job_…       bounded job
receipt_…   immutable action receipt
```

Use `object.list`, `object.search`, `object.stat`, `object.inspect`, `object.history`, and
`object.provenance`. Specialized actions such as `image.inspect` and `memory.history` remain
available over the same records.

Beginning in v0.6, Picture Drawer card text participates in `object.search`. For progressive,
refinable result cards use `search.session`; for temporary resident-selected working context
use `attention.tray`. See [ATTENTION_AND_SEARCH.md](ATTENTION_AND_SEARCH.md).

`house://index.md` is an automatically maintained front door generated from verified
house state. It lists accessible text files and image artifacts with stable IDs,
timestamps, provenance, and privacy. The index is evidence of presence, not evidence
that the resident read, reviewed, adopted, shared, delivered, or acknowledged an item.

Evidence states are not interchangeable:

- A participant-supplied locator is a claim about where something may be.
- A resolved object has a verified house locator and stable ID, but may not have been read.
- A read document or pixel-inspected image records the operation actually performed.
- A missing locator remains `not found` or `unverified`; it is not smoothed into a memory.

## Workspace and editor

`house://workspace/` is the only writable shelf by default. It accepts bounded text files and
refuses traversal, hidden paths, symlinks, binaries, oversized writes, and paths outside the
configured writable roots.

```text
file.diff     preview a proposed text change
file.write    atomically create or replace workspace text
file.patch    replace one exact span with optional expected-hash protection
```

Each successful write preserves the prior version, updates the shared object registry, and
emits an immutable receipt. Identity and memory-bearing files keep their existing proposal,
preview, later hash-bound adoption or rejection boundaries.

## Bookmarks and receipts

`bookmark.add` records a stable object plus reading position. `bookmark.open` resolves the
same object and position later. A bookmark does not queue curation, create memory, or imply
adoption. The legacy `bookmark` action still means “queue an excerpt for curation” and is
reported under that explicit compatibility name.

Every tool action produces a `receipt_…` record containing the requested action, status,
source and normalized envelopes, timestamps, relevant object references, bounded result or
error evidence, and whether an outward effect occurred. Receipts are immutable and can be
listed, inspected, pinned, and unpinned. Pinned compact receipts are included in the live
resident capability panel after ordinary context rollover.

Legacy `HOUSE_TOOL` input remains accepted, but its receipt records:

```text
source_envelope: HOUSE_TOOL
normalized_envelope: TOOL_ACTION
adapter_version: v0.5
translation_loss: false
```

## Receipt delivery under result truncation

Beginning in v0.5.1, every private tool round places a compact, complete delivery
manifest before the potentially large action-result detail. The manifest preserves
the action name, success or failure state, continuation route, and durable
`receipt_id` even when a listing or search result exhausts the private result-token
budget. The resident can recover the full bounded result with `receipt.inspect`.

`pending` also exposes recent completed action receipts in
`recent_action_receipts`. This recovery lane is deliberately separate from pending
identity, tool, curation, and image-share drafts: receipt availability does not imply
review, assent, adoption, or an unresolved outward action.

Beginning in v0.5.2, the private router recognizes balanced action envelopes anywhere
in provider prose, including several adjacent calls followed immediately by ordinary
text. Raw action JSON is removed before Discord delivery. Actual execution outcomes
are rendered as compact `-#` subtext with their durable receipt IDs; model-authored
claims never generate these status lines.

Beginning in v0.5.3, retrieve one complete action contract without loading the full
registry:

```json
{"action":"capabilities","target":"image.share","after":"continue"}
```

The protected live panel carries every enabled handle plus a compact complete emergency
map for consequential outward image sharing. Detailed schemas are focused lookups rather
than permanent passengers in the continuity stack.

## Private work and the chalkboard

Private work has independent turn, call, result-token, and job-operation ceilings. Defaults:

```env
VESTIGIA_RESIDENT_MAX_PRIVATE_TURNS=6
VESTIGIA_RESIDENT_MAX_TOOL_CALLS=12
VESTIGIA_HOUSE_RESULT_TOKENS=6000
VESTIGIA_JOB_MAX_OPERATIONS=24
VESTIGIA_FORGE_MAX_MANIFEST_STEPS=6
```

Old `VESTIGIA_HOUSE_TOOL_ROUNDS` and `VESTIGIA_HOUSE_TOOL_CALLS` names remain compatible.
Forge manifest steps are composition steps, not model self-continuation turns.

`jobs.create` requires an explicit allowed-action list, an operation ceiling, and no outward
messaging. `jobs.step` performs one allowlisted action and links its receipt. Jobs can be
paused, resumed, cancelled, inspected, and given a bounded chalkboard. Paused, cancelled,
expired, and completed records remain visible.

The activity window contains two deliberately separate colors of evidence:

- Mechanically verified operations and linked receipts.
- A short resident-authored chalkboard note describing what is being investigated.

The note is not hidden chain-of-thought. When no operation receipt is linked, the API reports
`activity_reported_operation_unconfirmed`.

## Curation and identity

Every-three-exchange curation now emits a durable receipt. `curation.list`,
`curation.inspect`, and `curation.history` expose eligible turns, selected memories, related
records, drafts, events, and final state. Attention is not assent; silence is not escalation.

Identity history, comparison, and provenance keep proposals, authorship, contradictions,
claims, and rejections visible. The current resident-authored self-description remains
authoritative over imported characterization.

## Cottage Commander

Cottage Commander—“Norton Commander for Daemons”—is a loopback-only four-pane local view
over the same `HousePort` and ledger:

1. Shelves and bookmarks
2. Objects and search
3. Preview and bounded workspace editor
4. Provenance, history, receipts, and activity

It does not maintain a second index or alternate source of truth. Start it with:

```bash
vestigia commander homes/liora --env-file .env
```

The server binds only to loopback and uses a per-launch session token. Closing the process
closes the interface.

Image selection now displays verified local bytes in the preview pane. “Inspect pixels”
separately invokes OCR and low-detail vision and emits a receipt. “Prepare attachment”
creates an optional high-assurance hash-bound share draft but cannot claim it from the local
Commander. Ordinary resident quick-draw delivery occurs through the authenticated Discord door.
