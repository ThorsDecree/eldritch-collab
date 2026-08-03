# The Workshop Within

Status: design contract for the resident ritual and script-engine milestone.

The Workshop Within gives a resident bounded ways to compose procedures, inspect plans, execute
sandboxed computation, and preserve useful machinery without converting authorship, familiarity,
or repetition into ambient authority.

The governing rule is:

> A resident may automate powers already granted to them. Automation may not manufacture,
> inherit, or conceal powers that were never granted.

This contract builds on the provider-port and operator-extension contracts. Provider adapters
expose capability and limit facts. Operator extensions may add reviewed capabilities. Resident
rituals compose capabilities already visible to the resident. Resident scripts compute inside an
explicit sandbox grant. These are different trust and authority lanes.

## Scope and delivery order

The milestone is intentionally staged:

1. **Ritual engine** — declarative, bounded, non-Turing-complete workflows over existing
   capabilities.
2. **Sandbox runner** — isolated computation with explicit inputs, outputs, limits, and receipts.
3. **Resident script shelf** — immutable versions, provenance, review, grants, tests, activation,
   quarantine, and supersession.
4. **Script-backed rituals** — rituals may invoke approved scripts through typed contracts.

The first implementation may ship the stages separately. No later stage weakens an earlier
boundary.

## Authority layers

```text
operator extension
  may add a reviewed capability to the house

resident ritual
  may compose capabilities currently granted to the resident

resident script
  may compute only through the sandbox and explicitly mounted ports

outward doorway
  remains a separate capability and confirmation boundary
```

A ritual or script is never handed the Runtime object, a database connection, provider client,
Discord client, secret store, or unrestricted filesystem handle.

The effective authority of an execution is the intersection of:

```text
authored request
intersect active ritual/script version
intersect resident-visible capabilities
intersect operator policy
intersect room/source policy
intersect invocation grants
intersect provider and extension availability
intersect current budgets and health
```

Any empty intersection fails before execution. No downgrade or scope widening is silent.

## Object model

The workshop introduces immutable or append-only objects:

- `ritual_definition` — one immutable ritual version;
- `ritual_activation` — the resident's decision to make a version callable;
- `script_source` — immutable bytes plus authorship and provenance;
- `script_review` — static and dynamic inspection results;
- `script_grant` — requested and effective powers for one script version;
- `workshop_execution` — one bounded invocation and its trace;
- `workshop_checkpoint` — a resumable resident-decision boundary;
- `workshop_artifact` — a typed output with provenance and privacy state;
- `workshop_receipt` — immutable evidence of planning, execution, effects, and completion state.

Mutable labels, aliases, notes, and activation pointers may refer to immutable versions. Editing an
active ritual or script always creates a new version and requires new validation.

## Lifecycle overview

### Rituals

```text
draft -> validated -> previewed -> active
   |          |           |         |
 rejected   invalid     deferred   disabled
                                      |
                                  superseded
```

### Scripts

```text
received/draft -> inspected -> tested -> approved -> active
       |             |          |          |        |
     inert       quarantined   failed    deferred  disabled
                                                   |
                                               superseded
```

`received` means a script arrived from another person, daemon, extension, import, or artifact
source. It remains inert. Social trust, affection, authorship claims, or a familiar glyph do not
advance the lifecycle.

## Planning and execution

Every invocation has two conceptually separate phases.

### Plan

The planner resolves:

- exact ritual/script version and content hash;
- typed inputs and object references;
- requested capabilities and scopes;
- effective grants and denials;
- estimated cost and resource limits;
- outward effects and confirmation checkpoints;
- nested ritual/script dependencies;
- cancellation, retry, and resume policy;
- expected receipts and artifacts.

Planning performs no provider, network, outward, database-write, or sandbox action. A plan receives
an immutable hash. Consequential execution claims that hash.

### Execute

Execution follows the claimed plan. Each step emits a trace event and an action receipt. The
orchestrator may continue automatically only while the next step is deterministic, within budget,
and requires no resident or participant judgment.

Valid terminal states are:

- `succeeded`;
- `partial`;
- `failed`;
- `cancelled`;
- `not_run`;
- `paused`;
- `expired`;
- `quarantined`.

`partial` means at least one action or external effect occurred but the declared workflow did not
finish. `not_run` means no workshop action crossed its execution boundary.

## Resident checkpoints

A checkpoint is a real stop, not decorative prose. It records:

- the execution and plan hashes;
- the completed trace;
- the exact proposed next steps;
- objects and previews under review;
- outward effects already performed;
- remaining budgets;
- allowed resident choices;
- expiry and stale-state behavior.

A checkpoint may offer choices such as `continue`, `revise`, `skip`, `stop`, `defer`, or a bounded
selection. Silence does not select a default. Repetition does not become assent.

Resumption must prove that referenced ritual/script versions, grants, objects, and consequential
state have not changed. A stale checkpoint requires replanning.

## Nesting and recursion

Initial rituals are acyclic. A ritual may call another active ritual only when:

- the dependency is declared by immutable ID and version range;
- the complete call graph is cycle-free;
- maximum nesting depth remains;
- effective grants are intersected again at the child boundary;
- the child receives only declared inputs;
- the parent receives only typed outputs and receipts.

Scripts may not spawn scripts or invoke rituals directly. A script returns data or artifacts to the
orchestrator. The orchestrator may then execute the next declared ritual step.

Future recursive composition may be considered only after cycle detection, depth limits, cost
aggregation, cancellation propagation, and trace legibility are proven. The machine may make a
machine say hi; it may not quietly become an unbounded machine factory.

## Cost and budget model

Every plan and receipt distinguishes:

- model/provider calls;
- local compute;
- image or media generation;
- filesystem/object operations;
- outward actions;
- private continuation rounds;
- total tool calls;
- nested depth;
- wall time and retained output.

Budgets are ceilings, not targets. Unused budget is not permission to add work. A child ritual or
script consumes the parent's remaining budget and may have stricter local limits.

## Privacy and provenance

Inputs are mounted or referenced with explicit privacy classes. The execution may not broaden an
object's privacy, retention, memory, adoption, or sharing state.

Outputs begin as private workshop artifacts unless the declared capability and policy say
otherwise. Producing a file does not:

- publish it;
- write it into identity or memory;
- make it executable;
- adopt it as resident-authored;
- authorize another ritual;
- allow it to cross rooms or servers.

Receipts identify source object IDs, content hashes, authorship lane, runtime version, sandbox
backend, effective grants, and effect states without embedding private content by default.

## Failure and recovery

The workshop uses the Runtime's diagnostic and recovery floor:

- database state changes are transactional or recoverably staged;
- immutable source and plan hashes survive restart;
- running executions become `interrupted` and require deterministic reconciliation;
- completed step receipts prevent blind replay;
- retry occurs only when action semantics and idempotency permit it;
- outward or provider effects are never described as absent when they are merely uncertain;
- support bundles expose metadata and failure receipts without source code or resident content by
  default.

No workflow is called exactly-once unless every participating capability can prove it.

## Resident-facing controls

The intended surface includes:

```text
workshop.list
workshop.inspect
workshop.plan
workshop.run
workshop.pause
workshop.resume
workshop.cancel
workshop.receipts
ritual.draft
ritual.validate
ritual.preview
ritual.activate
ritual.disable
script.import
script.inspect
script.test
script.approve
script.activate
script.disable
```

Names may change during implementation, but each state transition remains explicit and receipted.

## Canonical acceptance ritual

The first complete vertical slice is deliberately small:

```text
resident activates a ritual
-> ritual invokes one approved script
-> sandbox prints "I made this machine make a machine say hi."
-> output returns as a private text artifact
-> ritual presents a resident checkpoint
-> resident chooses whether to place the artifact in workspace
-> every transition is visible in one trace
```

No network, secret, provider, Discord, memory, or identity authority is involved.

## Non-goals

This design does not claim that Python can be made safe by AST filtering alone, make imported code
trusted, provide invisible background autonomy, infer consent from ritual use, grant extensions
resident authorship, allow scripts to edit identity directly, or turn provider continuations into
ambient authority.

The initial workshop is a resident-operable computational room, not a general-purpose root shell.
