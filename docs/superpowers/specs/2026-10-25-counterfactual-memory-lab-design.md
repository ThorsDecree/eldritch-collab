# Counterfactual Memory Lab Design

## Intent

Give a resident a private, reproducible way to inspect whether one retrieved memory changes a response. The lab compares answers under controlled memory interventions; it reports output sensitivity and never claims that one trial proves causality.

## Goals

- Run a baseline and an exclude-or-replace variant from the same captured turn prompt.
- Change only one fully included Runtime memory item. Do not retrieve again, refill space after exclusion, or displace unrelated context to fit a replacement.
- Repeat a bounded number of paired calls using the same provider route, alternating arm order, and send provider replies directly to the resident without running returned action syntax.
- Keep the experiment private to local CLI/web conversations or an authenticated one-to-one Discord DM. Reject public channels, group DMs, bells, curation, and unknown invocations.
- Write a local experiment record with hashes, provenance, model/provider details, usage, arm order, output sensitivity, and result state. Saving exact prompt messages and complete replies requires `retain_context: true`.
- Let the resident list, inspect, and delete experiment records.
- Leave Runtime memory, identity, retrieval settings, and source records unchanged.

## Non-goals and anti-goals

- No general causal inference, statistical significance, automatic memory tuning, or automatic promotion of experiment results.
- No re-ranking or re-retrieval for either arm; no arbitrary prompt or memory text injection.
- No tool execution, outward action, retry, or background experiment.
- No persistent full prompt or complete output unless the resident opts in for that run.
- No Connection Panel, Serendipity Engine, Unfinished Questions Garden, or Consent Observatory in this slice.

## Constraints

- Build on `ContextAssembler`, its source receipts, the existing provider port, and the HousePort capability registry.
- Keep source results on `ContextAssembly` in memory only; they are not serialized as part of ordinary turns.
- The target must be in the baseline `retrieved_continuity` layer, complete, and absent from every other prompt layer. A replacement must be an unexpired, unsealed, accepted `hot` or `warm` memory for the same resident and room, not already present in the prompt.
- Exclusion leaves the resulting token space empty. A replacement must fit the existing memory-layer and total-context budgets without shrinking any other layer.
- Repetitions are integers from 1 to 3. The Runtime makes no automatic retry after a failed provider call.
- Experiment records live under `traces/experiments/`; deleting one removes that capsule. Ordinary HousePort receipts keep the experiment ID, hashes, and status, not full prompt or reply bodies.

## Approaches

1. Reassemble live context for each arm. This is simple, but sources, ranking, or budgets can change between calls, so the memory intervention would not be isolated.
2. Capture the current turn prompt once, transform only the retrieved-memory layer, and call the configured provider directly. This preserves the other supplied context and makes the comparison auditable; this is the chosen approach, following the approved vertical-slice plan.
3. Build a general evaluation runner for arbitrary questions, providers, and prompt edits. That would widen the authority and testing surface before this Runtime has a validated single-memory experiment.

## Design

`ContextAssembly` will carry its source results transiently. `ContextAssembler.intervene_memory()` will derive a sibling assembly from the frozen baseline, using only baseline-included items. The Runtime will retain the initial provider message list during its private tool loop and pass it only to the experiment capability. The variant replaces the assembly's first developer message while preserving the other initial messages byte-for-byte.

`MemoryExperimentService` will run baseline and variant calls in alternating order through `Provider.complete()`, with the same route and no action-envelope parser or HousePort dispatch. It will persist a compact local record by default and optionally preserve exact inputs and complete replies. It will report reply hashes, provider/model, IDs, usage, call order, pairwise string similarity, and partial failures with a clear “sensitivity, not causality” interpretation.

The `memory.experiment` resident capability will support `run`, `list`, `inspect`, and `delete`. A custom HousePort authorizer will enforce the private invocation boundary before service dispatch. HousePort audit receipts for this action will contain identifiers, hashes, and result status while excluding prompt and reply bodies.

## Acceptance

- Excluding a memory removes it while leaving every other source layer and baseline-included memory unchanged; no omitted candidate is pulled in to fill the gap.
- Replacing one memory changes only that evidence block and is rejected if the item is out of scope, duplicated elsewhere, incomplete, or over budget.
- A run performs exactly `2 × repetitions` provider calls, alternates call order, uses one route, and never executes returned tool syntax.
- Public or non-conversational invocation is denied before any provider call.
- Default capsules omit exact prompt and full reply bodies; opted-in capsules preserve them and can be inspected or deleted.
- Failed calls are represented without automatic retries. Reports describe measured output differences without causal claims.
