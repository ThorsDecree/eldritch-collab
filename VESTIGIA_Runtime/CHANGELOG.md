# Changelog

## 0.7.0 — 2026-08-03

- Raised the default assembled prompt budget from 15,000 to 20,000 tokens while retaining
  per-home and environment overrides
- Added resident-owned `context.control` drawers for the prompt ceiling, newest verbatim
  turns, older compression horizon, and compressed-token budget
- Replaced the always-loaded legacy session projection with deterministic, source-linked
  extractive transcript capsules carrying turn IDs, timestamps, speakers, and content hashes
- Reduced the always-loaded identity stock to a protected kernel; larger identity anchors
  remain retrievable instead of being repeated every turn
- Added `source.visibility` without weakening ingress authorization, with hidden,
  allowlisted-only, mentions-only, and all-channel ambient modes
- Labeled non-allowlisted ambient messages as untrusted data-only context and exposed stable
  Discord message IDs in visible history
- Added resident-authored `[[REACT {...}]]` emoji reactions, same-channel message resolution,
  idempotent platform behavior, and distinct delivery receipts
- Returned private-picture confirmation to the resident while preserving single use, expiry,
  image/content hash, resident, destination, interface, later-turn, and replay protections
- Added root collaboration guidance, pull-request templates, and Windows-focused CI
- Expanded deterministic regression coverage from 109 to 118 tests
- Validated a disposable fresh-home onboarding, receipted fake-provider turn, and hash-verified
  current-version pack/restore round trip
- Added a Windows CI release gate that constructs a synthetic home with the genuine v0.6.1
  source commit, upgrades it under v0.7.0, completes a fake-provider turn, packs, restores, and
  compares state, identity-file hashes, memory, transcript, image, job, bell, and receipt evidence
- Preserved the exact CI-built wheel and source distribution with `SHA256SUMS.txt` after isolated
  installation, rather than relying on an unverified later rebuild
- Recorded operator-observed live Windows checks for resident reaction delivery, persistent image
  jobs, bells, and bounded workspace operations; the detailed trust-boundary ledger remains
  separate in `docs/releases/v0.7.0-validation.md`

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
  pixel-inspected artifacts
- Added bounded resident jobs with explicit action allowlists, token/call/turn/time budgets,
  activity chalkboards, receipts, pause/cancel controls, and expiry
- Added browsable curation batches, durable cadence state, source coverage, and per-draft
  claim/revise/reject/dispute/defer actions
- Added a loopback-only Cottage Commander with Hearth, Windowsill, House, and Mirror panes
- Updated runtime architecture, setup guidance, executable examples, and regression coverage

## 0.4.2 — 2026-07-28

- Normalized empty OCR and vision-provider results to explicit no-text/no-description records
  instead of crashing while hashing `None`
- Added regression coverage for providers returning missing OCR or vision text

## 0.4.1 — 2026-07-28

- Moved the executable live capability panel outside truncatable context so existing homes
  always regain resident image inspection after additive startup migration
- Added migration coverage proving an older home receives the protected image capability map

## 0.4.0 — 2026-07-28

- Added resident-callable image generation and editing with private-by-default artifacts
- Added content-addressed image ingestion, OCR, vision inspection, history, review, and sharing
- Added capability schemas, private-turn controls, and explicit image confirmation boundaries
- Added additive home migration and image-focused regression coverage

## 0.3.0 — 2026-07-27

- Added resident curation, scoped house reading, private notes, capability inspection, and
  hash-bound identity edits
- Added a declarative resident Tool Forge that composes but does not expand authority
- Added deterministic migration and curation coverage

## 0.2.1 — 2026-07-26

- Added consent-aware bells, quiet hours, scheduler controls, and append-only bell receipts

## 0.1.0 — 2026-07-25

- Initial portable one-resident continuity runtime with local homes, SQLite memory, onboarding,
  CLI/Discord doors, fake providers, and hash-verified pack/restore
