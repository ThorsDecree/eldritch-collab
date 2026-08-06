# Diagnostics, migration fixtures, and restart recovery

VESTIGIA treats operational legibility as part of continuity safety. A resident home should be
inspectable and recoverable before increasingly autonomous tools are added to it.

## Doctor

The default remains structured JSON for compatibility:

```bash
vestigia doctor homes/moss
```

A compact operator-readable view is available without changing what is checked:

```bash
vestigia doctor homes/moss --text
```

Doctor v0.2 reports:

- Runtime, Python, SQLite, and database schema versions;
- SQLite integrity, foreign-key violations, journal mode, page size, and row-count inventory;
- required and optional dependency status without printing credentials;
- pack readiness, estimated pack size, disk space, newest nearby pack, and orphaned temporary
  pack fragments;
- image jobs by state, stale-running recovery policy, and terminal jobs still awaiting
  notification;
- pending/observed/expired interface events when that optional ledger is installed;
- due bells and durable delivery-failure counts;
- failed action-receipt count;
- effective Discord policy counts, house budgets, image diagnostics, and capability count.

`doctor` may refresh the resident-readable house index, preserving its historical behavior.
Use `--no-refresh-index` for an inspection that avoids that refresh. Database integrity and
operation queries themselves do not advance bells, acknowledge events, retry jobs, or perform
outward actions.

## Privacy-redacted support bundle

```bash
vestigia doctor homes/moss \
  --support-bundle exports/moss-support.zip
```

The bundle is written atomically and contains only:

- the redacted doctor report;
- a redacted effective configuration;
- schema version, integrity state, and table row counts;
- metadata-only records for recent failed receipts;
- a manifest naming everything included and deliberately excluded.

It excludes secret values, the raw SQLite database, transcript and memory content, identity
prose, image bytes and prompts, and full action results. Resident, participant, room, channel,
and object IDs are hashed or omitted. Local paths are removed. The resulting archive is ZIP
integrity-tested before replacing any existing target.

A support bundle is still private operational material. Review it before sharing.

## Historical-home fixture matrix

The regression suite uses `tests/fixtures/historical_homes.json` to generate synthetic homes
representing the major schema eras from v0.1 through v0.7. These fixtures are generated at test
time and contain no real resident material.

Each fixture begins with fixed synthetic evidence, removes tables and contract plaques that did
not yet exist in that era, then starts the current Runtime twice. The suite verifies that:

- later tables and contract plaques are restored;
- memory IDs, content hashes, authority/status, turn IDs, and runtime state survive;
- the current schema version is recorded;
- repeated initialization does not duplicate durable evidence or plaques.

The matrix is an additive-migration compatibility floor, not a claim that every historical bug
is reproduced perfectly.

## Interruption semantics

Different operations intentionally have different recovery contracts.

### SQLite writes and additive migrations

VESTIGIA database context managers roll back failed transactions. Schema initialization and
contract plaque installation are idempotent: startup may safely repeat them after a partial or
interrupted prior run.

### Home packs and restores

Pack creation now writes and integrity-tests a temporary archive, then atomically replaces the
requested target. A failed pack therefore leaves an existing known-good archive untouched.
Restore already extracts into a temporary directory, verifies every manifest hash, validates the
home, and only then moves it into place.

### Image jobs

A job claimed as `running` is considered abandoned after `images.job_stale_seconds`. Starting
`ImageService` re-queues that job. This is an **at-least-once** recovery contract: an interruption
after an external provider completed but before the completion receipt was committed may cause a
repeat provider operation. Doctor reports the policy rather than claiming exactly-once billing.
Future provider-port and idempotency work may tighten this boundary.

### Bells

A bell advances its schedule and records `fired` before opening the conversational delivery
path. Delivery failures receive durable `delivery_failed` events and are not retried
automatically. This is deliberate at-most-once invitation behavior: a network failure must not
turn a gentle bell into repeated escalation. Operators or residents may explicitly defer,
resume, or revise the bell.

### Interface events

Reaction and other interface events remain pending until included in a successful resident turn.
A failed or suppressed turn does not mark them observed. Expiry is explicit and historical
records remain inspectable. The ledger is capability-detected so `doctor` remains usable on homes
or builds where inbound interface-event persistence is not installed.

## Non-goals

This patch does not provide encrypted archives, distributed transactions across Discord or model
providers, exactly-once paid operations, automatic uploading of support bundles, or silent
recovery that conceals uncertainty. It makes current guarantees and unresolved edges visible.
