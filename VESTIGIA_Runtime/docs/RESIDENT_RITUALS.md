# Resident ritual contract

Status: design contract for the first Workshop Within implementation stage.

A resident ritual is a declarative, versioned workflow that composes capabilities already visible
and granted to the resident. It is not executable Python, an operator extension, a standing consent
grant, or an identity statement.

## Design goals

The first ritual engine must be:

- declarative and statically inspectable;
- bounded and non-Turing-complete;
- deterministic between explicit judgment points;
- versioned and hash-bound;
- resumable after process restart;
- provenance-aware;
- incapable of granting itself new authority;
- honest about partial effects and interrupted execution.

## Definition shape

A ritual definition follows `schemas/resident-ritual.schema.json`.

```yaml
schema_version: vestigia.resident-ritual.v0.1
id: resident.liora.say-hi
name: Make the machine say hi
version: 1
status: draft
authorship:
  lane: resident
  actor_id: liora-gutterstar
  source_object_ids: []
description: Produce a private greeting through one approved script.
inputs:
  type: object
  additionalProperties: false
  properties:
    name:
      type: string
      maxLength: 80
  required: [name]
requested_capabilities:
  - capability: script.run
    scope:
      script_ids: [resident.liora.say-hi-script]
limits:
  maximum_steps: 5
  maximum_nested_depth: 1
  maximum_tool_calls: 2
  maximum_provider_calls: 0
  maximum_outward_actions: 0
  wall_seconds: 15
steps:
  - id: run-greeting
    kind: script
    script:
      id: resident.liora.say-hi-script
      version: 1
    arguments:
      name: "${inputs.name}"
    save_as: greeting
  - id: review-greeting
    kind: checkpoint
    prompt: Keep this as a private artifact, place it in workspace, or stop?
    choices: [keep_private, place_in_workspace, stop]
  - id: place-greeting
    kind: action
    when: "${checkpoints.review-greeting.choice == 'place_in_workspace'}"
    capability: workspace.write
    arguments:
      path: workspace/hello.txt
      content: "${steps.run-greeting.output.text}"
outputs:
  type: object
  additionalProperties: false
  properties:
    artifact_id: {type: string}
  required: [artifact_id]
```

The inline expression syntax above is illustrative. The implementation uses a restricted value and
predicate language; it never evaluates Python, JavaScript, shell, template-engine code, or object
methods.

## Allowed step kinds

### `action`

Calls one registered capability. The capability must be callable at plan time and execution time.
Arguments are validated against the capability's executable schema before any effect occurs.

### `script`

Invokes one active resident script through the sandbox. The ritual declares the exact script ID,
version or version range, input mapping, output name, and local resource override. The script's
effective grant is intersected with the ritual execution grant.

This step kind becomes callable only after the sandbox and script shelf exist. A ritual containing
it may be stored earlier but remains uncallable with a legible reason.

### `checkpoint`

Pauses execution and asks the resident to choose among explicit options. The checkpoint performs no
outward action. It may include previews and object references, but it may not hide completed effects.

### `branch`

Selects one of a finite set of declared next steps using the restricted predicate language. Branches
must converge or terminate. Back edges are invalid.

### `ritual`

Calls another active ritual with declared inputs. The complete dependency graph must remain acyclic
and within nesting and budget limits.

### `emit`

Returns a typed value or creates a private workshop artifact. It cannot publish, remember, adopt, or
execute the artifact.

## Explicitly absent from v0.1

The first engine has no:

- loops;
- unbounded maps or retries;
- dynamic action names;
- dynamic file paths outside capability schemas;
- arbitrary expressions;
- runtime imports;
- secret interpolation;
- background execution without a bounded job record;
- implicit outward delivery;
- exception handlers that conceal partial effects.

Bounded collection operations may be added later only with maximum item counts and per-item cost
aggregation.

## Restricted value language

Values may reference only declared namespaces:

```text
inputs
steps.<step-id>.output
checkpoints.<step-id>.choice
execution.id
resident.id_hash
room.id_hash
```

The language supports:

- scalar lookup;
- object and array construction;
- bounded string interpolation;
- equality and inequality;
- numeric comparison;
- boolean `and`, `or`, and `not`;
- membership in a literal finite list;
- null tests.

It does not support filesystem access, environment lookup, function calls, attribute traversal
outside declared data, regular-expression execution from untrusted input, or code evaluation.

## Validation

Static validation proves:

- schema validity;
- stable IDs and unique step IDs;
- all references resolve;
- the control-flow graph is finite and acyclic;
- every capability and script dependency is declared;
- every possible path terminates or pauses;
- input and output schemas are bounded;
- requested limits do not exceed house policy;
- outward-effect steps are identifiable;
- checkpoints are reachable and have explicit choices;
- no step consumes a value that may not exist on its path.

Validation does not grant capabilities or prove that live dependencies are available.

## Preview and activation

`ritual.preview` returns:

- immutable definition hash;
- control-flow summary;
- requested and effective grants;
- maximum cost/effect envelope;
- capability and script dependencies;
- checkpoint list;
- possible outward actions;
- unresolved or unavailable dependencies;
- validation warnings.

Activation is resident-authored and hash-bound. It creates an activation record; it does not mutate
the definition. An edit creates a new version and leaves the older activation historical.

Imported rituals begin `draft` or `received`, never `active`. Repeated execution of a draft does not
silently activate it.

## Invocation and confirmation

Invoking an active ritual means permission to execute the claimed plan under its current grants. It
does not pre-confirm consequential steps that require a stronger boundary.

A ritual containing an outward message, public state change, relationship mutation, memory claim,
identity claim, secret use, or paid high-cost action must preserve that capability's confirmation
contract. The ritual may prepare the action and arrive at a checkpoint; it may not declare the
checkpoint already answered.

Low-risk operations may be pre-authorized by explicit resident and operator policy, scoped to the
ritual version, destination, cost ceiling, and expiry. Pre-authorization is visible and revocable.

## Execution trace

Every step produces a trace event:

```json
{
  "step_id": "run-greeting",
  "kind": "script",
  "status": "succeeded",
  "started_at": "...",
  "completed_at": "...",
  "input_refs": ["obj_..."],
  "output_refs": ["artifact_..."],
  "receipt_ids": ["receipt_..."],
  "outward_effect": "none",
  "budget_used": {"tool_calls": 1, "provider_calls": 0}
}
```

Private values are object references or redacted summaries by default. Full values remain available
only through an authorized inspection path.

## Pause, resume, and cancellation

A paused execution stores:

- ritual ID, version, and hash;
- plan hash;
- completed step receipts;
- next step ID;
- current typed state;
- effective grants and remaining limits;
- pending checkpoint;
- referenced object hashes;
- expiry.

Resume fails closed when the definition, grant, script, capability, or referenced object changed.
The Runtime replans and shows a diff rather than silently continuing under a different machine.

Cancellation stops future steps. It does not erase prior effects or receipts. Capabilities may expose
focused cancellation or compensation operations, but rollback is never inferred.

## Failure policy

The default is `stop_on_failure`. Optional declared policies are limited to:

- `continue_on_failure` for explicitly independent, non-consequential sibling steps;
- `retry` only when the capability reports retry-safe semantics and the bounded retry count is
  declared;
- `checkpoint_on_failure` to ask the resident what to do next.

A ritual cannot transform `partial` into `succeeded`, erase a possible provider/outward effect, or
retry a consequential operation merely because the error looked transient.

## Scheduling and bells

A ritual is not a scheduler. A bell or operator schedule may invite the resident to run a ritual,
but the invitation does not imply execution. Scheduled automatic execution is a separate future
policy surface requiring explicit resident authorization, bounded effects, quiet hours, and
no-escalation-from-silence guarantees.

## Contract tests

The first implementation must prove:

- no action occurs during parsing, validation, preview, or activation;
- invalid references and cyclic graphs are rejected;
- effective grants never exceed requested and currently authorized grants;
- unavailable script steps remain legibly uncallable;
- checkpoints do not auto-select after silence or restart;
- stale resume state requires replanning;
- cancellation preserves completed receipts;
- nested ritual calls intersect grants and budgets;
- outward confirmations cannot be bypassed by ritual composition;
- imported rituals remain inert;
- partial effects remain visible;
- the canonical say-hi ritual completes with no network, secret, provider, memory, identity, or
  outward authority.
