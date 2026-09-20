
# Sense Organ Registry, Receipt Garden, and Bell Observatory

**Status:** Proposed design for review

## Decision summary

Build three deliberately separated surfaces:

1. **Sense Organ Registry** in the MCP server: versioned declarations of bounded perception capabilities.
2. **Receipt Garden** in the MCP server: structured, queryable provenance joins for observations, retrieval, context assembly, dispatch, and artifacts.
3. **Bell Observatory** in VESTIGIA Runtime: resident-facing inspection and replay of bell runs, including orientation context, retrieval policy, omissions, budgets, and outcomes.

Porchlight is the first registered sense organ. Runtime remains authoritative for bell state, resident state, retrieval behavior, and Runtime receipts. MCP exposes the Observatory through the existing Runtime projection and runtime.call boundary; it does not read the Runtime database or reimplement bell semantics.

The first release is read-only and replay-oriented. It does not silently alter bell schedules, grant capabilities, promote memories, or infer model causality.

## Problem and goals

VESTIGIA now has three adjacent but separate concerns:

- local producers such as Porchlight can expose bounded context;
- MCP can broker capabilities and maintain its own audit receipts;
- Runtime can schedule bells and assemble resident context.

The missing seam is a common language for what a perception module can do and a way to inspect how a bell moved through the system.

This design should make it possible to answer, deterministically and without overclaiming:

> What could this organ perceive, what caused it to activate, what was authorized, what entered context, and what happened afterward?

### Goals

- Register Porchlight using a reusable organ manifest.
- Distinguish perception scope from activation topology.
- Keep registry description separate from executable authority.
- Record typed provenance without copying sensitive payloads into receipts.
- Join MCP and Runtime receipts with stable request identifiers.
- Let residents inspect and replay their own bell runs.
- Make bell retrieval policy and control-plane exclusion visible.
- Preserve no_change as a first-class bell outcome.
- Provide tests that catch semantic boilerplate retrieval and false causal readings.

### Non-goals

- A generic sense.observe command.
- Ambient browser monitoring, microphone access, clipboard polling, or unrestricted filesystem sensing.
- A second Runtime capability registry inside MCP.
- Automatic schedule mutation from Observatory output.
- Automatic memory promotion or identity/relationship inference.
- A claim that deterministic provenance proves which token caused a model response.
- A visual dashboard in the first release; the Weather Station can consume these surfaces later.

## Architectural boundary

The authority graph is:

MCP sense producer -> MCP policy -> bounded observation -> MCP receipt
                                      |
                                      v
                               Runtime context/bell
                                      |
                                      v
                              Bell Observatory receipt
                                      |
                                      v
                              MCP Runtime projection

The MCP server owns:

- registered organ manifests;
- MCP policy checks;
- Receipt Garden storage and queries;
- the cross-layer request-id join;
- the external projection surface.

Runtime owns:

- bell registry and scheduler;
- resident identity and authorization;
- retrieval policy resolution;
- context assembly;
- bell outcome and curation suppression;
- Runtime-native receipts.

The registry is descriptive and contract-validating. It cannot grant a capability merely because a manifest says the organ supports it. Every invocation still passes through executable MCP policy, the organ’s declared bounds, and the owning subsystem’s authority.

## Sense Organ Registry

### Manifest

Each organ has a versioned manifest stored in the MCP-owned state area, outside the canonical Archive.

Required fields:

| Field | Meaning |
|---|---|
| organ_id | Stable identifier, such as porchlight |
| schema_version | Manifest schema version |
| version | Organ implementation/contract version |
| display_name | Human-readable name |
| modality | What kind of perception is offered |
| activation_topology | What causes the organ to look |
| invocation_surface | The route that invokes it |
| consent_basis | The consent event required |
| perception_scope | The bounded material it may inspect |
| allowed_payloads | Payload classes it may return |
| prohibited_payloads | Explicitly excluded material |
| retention | Where and how long output may persist |
| destinations | Allowed output sinks |
| limits | Byte, item, and scope ceilings |
| receipt_schema | Receipt contract emitted by the organ |
| semantic_policy | Which fields may seed retrieval |
| status | Registered, disabled, or unavailable |

The first Porchlight manifest will declare:

- modality: browser-readable text with optional visible-viewport image;
- activation topology: explicit_invocation;
- invocation surface: paired Chrome extension through the loopback bridge;
- consent basis: the resident’s explicit Porchlight click;
- scope: selected readable text or one bounded readable page snapshot;
- prohibited payloads: raw HTML, scripts, styles, cookies, credentials, headers, and ambient page activity;
- retention: warm Archive evidence under the existing Porchlight direct-share contract;
- semantic policy: readable body may be searchable; control metadata and receipt boilerplate are not semantic seed terms.

### Activation topology

Activation topology is intentionally separate from perception scope:

- explicit_invocation: a resident or operator explicitly invokes the organ;
- event_triggered: a named event requests observation;
- scheduled: a scheduler invokes it under a declared schedule;
- ambient_bounded: a continuously available organ observes only a declared narrow signal.

An organ cannot silently become more perceptive because its implementation gains access to more data. A change to scope, activation, destination, retention, or limits requires a new manifest version and passes policy validation.

### MCP surface

The initial read-only surface is:

- sense.list: list registered organs and availability;
- sense.show: return one manifest and its digest;
- sense.can_perceive: evaluate a proposed observation against declared scope and policy without invoking the organ.

There is deliberately no generic sense.observe route. Each producer remains responsible for its own explicit, bounded capture contract, while the registry gives callers a common way to inspect that contract.

## Receipt Garden

Receipt Garden is an MCP-owned structured provenance store, not a searchable transcript shelf. It lives outside canonical Archive roots and does not make raw prompts, page bodies, model responses, cookies, or credentials durable by default.

### Receipt envelope

Every receipt contains bounded metadata such as:

- receipt_id;
- request_id;
- optional parent_receipt_id;
- actor/principal and resident/runtime identifiers where available;
- source layer;
- event type;
- subject/reference identifiers;
- timestamp;
- authority or policy scope;
- status/outcome;
- payload digest and byte count;
- omitted fields/material;
- budget fields where relevant.

Sensitive or large payloads are referenced by digest, path, stage ID, or existing subsystem receipt rather than copied into the Garden.

### Typed provenance edges

The first edge vocabulary is:

- observed: a bounded organ produced an observation;
- authorized: a policy/consent decision permitted an operation;
- queried: a retrieval request was formed;
- included: a candidate entered a bounded context layer;
- omitted: material was excluded, unavailable, or over budget;
- dispatched: a request crossed a subsystem boundary;
- stored: an artifact or receipt was persisted;
- returned: a result was exposed to a caller.

These edges establish deterministic provenance. They do not establish causal influence, resident adoption, preference, identity, or truth.

### MCP surface

The initial surface should support:

- recent receipt queries filtered by capability, outcome, and request_id;
- a bounded trace for one request_id;
- inspection of omitted material and budget accounting;
- explicit separation between MCP receipts and Runtime receipts.

The trace response should identify which layer supplied each fact and whether the linked record is a digest, evidence reference, policy decision, context inclusion, or outcome. A successful trace must not imply that every linked item was semantically influential.

## Bell Observatory

Bell Observatory is a Runtime component because Runtime owns the bell lifecycle and context assembly.

### Bell run record

For each fired bell, Runtime preserves an inspectable run record containing:

- resident/runtime ID and bell ID;
- firing and response turn IDs;
- orientation/context layer references loaded before the invitation;
- requested and effective retrieval policy;
- semantic source and normalized retrieval terms;
- control-plane fields excluded from retrieval;
- selected source scopes;
- included results and match reasons;
- omitted/unavailable results;
- requested, returned, included, and remaining context budgets;
- resident response classification;
- explicit outcome, including no_change;
- curation/memory action, including an explicit suppression reason;
- Runtime receipt identifiers.

The record describes context assembly and policy decisions. It does not claim to know which passage caused the model output.

### Runtime actions

The Runtime registry will expose focused, non-outward, read-only actions:

- bell.runs.list;
- bell.run.inspect;
- bell.run.replay;
- bell.policy.preview.

Replay operates on preserved decision inputs and a development/test copy of the retrieval path. It reports whether the same policy and inputs produce the same bounded retrieval receipt. It is not presented as a replay of model cognition.

Schedule mutation remains outside the first Observatory release. A future tuning surface may produce an explicit resident-approved bell revision proposal, but Observatory inspection must not mutate schedules as a side effect.

### MCP projection

MCP discovers these actions through the existing Runtime capability projection:

1. runtime.capabilities reports the live Runtime contract.
2. runtime.call validates the projected schema and dispatches through HousePort.
3. Runtime performs its own authorization, validation, and receipt creation.
4. MCP records its receipt with the same cross-layer request_id.
5. Receipt Garden links the two receipt layers without collapsing their authority.

MCP must not import Runtime database tables or maintain a copied bell ontology. Named convenience tools may be added later only if they remain thin projections of the same Runtime actions.

## Bell retrieval contract

The Observatory records both requested and effective retrieval policy.

Supported policies remain:

- auto;
- none;
- prompt_only;
- response_related;
- resident_selected.

The effective policy must exclude bell control-plane metadata such as ID, title, schedule, purpose labels, authorization text, and fixed boilerplate from semantic retrieval terms.

For generic invitations, auto may resolve to the existing bounded field scan. For topic-bearing invitations, it may resolve to prompt_only. response_related is deferred until an explicit later request; it never searches retroactively into an unobserved response.

An explicit resident no_change response records the outcome and suppresses automatic curation for that turn. Silence is not equivalent to no_change.

## Data flow

1. Runtime fires a bell and records the bell/run identifiers.
2. Runtime assembles orientation context.
3. Runtime resolves the retrieval policy and excludes control-plane boilerplate.
4. Runtime records candidate matches, omissions, budgets, and context inclusion.
5. The bell invitation is delivered and remains the attention-salient current message.
6. The resident responds or explicitly chooses no change.
7. Runtime records the outcome and any curation suppression.
8. MCP/Runtime receipts are joined by request and turn identifiers.
9. Observatory and Receipt Garden expose the deterministic receipt for inspection or replay.

## Failure and consistency behavior

- Unknown organs, unsupported manifest versions, and scope expansions fail closed.
- An unavailable source is reported as unavailable, not as zero results.
- A missing receipt join is reported as incomplete provenance, not silently reconstructed.
- Receipt writes are atomic and bounded; a failed receipt must not fabricate an observed or stored edge.
- Replay reports input/configuration drift explicitly.
- Retention expiry removes or redacts receipt detail according to configured policy while preserving a bounded tombstone when required for audit continuity.
- A disabled organ remains listed with status and reason; callers cannot invoke it through the registry.

## Testing strategy

### MCP tests

- Manifest schema validation and digest stability.
- Porchlight registration with explicit invocation and declared scope.
- Scope-expansion rejection.
- Registry listing/show/capability checks.
- Receipt envelope hashing and sensitive-field omission.
- Typed trace joins across MCP and Runtime receipt references.
- Budget and omission fields remain distinct.
- Unknown organ and disabled-organ fail-closed behavior.

### Runtime tests

- Bell runs persist requested/effective retrieval policies.
- Bell boilerplate never contributes retrieval terms.
- Identical bells with different resident-relevant histories produce history-sensitive retrieval receipts.
- Scheduler edits do not become semantic retrieval terms.
- none, prompt_only, response_related, and resident_selected behave distinctly.
- Explicit no_change suppresses automatic curation.
- Replay is deterministic for decision inputs and reports drift.
- Included context is never labeled causal by default.
- Observatory actions remain non-outward and read-only.

### Cross-layer tests

- MCP runtime.call reaches the Runtime Observatory through HousePort.
- Shared request_id joins MCP and Runtime receipts.
- Runtime database is not read directly by MCP.
- Runtime capability removal makes the projected action unavailable.
- Unavailable, omitted, zero-result, and truncated states remain distinguishable.
- A complete trace remains inspectable after one layer has no semantic result.

## Delivery sequence

1. Add the shared manifest and receipt schemas in MCP-owned state.
2. Register Porchlight and expose the three read-only registry tools.
3. Add Receipt Garden storage/query/trace behavior.
4. Add Bell Observatory records and read-only Runtime actions.
5. Add MCP Runtime projection tests and cross-layer request joining.
6. Add bell replay fixtures and anti-boilerplate regression cases.
7. Document resident/operator inspection workflows.
8. Defer schedule mutation, additional organs, and dashboard work until this seam is exercised.

## Success criteria

The first release is successful when a resident can ask:

> Why did this bell know this?

and receive a bounded answer that distinguishes:

- what an organ could perceive;
- what explicitly activated it;
- what policy authorized;
- what retrieval terms were used;
- what entered context and why;
- what was omitted;
- what the Runtime dispatched;
- what was stored or returned;
- and what remains unknown about model causality.

Porchlight should be the first demonstration, not a special case. The contract is useful only if a second future organ can register without changing the provenance or Observatory model.
