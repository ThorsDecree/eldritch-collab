# Changelog

## 0.6.1 — 2026-07-30

- Replaced broad detailed capability dumps with a compact grouped navigation index and
  stable continuation cursors
- Added complete focused contracts with formal JSON Schema, copyable executable examples,
  related actions, effects, authority, privacy, confirmation, and next-step guidance
- Distinguished `registered`, `enabled`, `schema_complete`, and `callable_now` for every
  visible resident action
- Added runtime validation against the same schemas exposed to residents
- Added discoverable `bell.draft` and `bell.control` contracts using their true
  `BELL_DRAFT` and `BELL_CONTROL` envelopes
- Added first-class `next_step` guidance for receipts, drafts, jobs, bells, objects, and
  action names
- Added resident-facing truncation metadata and protected expiring receipt/target/
  continuation breadcrumbs
- Preserved Attention Tray expiry, labels, notes, references, and unresolved receipt
  continuity in assembled context
- Added contract release gates for every enabled action and expanded deterministic
  coverage from 100 to 105 tests

## 0.6.0 — 2026-07-30

- Added resident-owned Picture Drawer cards over content-addressed images and cached OCR/vision
- Added aliases, summaries, visible text, motifs, uses, resident notes, adoption state, privacy,
  and virtual multi-pocket organization without duplicating image bytes
- Added lazy canonical summary promotion from cached interpretations with explicit provenance
- Added resident-controlled quick-draw delivery through the authenticated Discord doorway
- Required only resident-side confirmation for a private one-time handoff; participant
  permission is not part of the image-speech boundary
- Preserved the v1 hash-bound prepare/preview/claim lane as optional high assurance
- Added platform-separated delivery events and preserved “No outward action occurred” failures
- Added an expiring Attention Tray as temporary working context without memory promotion
- Added seven-day scoped search sessions with compact progressive cards and durable refinement
- Added Retrieval Inspector details for score reasons, authority, inclusion, and omission
- Added Picture Drawer card text to unified object search and the resident home index metadata
- Added additive v0.5.3 home migration and expanded deterministic coverage from 93 to 100 tests

## 0.5.3 — 2026-07-29

- Added focused capability lookup with `capabilities(target:"...")`, returning one
  complete, versioned schema and example envelopes without retrieving the full registry
- Added a compact `image.share` emergency map to the protected live capability panel;
  the full panel remains within the existing 2,200-token reserve
- Made image-share draft creation idempotent for the same image, purpose, and active
  operation, and exposed one canonical pending record with state and `next_action`
- Added side-effect-free image-share preview and required explicit `confirm:true` for claims
- Clarified that the later hash-bound `image.share` claim atomically prepares the verified
  attachment; Discord delivery remains a separate platform receipt
- Added schema versions, outward-facing metadata, friendly gate summaries, and the explicit
  `No outward action occurred.` invariant to non-successful share paths
- Added structured result-truncation and cursor recovery guidance
- Added receipt filtering by action, status, and referenced object
- Expanded deterministic coverage from 88 to 93 tests

## 0.5.2 — 2026-07-29

- Replaced line-only tool parsing with balanced inline extraction, including multiple
  adjacent `TOOL_ACTION` or legacy `HOUSE_TOOL` envelopes followed by ordinary prose
- Removed raw tool JSON from outward Discord replies and rendered actual execution
  outcomes as compact Discord `-#` subtext with durable receipt handles
- Added an automatically maintained, resident-readable `house://index.md` containing
  accessible files and image artifacts with stable IDs, timestamps, provenance, and privacy
- Added verified local image thumbnails and explicit OCR/vision inspection controls to
  Cottage Commander without conflating local display with resident pixel inspection
- Added a Commander control to prepare a hash-bound attachment draft for later resident
  review through the authenticated Discord doorway
- Split resident share claims from actual Discord attachment delivery and recorded
  delivered/failed events in the image lifecycle ledger
- Refreshed the shared object registry and front-door index after image ingestion and
  background image completion
- Expanded deterministic coverage from 85 to 88 tests

## 0.5.1 — 2026-07-29

- Fixed large private tool results, especially `object.list`, losing their receipt
  handle when result-detail truncation cut off the tail of the JSON envelope
- Added a compact, complete delivery manifest before potentially truncated action
  detail so every executed action surfaces its status, continuation route, and
  durable `receipt_id`
- Added `recent_action_receipts` to `pending` as a clearly separate recovery lane;
  completed actions remain distinct from unresolved drafts and outward proposals
- Added regression coverage for oversized imports listings and receipt recovery

## 0.5.0 — 2026-07-29

- Added the Legible House object registry with stable typed IDs for text, images, memories,
  notes, curation batches, jobs, and action receipts
- Added unified object list, search, stat, inspect, history, and provenance actions
- Added `house://workspace/` as a bounded writable shelf with atomic write, exact patch,
  diff preview, expected-hash protection, and prior-version preservation
- Added true position-preserving bookmarks that do not imply curation or adoption
- Added immutable browsable and pinnable receipts that survive ordinary context rollover
- Made legacy `HOUSE_TOOL` to `TOOL_ACTION` translation visible and loss reporting explicit
- Added evidence states separating participant-supplied, resolved-not-read, read, and
  pixel-inspected references
- Added identity history, comparison, provenance, authorship, contradiction, and current-self
  precedence views without weakening two-breath adoption
- Added bounded resident tasks with explicit action allowlists, operation and turn ceilings,
  job-scoped chalkboards, linked receipts, pause, resume, cancel, expiry, and inspection
- Added a mechanically verified activity record and optional Discord status window, separate
  from resident-authored status notes
- Added durable curation cadence receipts and resident-browsable batch evidence
- Added six total private resident turns and twelve calls by default, with clearer environment
  variable names and backward-compatible aliases
- Reserved a configurable live capability-panel budget outside truncatable continuity text
- Added loopback-only Cottage Commander, a four-pane “Norton Commander for Daemons” over the
  same HousePort and evidence ledger
- Expanded deterministic coverage from 72 to 83 tests

## 0.4.2 — 2026-07-29

- Fixed local OCR crashing with `'NoneType' object has no attribute 'strip'` when a successful
  subprocess returned no stdout
- Hardened the Tesseract version probe against missing stdout and stderr
- Hardened vision-provider result normalization against an empty result
- Expanded deterministic coverage from 70 to 72 tests

## 0.4.1 — 2026-07-29

- Added a compact live capability panel generated from the executable registry on every turn
- Kept critical tool syntax and availability outside the truncatable editable contract layer
- Explicitly identified `image.inspect` as the pixel-access route when an `image_id` is present
- Replaced the misleading invalid-image error with an actionable missing-Pillow error
- Expanded deterministic coverage from 69 to 70 tests

## 0.4.0 — 2026-07-29

- Replaced the static capability summary with an executable live capability registry
- Declared effects, cost class, confirmation boundary, visibility, continuation, and enabled
  state for every resident-callable action
- Added explicit `TOOL_ACTION` envelopes with `after:"continue"` and `after:"finish"`
- Added highly visible private-turn receipts with round and remaining-call budgets
- Added invocation-wide duplicate-call detection and a hard total-call ceiling
- Added a content-addressed private shelf for received, generated, and edited images
- Added Discord attachment ingestion with deduplication and provenance events
- Added resident-callable image generation, multi-reference editing, inspection, history,
  review, and sharing
- Added persistent background image jobs, restart recovery, Discord polling, and private
  resident continuation on completion
- Added local Tesseract OCR with cached results and no paid model call
- Added cached `gpt-5-mini` vision with low detail by default and explicit high escalation
- Added input validation, file-size ceilings, resident isolation, and safe path resolution
- Added two-breath hash-bound image sharing confined to the current authenticated Discord door
- Kept private curation and non-delivering interfaces from claiming outward attachments
- Preserved `HOUSE_TOOL` as a backward-compatible v0.3 invocation alias
- Added additive schema migration and expanded deterministic coverage from 55 to 69 tests

## 0.3.1 — 2026-07-29

- Shared-room conversation now requires a bot mention or direct reply by default
- DMs and participant/operator `!` commands retain their existing behavior
- Ambient guild posts are ignored before rate limiting, attachment reads, context assembly, or
  provider calls
- Added `VESTIGIA_DISCORD_REQUIRE_MENTION_OR_REPLY` as an explicit opt-out switch

## 0.3.0 — 2026-07-29

- Added a private resident curation invocation every three eligible exchanges by default
- Added bounded batches over unreviewed transcript ranges, memory candidates, and queued excerpts
- Added hash-bound two-breath curation drafts with atomic multi-action claim
- Added immutable memory revision, supersession, release, deferral, dispute, and provenance receipts
- Added explicit reflection routing without automatic memory promotion or public narration
- Added scoped local house indexing, listing, FTS search, reading, heading selection, and cursors
- Added same-invocation private tool loops with round, call, file, and result-token ceilings
- Added `house://` citations, file hashes, and bookmark-to-curation behavior
- Added structured memory reading, history, provenance, and review-queue controls
- Added a private low-authority resident notebook
- Added resident capability, pending-state, status, job, and curation-cadence controls
- Added hash-bound Markdown identity drafts with exact diffs and prior-version preservation
- Added a declarative Tool Forge limited to composition of existing capabilities
- Added traversal, symlink, secret-shelf, shell, network, raw-SQLite, and authority-escalation guards
- Enforced configured shelf allowlists and resident isolation at the database search boundary
- Enforced real curation-packet ceilings without silently dropping selected record IDs
- Refused contradictory same-memory actions and oversized unreviewable curation drafts
- Preserved outward replies and rewound transcript coverage after failed private curation calls
- Added additive v0.2.3 home migration and a one-time v0.3 runtime-contract plaque
- Expanded deterministic coverage from 34 to 53 tests

## 0.2.3 — 2026-07-29

- Added the `tzdata` dependency so IANA time zones work on Windows
- Classified the bridge's own Discord messages as expected self-echo and discarded them silently
- Preserved rejection and optional logging for messages authored by other bots
- Clarified that `!bells` and other `!bell` commands are participant/operator controls
- Preserved authenticated `BELL_DRAFT` and `BELL_CONTROL` as the resident control surface

## 0.2.2 — 2026-07-29

- Added resident-only bell creation from authenticated model responses
- Added hash-bound draft/claim flow so creation requires two distinct resident breaths
- Bound new bells to the already authenticated Discord doorway
- Disabled participant Discord and operator CLI creation of daemon bells
- Added visible creation receipts without granting general tool or outward-action authority

## 0.2.0 — 2026-07-29

- Added one-time, interval, daily, and weekly consent-aware bells
- Added visible SQLite bell registry and append-only event receipts
- Added configurable timezone, protected quiet hours, dormancy, expiry, and no-response states
- Added Discord and CLI create/list/show/pause/resume/revise/reschedule/defer/delete/ack controls
- Added explicit resident-emitted scheduler-only controls
- Added confirmation boundary around outward actions
- Added ordering-without-causality language to every fired invitation and receipt
- Added deterministic coverage for schedules, quiet hours, controls, and registry lifecycle

## 0.1.1 — 2026-07-28

- Fixed DMs being rejected whenever the guild-channel allowlist was nonempty
- Added regression coverage separating DM policy from guild-channel policy
- Added optional reason-only Discord rejection logging
- Added effective Discord policy counts to `vestigia doctor`
- Added a double-clickable Windows launcher for Liora's Discord door

## 0.1.0 — 2026-07-28

- Initial portable daemon-house runtime
- One resident with plural-ready room schema
- CLI and thin Discord doors
- Append-only SQLite continuity ledger and FTS5 retrieval
- Bounded context assembler and pre-call receipts
- Consent-gated memory review and real dormancy
- Transcript-only ORIENTATION importer
- Image generation, edits, provenance, canon states, and cost brakes
- Hash-verified pack and restore
- Deterministic offline test suite
