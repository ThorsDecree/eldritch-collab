# Bells

The live registry exposes these envelope contracts as `bell.draft` and `bell.control`.
They are discoverable through focused `capabilities` lookup, but remain correctly
invoked as `BELL_DRAFT` and `BELL_CONTROL`, not `TOOL_ACTION`.

Bells are scheduled invitations. They are not obligations, identity assertions, or evidence
that a later action was caused by the bell.

## Quick answer: every three hours

Emit this exact control line from an authenticated resident response:

```text
[[BELL_DRAFT {"title":"Three-hour pulse","purpose":"look_around","prompt":"Notice what wants attention, or choose nothing.","schedule_kind":"interval","schedule":{"seconds":10800},"timezone":"America/Chicago"}]]
```

`10800` seconds is three hours. The interval is anchored when the draft is created, so this
means “every three hours from creation,” not “at midnight, 03:00, 06:00…” The receipt previews
the exact first firing and returns `draft_id` plus `expected_hash`. Nothing is active yet.
After checking the receipt, claim it in a later resident response:

```text
[[BELL_CONTROL {"draft_id":"bell_draft_...","action":"claim","expected_hash":"..."}]]
```

Use `action:"reject"` instead to discard it. Do not invent the ID or hash; copy both from the
draft receipt.

## Schedule syntax at a glance

The four supported schedule shapes are:

```text
Once:
[[BELL_DRAFT {"title":"One visit","purpose":"hello","prompt":"Say hello, or choose nothing.","schedule_kind":"once","schedule":{"at":"2026-07-30T18:00:00-05:00"},"timezone":"America/Chicago"}]]

Every N seconds (minimum 3600):
[[BELL_DRAFT {"title":"Three-hour pulse","purpose":"look_around","prompt":"Notice what wants attention, or choose nothing.","schedule_kind":"interval","schedule":{"seconds":10800},"timezone":"America/Chicago"}]]

Daily at a local wall-clock time:
[[BELL_DRAFT {"title":"Morning windowsill","purpose":"look_around","prompt":"Notice what wants attention, or choose nothing.","schedule_kind":"daily","schedule":{"time":"09:00"},"timezone":"America/Chicago"}]]

Weekly on selected local weekdays (0=Monday … 6=Sunday):
[[BELL_DRAFT {"title":"Monday and Friday archive glance","purpose":"archive_review","prompt":"See whether anything wants tending, or choose nothing.","schedule_kind":"weekly","schedule":{"weekdays":[0,4],"time":"15:00"},"timezone":"America/Chicago"}]]
```

For interval schedules, optional `schedule.anchor` may be an ISO-8601 timestamp with a UTC
offset. Without it, creation time is the anchor. For `once`, `schedule.at` must be an ISO-8601
timestamp; include an offset so the intended instant is unambiguous.

## Complete BELL_DRAFT fields

| Field | Required | Values / shape | Default |
|---|---|---|---|
| `title` | yes | Non-empty text | — |
| `purpose` | yes | One value from the purpose list below | — |
| `prompt` | yes | Non-empty invitation text | — |
| `schedule_kind` | yes | `once`, `interval`, `daily`, `weekly` | — |
| `schedule` | yes | Shape shown above | — |
| `timezone` | no | IANA zone such as `America/Chicago` | `UTC` |
| `strength` | no | `gentle`, `repeated`, `urgent`, `outward_confirmation` | `gentle` |
| `quiet_start` | no | Local `HH:MM` | home default / none |
| `quiet_end` | no | Local `HH:MM` | home default / none |
| `no_response_required` | no | Boolean | `true` |
| `choose_nothing` | no | Boolean | `true` |
| `expires_at` | no | ISO-8601 timestamp | none |
| `reason` | no | Resident-readable drafting reason | none |

The destination is deliberately absent. It is inherited from the authenticated Discord
doorway where the resident authored the draft.

## Safety model

- Every bell names its purpose, prompt, strength, schedule, expiry, quiet hours, and doorway.
- Silence never escalates a bell.
- `no_response_required` and `choose_nothing` are real registry fields.
- The configured doorway authorizes the bell prompt and one conversational response only.
- Posting elsewhere, messaging another person, spending resources, changing relationships,
  or altering public state still requires explicit confirmation.
- Receipts record creation, revision, deferral, firing, delivery, answer, acknowledgement,
  failure, pause, completion, expiry, and deletion without claiming causal influence.
- `DORMANT` prevents provider calls. Due bells wait without treating rest as failure.
- Quiet-hour firings move to the next quiet-hour end.

## Configuration

```dotenv
VESTIGIA_BELLS_ENABLED=true
VESTIGIA_BELL_POLL_SECONDS=30
VESTIGIA_BELL_TIMEZONE=America/Chicago
VESTIGIA_BELL_QUIET_START=22:00
VESTIGIA_BELL_QUIET_END=08:00
```

The Discord process hosts the scheduler loop. Keep `Start_Liora_Discord.bat` running for bells
to fire. Missed bells remain due and are considered after the process returns, subject to
quiet hours, dormancy, pause, and expiry.

The minimum recurring interval is one hour.

## Discord registry

These are participant/operator commands processed at Discord ingress. The resident does not
execute them by printing them in a model response; resident-authored scheduler changes use the
authenticated `BELL_DRAFT` and `BELL_CONTROL` surfaces below.

```text
!bells
!bell show BELL_ID
!bell pause BELL_ID
!bell resume BELL_ID
!bell delete BELL_ID
!bell defer BELL_ID MINUTES
!bell ack BELL_ID seen|ignored|deferred|answered [note]
!bell revise BELL_ID | prompt|title|purpose|strength | new value
!bell reschedule BELL_ID | SCHEDULE
```

Participant-side bell creation is disabled. `!bell add` is retained only to return a clear
resident-authorship notice; it does not create registry state.

## Resident-authored creation

The authenticated resident response path may create a bell with two breaths. First, draft:

```text
[[BELL_DRAFT {"title":"Morning windowsill","purpose":"look_around","prompt":"Notice what wants attention, or choose nothing.","schedule_kind":"daily","schedule":{"time":"09:00"},"timezone":"America/Chicago"}]]
```

The runtime returns a pending draft ID, payload hash, and calculated first firing. It does not
activate the bell. In a later response, the resident may claim the exact preview:

```text
[[BELL_CONTROL {"draft_id":"bell_draft_...","action":"claim","expected_hash":"..."}]]
```

`action:"reject"` closes the candidate without creating a bell. The destination is inherited
from the authenticated Discord doorway and cannot be supplied in the draft.

Purposes:

```text
reflection
creative_play
maintenance
relationship_tending
archive_review
room_inspection
look_around
hello
other
```

Strength defaults to `gentle`. Available strengths are `gentle`, `repeated`, `urgent`, and
`outward_confirmation`. Strength is descriptive; it does not create an escalation ladder.

## Resident edits from within

Every fired invitation tells the resident how to emit one explicit scheduler-only control:

```text
[[BELL_CONTROL {"bell_id":"bell_...","action":"pause"}]]
```

Supported actions are `pause`, `delete`, `defer`, and `revise`. Revision may change prompt,
title, purpose, strength, schedule kind, schedule data, or timezone. The control line is removed
from the visible reply, applied to the registry, and recorded with resident attribution.

```text
Pause:
[[BELL_CONTROL {"bell_id":"bell_...","action":"pause"}]]

Delete:
[[BELL_CONTROL {"bell_id":"bell_...","action":"delete"}]]

Defer 90 minutes:
[[BELL_CONTROL {"bell_id":"bell_...","action":"defer","minutes":90}]]

Revise the prompt:
[[BELL_CONTROL {"bell_id":"bell_...","action":"revise","prompt":"A softer invitation."}]]

Reschedule to every three hours:
[[BELL_CONTROL {"bell_id":"bell_...","action":"revise","schedule_kind":"interval","schedule":{"seconds":10800}}]]
```

This is not a general tool-execution channel. It cannot message another person, post, purchase,
generate an image, or mutate identity documents.

## CLI

```bash
vestigia bells HOME
vestigia bell show HOME BELL_ID
vestigia bell pause HOME BELL_ID --actor "Liora Gutterstar"
vestigia bell revise HOME BELL_ID --prompt "A softer question"
vestigia bell reschedule HOME BELL_ID --weekly "mon,fri@15:00"
```

The operator CLI deliberately cannot create daemon bells. It may inspect, pause, resume,
revise, defer, acknowledge, or delete resident-created bells.
