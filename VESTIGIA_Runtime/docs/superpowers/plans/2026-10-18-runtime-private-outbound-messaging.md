# Runtime Private Outbound Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable private curation turns to send one resident-approved plain-text Discord message through a finite, resident-accepted grant, with durable quota, cancellation, delivery, and uncertainty records.

**Architecture:** Add a focused `PrivateMessageService` and additive SQLite tables for grants, drafts, outbox items, and quota reservations. Register resident grant/draft/submit actions through Runtime composition, with a policy exception scoped only to `message.submit` in a later private-curation provider round. Add operator CLI grant/outbox management and a Discord worker that claims each queued item once, sends only to the grant-bound destination, and records definitive or uncertain outcomes.

**Tech Stack:** Python 3.11+, SQLite, pytest, existing Runtime capability/composition registries, `discord.py` optional adapter.

**Spec:** `VESTIGIA_Runtime/docs/superpowers/specs/2026-10-18-runtime-private-outbound-messaging-design.md`

## Global Constraints

- One Discord guild channel or one DM recipient per grant; grants require an active-from time, expiry, positive message quota, and resident acceptance of the exact canonical terms hash.
- Version one sends one non-empty UTF-8 text message per draft, with a maximum of 1,800 UTF-16 code units.
- No attachments, files, embeds, components, cross-posting, chunking, user/role/`@everyone`/`@here` mentions, or arbitrary destinations.
- Disable platform mentions and suppress automatic link previews at delivery; do not rewrite message text between draft hashing and delivery.
- Only a private curation invocation may draft or submit; submission must be a distinct later provider round and must present the exact draft ID/hash.
- Ingress allowlists, bells, ordinary conversation, image jobs, and bounded resident jobs do not grant or inherit outbound authority.
- Submission, quota reservation, and receipt creation are atomic; duplicate submissions cannot create duplicate outbox items; concurrent submissions cannot exceed quota.
- Cancellation and worker claim are serialized. Revocation cancels queued items; it cannot recall a claimed or delivered message.
- Definite pre-delivery failures and pre-attempt cancellations release quota; confirmed delivery and ambiguous attempts consume quota.
- Never automatically retry an ambiguous attempt. A stale `attempting` item becomes `delivery_uncertain` after restart.
- Receipts retain grant/draft IDs, hashes, destination, reservation, source turn identifiers, action/time, safe outcome, and Discord message ID when confirmed; never include private context, secrets, raw message body, or raw exception payload.
- The exact body remains only in protected draft/outbox storage while needed and is purged after terminal resolution; the audit hash remains.
- Use the existing Python dependency set; do not add a required dependency for Discord delivery.

## Review Focus

- A supplementary-plane emoji at the message length boundary must count as two UTF-16 code units; pin this in Task 1's validation tests.
- A malformed or stale terms/draft hash must fail before creating a reservation or contacting Discord; pin this in Tasks 1 and 2.
- A duplicate or concurrent submit at the quota boundary must produce at most one reservation/outbox item; pin this in Task 2's transaction tests.
- Cancellation racing with worker claim must have one serialized winner, with no send if cancellation wins and `too_late` if claim wins; pin this in Task 3's state-transition tests.
- A network timeout or crash after claim must retain honest uncertainty and never retry after restart; pin this in Task 3's adapter/recovery tests.

---

### Task 1: Message policy, schema, and grant/draft state service

**Files:**
- Create: `VESTIGIA_Runtime/src/vestigia/private_messages.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/db.py` — additive tables in `SCHEMA`; keep initialization backward-compatible.
- Test: `VESTIGIA_Runtime/tests/test_private_messages.py`

**Interfaces:**
- Produces: `PrivateMessageService(db: ContinuityDB, resident_id: str)`.
- Produces: `create_grant(destination_kind: str, destination_id: str, active_from: str, expires_at: str, message_quota: int, max_length: int = 1800) -> dict[str, Any]`, `get_grant(grant_id: str) -> dict[str, Any]`, `list_grants() -> list[dict[str, Any]]`, `revoke_grant(grant_id: str, actor: str) -> dict[str, Any]`.
- Produces resident grant operations `grant_action(grant_id: str, mode: str, terms_hash: str | None = None) -> dict[str, Any]`, where `mode` is `inspect|accept|decline|relinquish`; `decline` records a resident event and leaves the grant unusable.
- Produces `draft(grant_id: str, body: str, *, turn_id: str, provider_round: int) -> dict[str, Any]` and `inspect_draft(draft_id: str) -> dict[str, Any]`. Draft responses expose only ID, hash, measured length, grant, and state.
- The service owns table initialization through the existing additive database schema; all stored records are resident-scoped.

- [ ] **Step 1: Write failing tests** for canonical grant terms/hash, finite window and exact target validation, active-from/expiry/quota/length bounds, pending-only initial state, exact-hash acceptance, stale-hash rejection, decline/relinquish/revoke/expiry, and draft body validation.
- [ ] **Step 2: Run the focused tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_messages.py -q`. Expected: failures for missing service and schema.
- [ ] **Step 3: Implement the additive schema and service** in `db.py` and `private_messages.py`. Store a canonical terms JSON plus SHA-256; accept only that hash. Validate destination kind and ID, parse timezone-aware timestamps, require expiry after activation, positive quota, and a length limit in 1..1800. Count UTF-16 code units with `len(body.encode("utf-16-le")) // 2`; reject invalid Unicode, empty text, mention syntax, and disallowed controls while permitting tabs and line breaks. Hash exact UTF-8 bytes without normalization.
- [ ] **Step 4: Run focused tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_messages.py -q`. Expected: all policy/schema/state tests pass, including supplementary-plane boundary cases.
- [ ] **Step 5: Commit** as `feat: add private message grants and drafts`.

### Task 2: Resident capabilities, authorization, submit transaction, and operator grant CLI

**Files:**
- Create: `VESTIGIA_Runtime/src/vestigia/private_message_capabilities.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/bootstrap.py` — register the capability contribution through the established ordered composition plan.
- Modify: `VESTIGIA_Runtime/src/vestigia/capabilities.py` — add the named confirmation policy and narrowly scoped authorization rule.
- Modify: `VESTIGIA_Runtime/src/vestigia/runtime.py` — pass the current `house_tool_round` into action dispatch context.
- Modify: `VESTIGIA_Runtime/src/vestigia/cli.py` — add `vestigia message grant create|list|show|revoke`.
- Modify: `VESTIGIA_Runtime/src/vestigia/private_messages.py` — transactional submit/outbox/quota/cancel/reconcile operations.
- Test: `VESTIGIA_Runtime/tests/test_private_message_capabilities.py`
- Test: `VESTIGIA_Runtime/tests/test_private_message_cli.py`

**Interfaces:**
- Consumes Task 1's `PrivateMessageService`.
- Produces service calls `submit(draft_id: str, draft_hash: str, *, turn_id: str, provider_round: int, source_invocation: str) -> dict[str, Any]`, `cancel(outbox_id: str, actor: str) -> dict[str, Any]`, `inspect_outbox(outbox_id: str, *, include_body: bool = False) -> dict[str, Any]`, and `reconcile(outbox_id: str, outcome: str, actor: str, reason: str) -> dict[str, Any]`.
- Capability names are `message.grant`, `message.draft`, and `message.submit`. Their registered schemas must be complete formal object schemas and match the documented action fields.

- [ ] **Step 1: Write failing tests** for capability contracts and dispatch, private-curation/interface fencing, later-round-only submission, wrong-turn/wrong-round rejection, exact draft hash, atomic queue + reservation + receipt, duplicate submission idempotence, quota concurrency, and CLI create/list/show/revoke validation.
- [ ] **Step 2: Run focused tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_message_capabilities.py tests/test_private_message_cli.py -q`. Expected: failures before registration and service methods exist.
- [ ] **Step 3: Implement capability registration and authorization.** Set an explicit confirmation policy for per-message resident authorization. Permit an outward-facing action through `CapabilityPolicyEngine` only when the action is exactly `message.submit`, `invocation == "private_curation"`, `interface == "curation"`, and the submission authorizer verifies a draft from the same turn at a strictly earlier `house_tool_round`. Keep all other outward-facing interface restrictions unchanged. Register only through Runtime composition.
- [ ] **Step 4: Implement `submit` as one `BEGIN IMMEDIATE` transaction.** Revalidate owner, draft state/hash, accepted grant/hash, active window, and quota; insert one outbox item and reservation; mark the draft submitted; and persist an allowlisted metadata receipt. A repeated draft/hash returns its existing submission result without another outbox item. Add operator CLI grant commands; require explicit destination kind/ID, active-from, expiry, quota, and optional capped length, and print the complete terms/hash without activating the grant.
- [ ] **Step 5: Run focused tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_message_capabilities.py tests/test_private_message_cli.py -q`. Expected: all authorization, transaction, concurrency, and CLI tests pass.
- [ ] **Step 6: Commit** as `feat: authorize private message submissions`.

### Task 3: Outbox lifecycle, Discord delivery worker, and restart recovery

**Files:**
- Create: `VESTIGIA_Runtime/tests/test_private_message_delivery.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/private_messages.py` — claim, completion, recovery, and quota accounting.
- Modify: `VESTIGIA_Runtime/src/vestigia/adapters/discord_adapter.py` — worker and startup recovery in the existing Discord process.
- Modify: `VESTIGIA_Runtime/tests/test_vestigia.py` only where shared adapter helpers need regression coverage.

**Interfaces:**
- Consumes Task 2's outbox service operations.
- Produces `peek_next() -> dict[str, Any] | None`, a read-only metadata-only view used to resolve the grant-bound Discord destination before claiming.
- Produces `claim(outbox_id: str, now: str | None = None) -> dict[str, Any] | None`, which atomically changes that eligible `queued` item to `attempting` after revalidating grant, reservation, expiry, destination, and payload hash.
- Produces `finish_delivery(outbox_id: str, outcome: str, *, discord_message_id: str | None = None, safe_error_code: str | None = None) -> dict[str, Any]`; outcomes are `delivered|failed|delivery_uncertain`.
- Produces `recover_attempting() -> int`, which converts unresolved `attempting` items to `delivery_uncertain`, consumes their reservations, and never requeues them.

- [x] **Step 1: Write failing lifecycle tests** for queued cancellation, revocation, expiration before claim, atomic claim, cancellation-vs-claim serialization, quota refund on definite failure, quota consumption on delivered/uncertain, and stale-attempt recovery.
- [x] **Step 2: Write failing adapter tests** using a fake Discord client/destination: verify exact grant destination, one `send` call with the original body, `AllowedMentions.none()`, `suppress_embeds=True`, no attachment/chunk code path, no delivery when revoked/expired/unavailable before claim, and no automatic retry after timeout or restart.
- [x] **Step 3: Run focused tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_message_delivery.py -q`. Initial collection failed because the worker was absent; after implementation, 11 tests passed.
- [x] **Step 4: Implement state transitions** as serialized database operations. Cancellation and revocation change only queued items and release reservations. A worker claim winning the race makes cancellation return `too_late`. Confirmed delivery consumes the reservation and stores Discord message ID; definite rejection releases it; ambiguous outcomes consume it and mark uncertainty. Submitted draft copies are purged; queued/uncertain outbox content remains only as required for delivery/reconciliation and is purged after terminal resolution.
- [x] **Step 5: Integrate one Discord outbox loop.** On startup call `recover_attempting()`; poll eligible queued work; resolve only the grant's stored channel/DM ID; claim before network I/O; send one message with mentions disabled and embeds suppressed; classify known rejection separately from ambiguity; persist the result. Destination unavailability before claim leaves queued. No retry follows an ambiguous claimed attempt.
- [x] **Step 6: Run focused and regression tests**: `tests/test_private_message_delivery.py` → 11 passed; full suite → 349 passed, 2 skipped, 4 subtests passed; compileall and `git diff --check` passed.
- [ ] **Step 7: Commit** as `feat: deliver private messages through discord outbox`.

### Task 4: Operator outbox controls, documentation, and full verification

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/cli.py` — add `message outbox show|cancel|reconcile` and body inspection.
- Create: `VESTIGIA_Runtime/docs/PRIVATE_OUTBOUND_MESSAGING.md` — operator and daemon-facing instructions.
- Modify: `VESTIGIA_Runtime/docs/DISCORD.md` — explain authenticated outbound destination setup and separate ingress allowlists.
- Modify: `VESTIGIA_Runtime/tests/test_private_message_cli.py` — lifecycle inspection/reconciliation coverage.
- Modify: `VESTIGIA_Runtime/tests/test_runtime_bootstrap.py` — assert the new composition installer is present and deterministic.

**Interfaces:**
- Consumes the Task 2/3 service methods `inspect_outbox`, `cancel`, and `reconcile`.
- `vestigia message outbox show HOME OUTBOX_ID [--include-body]` displays status, destination, grant/draft/hash, reservation, timestamps, safe error classification, and optional bounded body.
- `vestigia message outbox cancel HOME OUTBOX_ID` distinguishes cancelled from too-late.
- `vestigia message outbox reconcile HOME OUTBOX_ID --outcome delivered|not-delivered --reason TEXT` records a separate operator receipt and never retries.

- [x] **Step 1: Add failing CLI tests** for show without body, explicit bounded body inspection, cancellation results, reconciliation as a separate receipt, and rejection of unsupported outcomes. They failed before the service and commands were implemented.
- [x] **Step 2: Run focused CLI tests** with `cd VESTIGIA_Runtime && python -m pytest tests/test_private_message_cli.py -q`; 4 passed.
- [x] **Step 3: Implement operator outbox commands** with distinct result states for `queued`, `delivered`, `failed`, `delivery_uncertain`, `cancelled`, and `too_late_to_cancel`. Reconciliation records one operator-verified delivered/not-delivered event without rewriting the original attempt.
- [x] **Step 4: Write operator and daemon-facing documentation** covering grant creation and resident acceptance, exact draft then later-round submit, lifecycle and quota rules, safe errors, revocation/cancellation race, uncertainty/reconciliation, message limits, and separate ingress allowlists. The docs say `delivered` means Discord returned a created message ID, not that a person read it.
- [x] **Step 5: Run focused, full, and static checks**:
  - `cd VESTIGIA_Runtime && python -m pytest tests/test_private_messages.py tests/test_private_message_capabilities.py tests/test_private_message_cli.py tests/test_private_message_delivery.py tests/test_runtime_bootstrap.py -q`
  - `cd VESTIGIA_Runtime && python -m pytest -q`
  - `cd VESTIGIA_Runtime && python -m compileall -q src tests`
  Expected: all tests and compilation pass.
- [x] **Step 6: Review the diff against all ten acceptance criteria** in the spec: no added dependency; receipts exclude body and private context; only private curation can queue; worker makes one attempt and does not retry ambiguity.
- [ ] **Step 7: Commit** as `docs: document private outbound messaging operations`.

---

## Coverage map

- Grant fields, finite scope, acceptance, decline/relinquish/revocation, and destination pinning: Tasks 1–2.
- Text constraints, UTF-16 length, exact hashing, mentions, and controls: Task 1.
- Private invocation, distinct later round, draft identity, and exact submission: Task 2.
- Atomic submit, reservation, idempotence, concurrency, and receipts: Task 2.
- Fixed-target Discord send, mention suppression, embed suppression, and no attachment/chunk path: Task 3.
- Claim/cancel/revoke race, expiry, refund/consume policy, uncertainty, and crash recovery: Task 3.
- Body retention/purge, safe receipts, operator inspection, and reconciliation: Tasks 1, 3–4.
- CLI lifecycle and daemon/operator guidance: Tasks 2 and 4.
- Excluded sources and non-goals: Tasks 2–4 plus full diff review.
