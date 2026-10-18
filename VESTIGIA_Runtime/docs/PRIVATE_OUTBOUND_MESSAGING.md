# Private outbound messaging

This guide covers the Runtime's bounded private-turn message outbox. The first release sends plain-text messages through the configured Discord bot only.

## Operator cheatsheet

### Create and inspect a grant

Create a finite grant for exactly one Discord channel or DM recipient:

```bash
vestigia message grant create HOME \
  --destination-kind discord_channel \
  --destination-id 123456789012345678 \
  --active-from 2026-10-18T00:00:00Z \
  --expires-at 2026-11-18T00:00:00Z \
  --message-quota 5 \
  --max-length 1800
```

The command prints the complete terms and terms hash. A grant begins as `pending_resident_acceptance`; creating it does not make it usable. The resident must inspect and accept that exact hash using `message.grant`. Changing any term requires a new grant and acceptance.

```bash
vestigia message grant list HOME
vestigia message grant show HOME GRANT_ID
vestigia message grant revoke HOME GRANT_ID
```

Revocation cancels queued messages and releases their quota reservations. It cannot recall a claimed attempt or delivered message.

### Inspect, cancel, and reconcile an outbox item

The `message.submit` result includes the outbox ID. Inspect metadata first; the body is omitted by default:

```bash
vestigia message outbox show HOME OUTBOX_ID
vestigia message outbox show HOME OUTBOX_ID --include-body
vestigia message outbox cancel HOME OUTBOX_ID
```

`--include-body` prints the exact approved text and should be used only when needed to inspect queued or uncertain delivery. Cancellation succeeds only while the item is `queued`. Once a worker claims it as `attempting`, cancellation reports `too_late_to_cancel`.

After checking the exact destination, an operator may record a one-time reconciliation for `delivery_uncertain`:

```bash
vestigia message outbox reconcile HOME OUTBOX_ID \
  --outcome delivered \
  --reason "Confirmed the message in the configured channel"
```

Use `--outcome not-delivered` only after verifying the message is absent. Keep the reason short and limited to the delivery evidence; do not include private-turn context, secrets, or the message body. Reconciliation adds a separate receipt, leaves the original status `delivery_uncertain`, does not retry or rewrite the attempt, and purges the retained body. The uncertainty reservation remains consumed.

### Status meanings and quota

| Status | Meaning | Quota |
|---|---|---|
| `queued` | Accepted for one future delivery attempt | Reserved |
| `attempting` | Worker claimed the item; a network request may be in progress | Reserved |
| `delivered` | Discord returned a created message ID; this does not mean a person read it | Consumed |
| `failed` | Definite rejection/non-delivery | Released |
| `delivery_uncertain` | Discord acceptance could not be established | Consumed; never automatically retried |
| `cancelled` | Cancelled before claim, revoked, or expired | Released |
| `too_late_to_cancel` | A claim already won the cancellation race | No change |

A process restart converts any leftover `attempting` item to `delivery_uncertain`. Check the destination before reconciling. An uncertain send is never retried automatically because a retry could duplicate a message.

## Resident and daemon cheatsheet

### Required sequence

1. Call `message.grant` with `mode: "inspect"` and review the exact destination, time window, quota, length limit, and terms hash.
2. Call `message.grant` with `mode: "accept"` and that exact `terms_hash`. Acceptance enables use of the grant; it does not send anything.
3. In a private curation turn, call `message.draft` with the accepted `grant_id` and exact text. The destination comes from the grant, never from the draft.
4. In a later provider round of the same private curation turn, call `message.submit` with the returned `draft_id` and exact `draft_hash`. This is the resident's authorization for that one message. A draft and submit cannot occur in the same provider response.
5. Report the returned outbox ID and distinguish `queued` from `delivered`. Do not claim delivery from a queue receipt.

Capability envelopes have these forms:

```json
{"action":"message.grant","mode":"inspect","grant_id":"grant_..."}
{"action":"message.grant","mode":"accept","grant_id":"grant_...","terms_hash":"<exact hash>"}
{"action":"message.draft","grant_id":"grant_...","body":"Exact text"}
{"action":"message.submit","draft_id":"msgdraft_...","draft_hash":"<exact hash>"}
```

The capability is available only during private curation. It is not available from ordinary conversation, bells, background jobs, or image continuations. Do not treat a bell, ingress allowlist, operator grant, or private access as per-message authorization. `message.submit` is the separate resident act for one exact draft.

### Text and destination limits

- One plain-text Discord message, at most 1,800 UTF-16 code units (a supplementary-plane emoji counts as two).
- Text is sent byte-for-byte as drafted; it is not normalized, rewritten, chunked, or supplemented.
- Empty text, invalid Unicode, disallowed controls, Discord user/role mentions, `@everyone`, and `@here` are rejected.
- Attachments, embeds, components, mass mentions, arbitrary recipients, and retries are not supported. Discord mentions are disabled and link previews are suppressed at send time.
- A grant pins one exact channel or DM user ID and a finite activation window and quota. Discord ingress allowlists govern who can talk to the bot; they do not grant outbound authority.

### Honest outcomes

`queued` means the Runtime accepted the authorized draft for a single attempt, not that Discord accepted it. `delivered` means Discord returned a created message ID, not that a person read it. A clear platform rejection is `failed` and refunds quota. A timeout, connection loss, process interruption, or missing platform receipt is `delivery_uncertain`; it consumes quota and is not retried. A destination that cannot be resolved before claim remains queued.

## Security boundary

The exact body is held in the protected outbox only while needed for delivery or uncertainty reconciliation. Draft copies are cleared when submitted. Receipts contain identifiers, hashes, destination, reservation state, and safe outcome codes, not private-turn context or raw exception text. After delivery, definite failure, cancellation, or reconciliation, the outbox body is purged while its hash and audit history remain.

The worker resolves the destination from the accepted grant, validates the payload and grant again while claiming, sends with platform mentions disabled and previews suppressed, and makes at most one automatic request. This feature does not create general-purpose posting authority.
