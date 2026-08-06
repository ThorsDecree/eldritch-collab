# Resident Attention Keyring

The attention keyring adds resident-owned quiet windows, explicit preference records,
live wake receipts, correction labels, and a compact attention dashboard.

Its governing boundary is:

> Attention controls may narrow reachability and explain routing. They do not create
> participant authority, tool authority, memory authority, or outward-action authority.

## Quiet mode

`attention.quiet` supports `inspect`, `activate`, `cancel`, and `release`.

Activation presets are:

- `ambient_closed`: close ambient text while preserving direct signals that were
  already open;
- `direct_only`: an explicit direct-signal-only name for the same boundary;
- `everything_closed`: close ambient text, mentions, replies, commands, and DMs;
- `custom`: specify the five signal booleans individually.

A quiet session captures the effective pre-quiet signal state. When the session expires
or is cancelled, the keyring restores only that captured baseline. The restored baseline
remains a visible lock until `release` is explicitly invoked. This prevents a concurrent
configuration change from silently reopening a door that was closed before quiet mode.

The recovery commands `!status` and `!wake` remain available through the Discord gate.
They do not themselves grant broader authority.

```json
{
  "action": "attention.quiet",
  "mode": "activate",
  "preset": "ambient_closed",
  "duration_seconds": 1800,
  "reason": "Quiet for half an hour",
  "after": "continue"
}
```

To dismiss the restoration lock explicitly:

```json
{
  "action": "attention.quiet",
  "mode": "release",
  "after": "continue"
}
```

## Attention preference ledger

`attention.preference` stores explicit resident-owned records. Preferences are not inferred
from memories or prior conversation.

Preference kinds:

- `always_notice`
- `usually_ignore`
- `semantic_check_only`

Each record includes a stable ID, literal and normalized terms, interface and optional
channel scope, status, optional expiry, timestamps, and provenance recording the explicit
actor and reason.

Active preferences are projected into the shadow router:

- `always_notice` becomes a hard-wake lexical term;
- `usually_ignore` becomes a suppression term;
- `semantic_check_only` becomes a soft semantic-check signal.

This projection does not promote shadow decisions to live routing.

```json
{
  "action": "attention.preference",
  "mode": "create",
  "kind": "semantic_check_only",
  "term": "show Liora this",
  "interface": "discord",
  "channel_id": "1234567890",
  "reason": "Explicit resident preference",
  "after": "continue"
}
```

Records can be listed, revised, disabled, or deleted by stable ID.

## Why-did-I-wake receipts

Every live Discord resident-model turn receives a compact wake receipt recording the direct
signal, routing reason, platform and resident-scope admission, live route, linked listening
and router evidence, included context message IDs, runtime outcome, and whether a visible
response or outward attachment/reaction was prepared.

The receipt is evidence of ingress and context inclusion. It does not claim that any included
message influenced the resident's thoughts or response.

Use `attention.wake.receipts` to list or inspect receipts.

## Correction ergonomics

`attention.correction` provides four explicit labels:

- `should_ignore`
- `worth_inviting`
- `keyword_too_broad`
- `fixture_or_quote`

Corrections remain labeled evidence with an `awaiting_review` state. They do not silently
rewrite lexical terms, retrain a model, or change live routing.

## Compact attention dashboard

`house.attention_dashboard` is a read-only projection containing current sensory state,
quiet locks, platform reachability, resident channel scope, preferences, router controls,
semantic budget remaining, recent decisions, wake receipts, pending corrections, and
keyring receipts.

Inspecting the dashboard does not acknowledge, resolve, remember, send, publish, or widen
anything.

## Operator ceilings

```yaml
attention_keyring:
  max_preferences: 128
  max_quiet_seconds: 604800
```

The attention router's existing term-length and semantic-budget ceilings continue to apply.
