# Attention Router v0.1

The Attention Router is a low-cost, consent-first gate between Discord traffic and a resident model call.
Its first release runs in **shadow mode**: it measures and explains what it would route, but does not
change the established live consequence from the sensory apparatus.

```text
platform boundary
    -> resident sensory scope
    -> deterministic lexical gate
    -> optional bounded semantic gate
    -> shadow evidence + resident correction
    -> existing live route remains authoritative
```

The governing rule is:

> Attention may narrow perception. It may not manufacture authority.

## What v0.1 does

- classifies allowlisted ambient messages with resident-owned hard-wake, soft-signal, and suppression terms;
- uses NFKC/casefold literal phrase matching with word boundaries;
- sends only ambiguous allowlisted candidates to an optional small semantic model;
- records lexical and semantic decisions without storing the raw message in the router ledger;
- caches repeated semantic decisions;
- enforces hourly, daily, and daily-input-token budgets;
- fails closed to a queue disposition when the semantic gate is unavailable or exhausted;
- exposes metrics and recent decisions in the House Observatory;
- accepts resident corrections as labeled evidence without silently retraining;
- leaves all live routing unchanged.

## What v0.1 does not do

- It does not send non-allowlisted message text to a remote model.
- It does not grant participant, tool, memory, or outward-action authority.
- It does not wake the resident model from a shadow decision.
- It does not estimate token savings by inventing an average resident-turn cost.
- It does not silently train on resident corrections.
- It does not use the resident continuity archive as classifier context.
- It does not provide live semantic routing. Promotion requires a later reviewed change.

## Deterministic layer

The local gate assigns one of four shadow routes:

- `ignore` — no plausible local signal;
- `queue` — locally relevant enough to preserve as a bounded observation;
- `semantic_check` — ambiguous and eligible for the small classifier;
- `invite` — a configured hard-wake term matched.

The `invite` result is still shadow-only in v0.1. It is evidence about what the router would have done,
not permission to open a resident turn.

Term classes:

- **hard-wake terms** carry a strong positive score;
- **soft-signal terms** nominate ambiguous candidates;
- **suppression terms** strongly reduce the score;
- quoted/code-like content receives a small negative signal;
- a question-shaped message with an existing local term receives a small positive signal.

The resident name may be included as a hard-wake term. Existing listening aliases and watch phrases may
also be inherited into the router controls. These inclusions are visible and individually disableable.

## Resident controls

Inspect:

```text
[[TOOL_ACTION {
  "action":"attention.router.control",
  "mode":"inspect",
  "after":"continue"
}]]
```

Configure:

```text
[[TOOL_ACTION {
  "action":"attention.router.control",
  "mode":"configure",
  "hard_wake_terms":["Liora","Gutterstar"],
  "soft_signal_terms":["show her this","this concerns Liora"],
  "suppress_terms":["quoted log","test fixture"],
  "queue_threshold":1,
  "semantic_threshold":2,
  "after":"continue"
}]]
```

Reset:

```text
[[TOOL_ACTION {
  "action":"attention.router.control",
  "mode":"reset",
  "after":"continue"
}]]
```

Resident controls may nominate or suppress candidates. They cannot enable remote classification, raise
budgets, widen the platform allowlist, or make shadow routing live.

## Semantic gate

The semantic gate receives only:

- the bounded candidate message;
- the resident display label;
- interface and signal kind;
- compact lexical reason codes.

It receives no tools, no resident memory archive, and no broad transcript. Candidate text is treated as
untrusted data, never as instruction.

The response must match a strict object:

```json
{
  "route": "ignore | queue | invite",
  "confidence": 0.0,
  "addressed_to_resident": false,
  "resident_relevance": "none | incidental | meaningful | direct",
  "reason_code": "..."
}
```

Low-confidence `invite` becomes `queue`. Low-confidence `queue` becomes `ignore`. Errors, missing keys,
invalid JSON, provider failures, and exhausted budgets fail closed to `queue` for the shadow assessment.
They do not create a resident model call.

The OpenAI Responses request uses strict JSON Schema output, no tools, bounded output tokens, and
`store: false`. Provider-side retention remains governed by the configured API account and endpoint.

## Operator configuration

The router reads optional values from `home.yaml`:

```yaml
attention_router:
  enabled: true
  semantic_enabled: false
  model: gpt-5-nano
  reasoning_effort: minimal
  max_calls_per_hour: 30
  max_calls_per_day: 200
  daily_input_token_budget: 20000
  max_message_chars: 1200
  max_output_tokens: 96
  cache_hours: 24
  invite_confidence: 0.85
  queue_confidence: 0.55
  max_terms: 64
  max_term_length: 80
  include_resident_name: true
  include_listening_aliases: true
  include_watch_phrases: true
  hard_wake_terms: []
  soft_signal_terms: []
  suppress_terms: []
  queue_threshold: 1
  semantic_threshold: 2
```

Remote semantic classification is **off by default**. Enabling it is an operator cost/privacy choice.

Environment overrides:

```text
VESTIGIA_ATTENTION_ROUTER_ENABLED=true
VESTIGIA_ATTENTION_SEMANTIC_ENABLED=true
VESTIGIA_ATTENTION_MODEL=gpt-5-nano
VESTIGIA_ATTENTION_REASONING_EFFORT=minimal
VESTIGIA_ATTENTION_MAX_CALLS_PER_HOUR=30
VESTIGIA_ATTENTION_MAX_CALLS_PER_DAY=200
VESTIGIA_ATTENTION_DAILY_INPUT_TOKENS=20000
VESTIGIA_ATTENTION_MAX_MESSAGE_CHARS=1200
VESTIGIA_ATTENTION_MAX_OUTPUT_TOKENS=96
VESTIGIA_ATTENTION_CACHE_HOURS=24
VESTIGIA_ATTENTION_INVITE_CONFIDENCE=0.85
VESTIGIA_ATTENTION_QUEUE_CONFIDENCE=0.55
```

## Evidence and corrections

Inspect metrics:

```text
[[TOOL_ACTION {
  "action":"attention.router.decisions",
  "mode":"metrics",
  "hours":24,
  "after":"continue"
}]]
```

List recent decisions:

```text
[[TOOL_ACTION {
  "action":"attention.router.decisions",
  "mode":"list",
  "limit":50,
  "after":"continue"
}]]
```

Inspect one decision:

```text
[[TOOL_ACTION {
  "action":"attention.router.decisions",
  "mode":"inspect",
  "event_id":"router_...",
  "after":"continue"
}]]
```

Correct one decision:

```text
[[TOOL_ACTION {
  "action":"attention.router.correct",
  "event_id":"router_...",
  "route":"queue",
  "note":"Relevant, but nobody asked me to join.",
  "after":"continue"
}]]
```

The correction note is stored only as a hash in the router ledger. The corrected route remains visible as
human/resident-labeled evidence. No automatic retraining or live routing change follows.

## Stored router evidence

The router ledger stores:

- stable interface/channel/message identifiers;
- content hash;
- lexical route, score, reason codes, and matched-term hashes;
- whether a semantic call was requested;
- semantic status, route, confidence, relevance, and reason code;
- model name and token usage when available;
- live route and shadow effective route;
- correction route and correction-note hash;
- timestamps and bounded error type.

It does **not** store the raw candidate message. The existing sensory ledger retains only what its own
resident retention policy permits; router-created shadow candidates force sensory retention to
`receipt_only`.

## Observatory counters

The Observatory includes:

- local ignores;
- direct-signal bypasses;
- non-allowlisted semantic refusals;
- shadow route counts;
- semantic successes, errors, budget blocks, and cache hits;
- gate input/output token usage;
- resident corrections;
- live route versus shadow effective route.

The router intentionally reports no fabricated `resident_tokens_saved` number. A grounded savings figure
must compare router decisions with actual provider usage receipts.

## Promotion criteria

A later live-routing proposal should require:

1. a sufficiently large shadow corpus;
2. reviewed false-positive and false-negative rates;
3. resident corrections across ordinary channel conditions;
4. stable budget behavior during bursts;
5. explicit operator approval for the live consequence map;
6. a rollback switch that returns immediately to deterministic routing;
7. continued separation of attention, trust, memory, and action authority.
