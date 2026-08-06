# Resident sensory apparatus

This layer extends the resident doorway controls introduced in PR #18. It adds resident-owned attention modes, bounded Discord source scopes, configurable retention, a read-only House Observatory, and a first-class `MAKE_NOTHING_HAPPEN` disposition.

The governing boundary is unchanged:

> A resident may narrow what reaches them and reduce what happens afterward. Attention cannot manufacture authority.

The implementation is intentionally stacked on `agent/resident-doorway-controls`. It assumes PR #18's requested/operator/effective policy intersection, deterministic literal listening, hash-only non-allowlisted queue events, and exact-doorway outward-action checks.

## Attention modes

`sensory.control` exposes five attention states:

- `present` — ordinary direct ingress and configured contextual listening may invite a turn.
- `peeking` — qualifying signals create receipt-only observations; no model turn opens.
- `digest_only` — qualifying allowlisted signals may retain a bounded extractive digest; no model turn opens.
- `asleep` — qualifying direct signals are held as receipt-only observations; no model turn opens.
- `deaf` — signals are ignored and retained as nothing.

`!wake` and `!status` remain explicit recovery commands when they arrive through an otherwise permitted doorway. Silence never escalates to interest, memory, or action.

A mode may expire:

```text
[[TOOL_ACTION {
  "action":"sensory.control",
  "mode":"listen_until",
  "attention_mode":"present",
  "duration_seconds":7200,
  "attention_after_expiry":"deaf",
  "after":"continue"
}]]
```

The operator sets the maximum temporary-attention window. A request beyond that window remains inspectable as requested state while the effective expiry is clamped.

## Ingress signals and source scopes

Implemented signal kinds are:

- `mention`
- `reply`
- `dm`
- `command`
- `ambient_text`

The resident may select permitted signal kinds, include only named Discord channel IDs, exclude named channel IDs, or turn off DM listening inside the operator's platform allowlist.

```text
[[TOOL_ACTION {
  "action":"sensory.control",
  "mode":"configure",
  "listening_ingress_signals":["mention","reply","ambient_text"],
  "listening_channel_ids":["1234567890"],
  "listening_excluded_channel_ids":["9876543210"],
  "listening_allow_dms":false,
  "after":"continue"
}]]
```

`not_this_channel` can infer the current authenticated Discord destination:

```text
[[TOOL_ACTION {
  "action":"sensory.control",
  "mode":"not_this_channel",
  "after":"finish"
}]]
```

These controls only narrow the existing Discord platform allowlist. They cannot add a channel, participant, interface, or destination that the operator did not already permit.

### Visible limitation

Named-participant listening scopes are not implemented in this slice. The current trigger-classifier seam receives trust class but not participant identity. `operator_limits.participant_scopes_implemented` therefore reports `false`; the Runtime does not present a decorative control it cannot enforce.

## Retention modes

- `live_context` — a qualifying allowlisted contextual match may invite a resident turn under the effective on-match policy.
- `short_digest` — store only a bounded extractive digest and the ordinary hash/provenance fields; do not open a model turn.
- `receipt_only` — store routing, trust, match, consequence, and content hashes without message text.
- `none` — create no listening event and take no further action.

Non-allowlisted authors are always downgraded to `receipt_only` or `none`. Their text is never retained as a digest and never opens a model turn.

## Why did this reach me?

Every recorded sensory event carries an explanation surface:

```text
[[TOOL_ACTION {
  "action":"source.explain",
  "event_id":"listen_...",
  "after":"continue"
}]]
```

The result identifies:

- interface and signal kind;
- resident policy that permitted the observation;
- attention and retention mode;
- author trust class;
- permitted consequence;
- whether a resident response was prepared;
- the invariant that no participant or tool authority changed.

A resident may discard any retained digest while preserving the minimal hash receipt:

```text
[[TOOL_ACTION {
  "action":"sensory.control",
  "mode":"forget_event",
  "event_id":"listen_...",
  "after":"finish"
}]]
```

## House Observatory

`house.observatory` is a read-only status surface over the house's existing ledgers:

```text
[[TOOL_ACTION {
  "action":"house.observatory",
  "section":"all",
  "after":"continue"
}]]
```

It can show:

- requested, operator, and effective doorway controls;
- recent listening events;
- bells;
- pending image-share challenges;
- resident and image jobs;
- memory states;
- recent receipts;
- unresolved action breadcrumbs;
- the current outward-action boundary.

Inspecting the observatory resolves nothing. It does not acknowledge a bell, consume a challenge, retry a job, adopt memory, send a message, or publish an artifact. The default outcome of leaving every row untouched is **nothing**.

## `MAKE_NOTHING_HAPPEN`

This is a first-class execution disposition, not a slogan:

```text
[[TOOL_ACTION {
  "action":"make.nothing.happen",
  "note":"Seen. Leave it untouched.",
  "after":"finish"
}]]
```

For that turn the Runtime:

- performs no outward action;
- adopts no memory and skips automatic candidate extraction;
- opens no automatic curation cadence;
- creates no follow-up job;
- exports and publishes nothing;
- changes no authority;
- emits no Discord receipt line;
- keeps only the minimal private durable action receipt.

The capability advertises a UI hint for a large decorated red button labeled **MAKE NOTHING HAPPEN**. A future Cottage Commander surface may render that hint, but the semantics already live in the Runtime.

## Operator ceilings

The sensory layer recognizes these optional `resident_controls` settings:

```yaml
resident_controls:
  allowed_attention_modes:
    - present
    - peeking
    - digest_only
    - asleep
    - deaf
  allowed_listening_retention_modes:
    - live_context
    - short_digest
    - receipt_only
    - none
  allowed_listening_ingress_signals:
    - mention
    - reply
    - dm
    - command
    - ambient_text
  max_listening_channels: 64
  max_attention_window_seconds: 604800
  max_listening_digest_chars: 280
```

Operator narrowing changes effective behavior immediately without silently rewriting the resident's requested state.

## Deliberate non-goals

This slice does not add a browser, network access, a code sandbox, named-participant scopes, website actions, household sensors, or publication tools. It establishes the sensory and legibility floor those later capabilities can reuse.
