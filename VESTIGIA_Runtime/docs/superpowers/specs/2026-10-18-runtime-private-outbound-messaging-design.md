# Runtime Private Outbound Messaging Design

**Status:** Proposed for review  
**Date:** 2026-10-18  
**Repository:** `ThorsDecree/eldritch-collab`  
**Scope:** First release of resident-initiated plain-text messages from private curation turns through the authenticated Discord doorway.

## Purpose

Allow a resident to decide that work performed in a private Runtime turn should be shared through a configured Discord destination, even when no operator is present at that moment. An operator grants narrow standing authority in advance. The resident must separately accept the exact grant and explicitly submit each exact message. The runtime must preserve a durable, truthful record of authorization and delivery without copying private-turn context into the receipt.

The feature must not infer sending authority from Discord ingress allowlists, a bell invitation, a private turn, or the existence of an operator grant alone.

## Existing architecture and insertion points

The Runtime already has:

- bounded private tool rounds in `CoreRuntime._complete_with_house_tools`;
- private curation invocations marked `invocation="private_curation"`, whose ordinary prose is not surfaced;
- an executable capability registry with per-action validation and authorization hooks;
- a Discord adapter that delivers resident output, bell replies, image attachments, and reactions;
- a SQLite continuity database and a legible receipt ledger.

Private jobs currently reject outward actions, and private curation suppresses outbound attachments and reactions. This first release adds a narrow text-message outbox path for private curation only. It does not turn the generic private tool loop or resident jobs into an outward-action channel.

The operator manages grants through the local CLI. The resident inspects, accepts, or relinquishes a grant through the authenticated capability path. The Discord adapter is the only delivery worker in this release.

## Authority model

### Operator grant

An operator creates a grant for exactly one Discord destination. A grant records:

- grant ID and resident ID;
- doorway kind: one guild channel or one DM recipient;
- exact Discord destination ID;
- active-from and expiry timestamps;
- maximum number of messages;
- maximum message length, capped at 1,800 UTF-16 code units;
- schema version, creation time, and a canonical terms hash.

Grant creation is an operator CLI operation and is never available as a resident capability. Each grant is finite: it must include an expiry and a positive message quota. An ingress allowlist does not create, widen, or imply an outbound grant.

A new grant starts in `pending_resident_acceptance`. The resident can inspect its exact terms and hash, then accept that hash in a later resident action, or decline it. Acceptance binds to the complete terms hash. It does not authorize any particular message. A grant whose terms change requires fresh resident acceptance. The resident may relinquish an accepted grant. An operator may revoke a grant at any time.

### Per-message resident act

A private curation turn may draft one plain-text message against an accepted grant. The draft uses the grant ID; it cannot supply or override a destination.

A separate resident action in a subsequent private provider round submits the draft ID and exact draft hash. The Runtime verifies the text, grant state, grant terms hash, destination, expiry, quota, and source invocation. Submission is the resident's explicit authorization for that exact message.

This second action may occur in the same private curation invocation, provided it is a later provider round. Drafting and submission in one provider response are rejected. The message is not sent from ordinary conversational responses, bells, image jobs, or bounded resident jobs in this release.

## Message constraints

Version one supports one Discord text message per submitted draft:

- Non-empty UTF-8 text, no more than 1,800 UTF-16 code units.
- No attachments, files, embeds, components, cross-posting, or chunking.
- Reject Discord user, role, `@everyone`, and `@here` mention syntax.
- Disable all platform mentions and suppress automatic link previews at delivery as defense in depth.
- Do not rewrite, normalize, escape, or append to message text between hashing and delivery. Hash the exact UTF-8 bytes stored for the draft.
- Reject invalid Unicode and disallowed control characters; allow ordinary line breaks and tabs.

These limits apply to the complete single message. A too-long draft is rejected with the measured UTF-16 code-unit count and the grant limit.

## Capabilities and operator interface

The capability registry adds narrowly scoped actions:

- `message.grant`: inspect a grant, accept an exact terms hash, decline a pending grant, or relinquish an accepted grant.
- `message.draft`: create a private draft against an accepted grant and return its ID/hash.
- `message.submit`: submit an earlier draft ID/hash for delivery. Only this action reserves quota and creates an outbox item.

The local operator CLI adds grant create/list/show/revoke commands. Grant creation requires a specific Discord channel or DM ID, active-from and expiry timestamps, message quota, and a length limit no greater than 1,800 UTF-16 code units. The CLI displays the complete terms and terms hash. It does not silently activate the grant.

The Discord worker does not accept destination data from resident message text. It resolves destinations only from the exact grant record and sends only while the grant remains valid.

## Persistence and delivery lifecycle

Use additive, resident-scoped SQLite tables for grants, drafts, outbox items, and quota reservations. Store message body only in the protected draft/outbox record required for delivery. The legible receipt contains metadata and hashes, not private-turn content.

Grant states:

`pending_resident_acceptance → active → relinquished | revoked | expired | exhausted`

Draft states:

`drafted → submitted | rejected | expired | cancelled`

Outbox states:

`queued → attempting → delivered | failed | delivery_uncertain`

A queued message may transition to `cancelled`. A worker must atomically claim a queued item as `attempting` before making the network request. This database transition is the boundary after which cancellation cannot promise suppression.

Submission runs in one database transaction: revalidate the accepted grant and exact draft; check remaining quota; create the outbox item; reserve one quota use; mark the draft submitted; and write a receipt. Duplicate submission of a draft/hash returns the existing result or a clear already-submitted response. It must not create another outbox item.

The worker revalidates grant status, time window, quota reservation, destination identity, Discord adapter availability, and payload hash immediately before claiming delivery. The destination is fixed in the grant, not caller-controlled.

## Revocation, cancellation, and quota

- Grant revocation cancels all messages still in `queued` state and releases their reservations.
- A queued message can be cancelled directly. Cancellation and worker claim race through one transaction: if cancellation wins, no network call occurs; if claim wins, cancellation returns `too_late` and delivery proceeds to a recorded result.
- Revocation cannot recall an already delivered message or guarantee interruption of an attempt already claimed.
- A cancellation before attempt releases its reservation.
- A definite pre-delivery failure or explicit platform rejection releases the reservation.
- Confirmed delivery consumes the use.
- Any timeout, connection loss after request initiation, process crash while `attempting`, or server response whose acceptance is ambiguous becomes `delivery_uncertain`, consumes the use, and is never automatically retried.
- An expired grant cannot be accepted, submitted against, or used for delivery. If a queued grant expires before worker claim, cancel the queued message and release its reservation.
- Concurrent submissions must not exceed the grant quota.

## Receipts and privacy

Every grant and message lifecycle transition records a durable, resident-scoped receipt. Message receipts include:

- grant ID and terms hash;
- message/draft ID and exact draft hash;
- exact destination kind and ID;
- quota reservation identifier and whether it was released or consumed;
- source invocation and turn IDs (identifiers only);
- authorization action and timestamp;
- queue, attempt, and delivery outcome;
- Discord message ID when delivery is confirmed;
- safe error classification and retry guidance.

Receipts must not contain private-turn prompts, retrieved context, unrelated conversation text, secrets, or raw exception payloads that may contain them. The exact approved message body may remain in the protected outbox while queued or uncertain; receipts expose its hash, not its content. The operator can inspect the message through a bounded local CLI command. The body is purged after a terminal outcome is resolved; the hash and audit metadata remain.

A `delivery_uncertain` item is terminal for automatic processing. An operator may reconcile it as delivered or not delivered after inspecting the destination, but the runtime never retries it. Reconciliation is recorded as a separate operator receipt and does not silently change the original attempt record.

## Failure and restart behavior

- Discord unavailable before an attempt: leave queued while the grant is valid; surface queue age and status in operator inspection.
- Explicit rejection before platform acceptance: mark failed, release quota, record safe error class.
- Ambiguous result after request initiation: mark uncertain, consume quota, retain body only until operator resolution/retention expiry, and do not retry.
- Crash after claim but before a known response: on startup, convert stale `attempting` records to `delivery_uncertain`; do not requeue.
- Database transaction failure before claim: no network call.
- Grant revoked or expired before claim: cancel without network call and release quota.
- Invalid or modified draft: reject before quota reservation and guarantee no outward action.

Each externally visible result distinguishes `queued` (accepted for delivery), `delivered` (Discord returned a message ID), `failed` (known non-delivery), `delivery_uncertain` (acceptance unknown), `cancelled`, and `too_late_to_cancel`.

## Testing and acceptance criteria

Use a fake Discord destination/client and temporary SQLite homes. Tests must establish:

1. Grant creation requires a finite window, exact target, positive quota, and bounded length; it creates no usable grant until resident acceptance of the exact hash.
2. Acceptance with stale or modified terms fails; declined, relinquished, revoked, and expired grants cannot submit.
3. Drafts cannot choose another destination. Invalid Unicode, mention syntax, control characters, empty text, and over-limit UTF-16 lengths are rejected. Boundary lengths include supplementary-plane emoji.
4. A message cannot be submitted without a draft from an earlier provider round, exact matching hash, private-curation invocation, accepted grant, and remaining quota.
5. Submission atomically creates one queued outbox item and reserves one quota use. Concurrent attempts cannot exceed quota; duplicate submissions cannot create duplicate messages.
6. Delivery sends exactly one plain-text message to the grant's exact target with mentions disabled and embeds suppressed; no attachment or chunk path is used.
7. Successful delivery records the Discord message ID and consumes quota. Definite rejection releases quota. Ambiguous timeout or simulated crash records uncertainty, consumes quota, and produces no retry after restart.
8. Cancellation before claim prevents sending and releases quota. Worker claim winning the race returns too-late and records delivery outcome. Revocation cancels queued items, but does not claim to recall already claimed/delivered items.
9. Receipts contain only approved audit metadata and hashes, never private-turn context or raw message body. Message content is purged after terminal resolution; the hash remains.
10. CLI grant lifecycle and resident grant/draft/submit actions expose clear, distinct status and error receipts.

## Non-goals

This design does not add arbitrary recipient selection, public posting APIs, attachments, embeds, bulk sends, user/role notifications, retries for uncertain deliveries, automatic grant creation, resident-controlled grant widening, messages from bells, ordinary conversational turns, image jobs, or bounded private jobs. It does not promise exactly-once delivery across a platform network boundary; it guarantees one automatic attempt and honest uncertainty reporting.
