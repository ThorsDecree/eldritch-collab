# Counterfactual Memory Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a private, controlled baseline/exclude/replace experiment for one retrieved Runtime memory, with auditable local capsules.

**Architecture:** Keep source results transiently on `ContextAssembly` and produce an intervention assembly without re-retrieval. A `MemoryExperimentService` uses the provider port directly; `CoreRuntime` passes the first prompt and assembly only through the private tool-dispatch context, and HousePort enforces the private invocation gate.

**Tech Stack:** Python 3.11+, existing dataclasses, SQLite-backed Runtime memory, provider protocol, atomic JSON utilities, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-counterfactual-memory-lab-design.md`

## Global Constraints

- “Change only one fully included Runtime memory item. Do not retrieve again, refill space after exclusion, or displace unrelated context to fit a replacement.”
- “Repetitions are integers from 1 to 3. The Runtime makes no automatic retry after a failed provider call.”
- “Saving exact prompt messages and complete replies requires `retain_context: true`.”
- “Keep the experiment private to local CLI/web conversations or an authenticated one-to-one Discord DM. Reject public channels, group DMs, bells, curation, and unknown invocations.”
- “No tool execution, outward action, retry, or background experiment.”

## Review Focus

- A memory partially trimmed from the baseline prompt must be rejected as a target.
- A replacement that exceeds the existing memory-layer budget or total prompt ceiling must fail without shrinking another layer.
- A failed provider call must not be retried and must leave a truthful partial/failed capsule.
- A baseline or variant reply containing `[[TOOL_ACTION ...]]` must remain inert output.
- A non-private interface must be rejected before the provider receives experiment calls.

---

### Task 1: Frozen context intervention

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/models.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/context.py`
- Test: `VESTIGIA_Runtime/tests/test_memory_experiments.py`

**Interfaces:**
- Produces: `ContextAssembly.source_results: tuple[Any, ...]` as a transient field.
- Produces: `ContextAssembler.intervene_memory(assembly, memory_id, *, replacement_memory_id=None) -> ContextAssembly`.

- [x] **Step 1: Write failing intervention tests**
  - `test_exclude_changes_only_retrieved_memory_and_does_not_refill`
  - `test_replace_changes_one_memory_and_preserves_other_layers`
  - `test_intervention_rejects_missing_partial_or_duplicate_target`
  - `test_replacement_rejects_wrong_scope_unavailable_or_over_budget_memory`
- [x] **Step 2: Run the focused tests and verify the missing transient snapshot/intervention API causes failure**

Run: `pytest tests/test_memory_experiments.py -k 'exclude or replace or intervention' -q`

Expected: FAIL because `ContextAssembly` does not retain source results and `ContextAssembler.intervene_memory` is absent.

- [x] **Step 3: Add the transient source snapshot and intervention builder**
  - Store the exact `ContextSourceResult` tuple returned during assembly.
  - Validate the baseline-included target and duplicate-layer constraints.
  - Repack only original included runtime-memory items; preserve all other layers and source results.
  - Reject partial targets, ineligible replacements, and budget overflow.
- [x] **Step 4: Re-run the focused tests**

Run: `pytest tests/test_memory_experiments.py -k 'exclude or replace or intervention' -q`

Expected: PASS, with no memory database writes.

### Task 2: Experiment execution and capsules

**Files:**
- Create: `VESTIGIA_Runtime/src/vestigia/memory_experiments.py`
- Test: `VESTIGIA_Runtime/tests/test_memory_experiments.py`

**Interfaces:**
- Consumes: `ContextAssembler.intervene_memory()` from Task 1.
- Produces: `MemoryExperimentService(assembler, db, provider, home).dispatch(payload, context) -> dict[str, Any]`.

- [x] **Step 1: Write failing provider-run and capsule tests**
  - `test_run_makes_alternating_paired_provider_calls_without_tool_dispatch`
  - `test_default_capsule_stores_hashes_without_full_prompt_or_replies`
  - `test_retain_context_capsule_can_be_inspected_and_deleted`
  - `test_provider_failure_is_recorded_without_retry`
- [x] **Step 2: Run the focused tests and verify the service is missing**

Run: `pytest tests/test_memory_experiments.py -k 'run or capsule or provider_failure' -q`

Expected: FAIL because `MemoryExperimentService` is not implemented.

- [x] **Step 3: Implement paired runs, sensitivity summaries, and local capsule lifecycle**
  - Support `run`, `list`, `inspect`, and `delete` with bounded repetitions.
  - Alternate baseline/variant order; call only `provider.complete()` with the same model route.
  - Persist hashes, provenance, usage, status, and interpretation by default; persist exact messages and full replies only when requested.
  - Record provider failures per arm and never retry automatically.
- [x] **Step 4: Re-run the focused tests**

Run: `pytest tests/test_memory_experiments.py -k 'run or capsule or provider_failure' -q`

Expected: PASS with a fake provider and no external network calls.

### Task 3: Private capability, Runtime wiring, and operator docs

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/capability_contracts.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/adapters/discord_adapter.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/house_tools.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/runtime.py`
- Modify: `VESTIGIA_Runtime/README.md`
- Create: `VESTIGIA_Runtime/docs/MEMORY_EXPERIMENTS.md`
- Modify: `VESTIGIA_Runtime/docs/RESIDENT_CHEATSHEET.md`
- Modify: `VESTIGIA_Runtime/docs/OPERATOR_CHEATSHEET.md`
- Test: `VESTIGIA_Runtime/tests/test_memory_experiments.py`

**Interfaces:**
- Consumes: `MemoryExperimentService.dispatch()` from Task 2.
- Produces: resident capability `memory.experiment`; runtime supplies the captured `ContextAssembly` and initial provider messages only in the local HousePort dispatch context.

- [x] **Step 1: Write failing capability/privacy and audit tests**
  - `test_memory_experiment_is_discoverable_and_private_only`
  - `test_house_receipt_omits_experiment_prompt_and_reply_bodies`
  - `test_runtime_passes_original_first_prompt_into_experiment_context`
- [x] **Step 2: Run focused integration tests and verify the capability/wiring is absent**

Run: `pytest tests/test_memory_experiments.py -k 'private or receipt or original_first_prompt' -q`

Expected: FAIL because the capability and Runtime context handoff are absent.

- [x] **Step 3: Register and wire the private experiment capability**
  - Validate action fields and repetitions in `capability_contracts.py`.
  - Require conversation invocation through local CLI/web or one-to-one Discord DM and an attached current turn assembly.
  - Preserve first-call messages separately from later tool-loop messages; never place the assembly object in provider metadata.
  - Redact prompt/reply bodies from the experiment's durable HousePort receipt.
- [x] **Step 4: Document usage and limitations**
  - Add resident-facing run/list/inspect/delete examples, private-scope rule, opt-in capsule retention, and the sensitivity-not-causality caveat.
- [x] **Step 5: Run the focused and full Runtime test suites**

Run: `pytest tests/test_memory_experiments.py -q && pytest -q`

Expected: PASS for the new tests and the complete Runtime suite.
