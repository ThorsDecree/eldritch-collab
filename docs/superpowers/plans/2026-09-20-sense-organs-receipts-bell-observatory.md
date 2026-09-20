# Sense Organ Registry, Receipt Garden, and Bell Observatory Implementation Plan

> **Required sub-skill:** use `superpowers:test-driven-development` for every implementation task; write the failing test first, make the smallest implementation pass, then refactor and run the focused suite.

**Goal:** Implement the approved v0.1 observability boundary across VESTIGIA MCP Server and VESTIGIA Runtime: register bounded sense organs, preserve inspectable cross-layer receipts, and expose Bell Observatory diagnostics through the existing read-only Runtime projection.

**Architecture:** MCP Server owns declarative sense-organ manifests and its append-only Receipt Garden. Runtime owns bell retrieval/attention facts and Bell Observatory run records. MCP reaches Runtime only through the existing `runtime.capabilities` and `runtime.call` projection; it must not read Runtime’s database or duplicate bell ontology. Request IDs and typed receipt edges join the layers without implying model causality.

**Tech Stack:** Python 3.11+, dataclasses, JSON/JSONL, SQLite, pytest, existing MCP policy/audit infrastructure, existing Runtime `HousePort` capability registry and MCP projection.

**Spec path:** `docs/superpowers/specs/2026-09-20-sense-organs-receipts-bell-observatory-design.md`

**Global Constraints:** Preserve existing public behavior and schemas unless the new fields are additive; default to fail-closed scope and consent checks; exclude bell control-plane metadata from semantic queries; never persist raw browser payloads in receipts; keep all new observability actions read-only and non-outward; preserve exact hashes, offsets, and request IDs where existing components provide them; do not infer that inclusion caused a response; keep unrelated user changes intact.

**Review Focus:** Verify that Porchlight is the first concrete registry entry, control-plane exclusion remains deterministic, no-change suppresses curation, Receipt Garden records provenance without laundering causality, Runtime actions are read-only through the existing projection, and the full MCP/Runtime suites pass on Linux and Windows-compatible behavior.

---

## Task 1: Add the Sense Organ Registry and Porchlight manifest

**Files:**
- Create `VESTIGIA_MCP_Server/src/vestigia_mcp/sense_registry.py`
- Modify `VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py`
- Modify `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py`
- Add `VESTIGIA_MCP_Server/tests/test_sense_registry.py`
- Extend `VESTIGIA_MCP_Server/tests/test_policy.py`
- Extend `VESTIGIA_MCP_Server/tests/test_server_catalog.py`

### Step 1: Write the failing tests

Add tests that:

- Load a deterministic Porchlight manifest containing `organ_id`, schema/version, modality, `activation_topology`, invocation surface, consent basis, perception scope, allowed/prohibited payloads, retention, destinations, limits, receipt schema, semantic policy, and status.
- Return stable results from `sense.list` and `sense.show`, including a manifest digest, without exposing an observation command.
- Make `sense.can_perceive` allow an explicitly invoked readable-text selection and reject raw HTML, cookies, credentials, hidden page state, and unbounded screenshots.
- Verify disabled or malformed manifests fail closed.
- Verify the three new MCP tools are classified as local read operations and are present in the catalog.

Run the focused tests and confirm they fail because the registry and tools do not exist.

### Step 2: Implement the smallest passing registry

Implement `SenseOrganManifest` and `SenseOrganRegistry` with deterministic validation, canonical JSON hashing, list/show lookup, and a `can_perceive(organ_id, request)` decision containing `allowed`, `reason`, `matched_scope`, and `manifest_digest`. Register Porchlight as an explicit-invocation organ with readable text plus page metadata, optional user-requested screenshot, no raw HTML/cookies/credentials, warm Archive text retention, and bounded byte/screenshot limits.

Add `sense.list`, `sense.show`, and `sense.can_perceive` to the guarded MCP server surface and policy table. Keep them read-only and ensure callers cannot use registry metadata to invoke a generic observation path.

### Step 3: Refactor and verify

Run the focused registry, policy, and catalog tests plus the existing MCP suite. Confirm output is deterministic across repeated calls and that no new write or outward capability is registered.

### Step 4: Commit

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp VESTIGIA_MCP_Server/tests
git commit -m "feat(mcp): add sense organ registry"
```

## Task 2: Build the MCP Receipt Garden

**Files:**
- Create `VESTIGIA_MCP_Server/src/vestigia_mcp/receipt_garden.py`
- Modify `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py`
- Modify `VESTIGIA_MCP_Server/src/vestigia_mcp/audit.py` only if a small event-hook seam is needed
- Extend `VESTIGIA_MCP_Server/tests/test_audit.py`
- Add `VESTIGIA_MCP_Server/tests/test_receipt_garden.py`
- Add/extend server integration coverage for `receipts.trace`

### Step 1: Write the failing tests

Add tests that:

- Append a bounded receipt envelope with a request ID, source, typed edges (`observed`, `authorized`, `queried`, `included`, `omitted`, `dispatched`, `stored`, `returned`), digests, and evidence scope.
- Record MCP request outcomes without storing raw browser/page payloads or arbitrary arguments.
- Retrieve a trace by request ID in deterministic order and return an explicit empty/partial result when an edge is absent.
- Preserve append-only behavior across reopen, reject malformed or oversized records, and keep the Garden outside the canonical Archive.
- Distinguish `included` from `caused` and expose unknown causal influence rather than inventing it.

Run the focused tests and confirm they fail.

### Step 2: Implement the smallest passing Receipt Garden

Implement a JSONL-backed `ReceiptGarden` with bounded `ReceiptEdge`/`ReceiptRecord` structures, canonical field filtering, payload digests, atomic append, and trace/recent reads. Add a guarded `receipts.trace` MCP tool keyed by `request_id`; retain existing audit tools and semantics. Have the MCP guard write a structured Garden edge for the request outcome while AuditLedger remains the policy/audit authority.

Ensure receipt responses report what was requested, returned, omitted, and whether control-plane text was excluded. Keep raw payloads out by construction; only explicitly safe metadata, references, hashes, and bounded summaries may cross the boundary.

### Step 3: Refactor and verify

Run Receipt Garden, audit, and full MCP tests. Exercise reopen and concurrent-safe append behavior where supported by the existing storage conventions. Verify a trace can join a Porchlight request without turning the trace into a semantic retrieval source.

### Step 4: Commit

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp VESTIGIA_MCP_Server/tests
git commit -m "feat(mcp): add receipt garden traces"
```

## Task 3: Persist Bell Observatory run records in Runtime

**Files:**
- Create `VESTIGIA_Runtime/src/vestigia/bell_observatory.py`
- Modify `VESTIGIA_Runtime/src/vestigia/db.py`
- Modify `VESTIGIA_Runtime/src/vestigia/context.py`
- Modify `VESTIGIA_Runtime/src/vestigia/runtime.py`
- Add `VESTIGIA_Runtime/tests/test_bell_observatory.py`
- Extend `VESTIGIA_Runtime/tests/test_vestigia.py` and context receipt tests

### Step 1: Write the failing tests

Add tests that:

- Create a bell run containing bell identity, orientation-context references, requested/effective retrieval policy, semantic source and terms, control-plane exclusion, selected sources, source results, omissions, budgets, response outcome, no-change state, curation suppression, and linked Runtime receipt paths.
- Record a run when a bell context is assembled and finalize it when the response/no-change outcome is known.
- Prove a `make.nothing.happen` response remains a first-class no-change result and is not eligible for curation merely because a bell fired.
- List and inspect runs in stable order, distinguish unavailable/omitted material from empty material, and fail closed on unknown run IDs.
- Replay only stored decision inputs and receipts; never claim to replay model cognition or dispatch outward effects.

Run the focused tests and confirm they fail.

### Step 2: Implement the smallest passing observatory

Add a Runtime-owned SQLite-backed `BellObservatory` service with explicit schema/versioning and bounded JSON fields. Use the existing context receipt and bell outcome data as inputs rather than reimplementing retrieval. Persist one run record keyed by a stable run ID and request/turn IDs, updating it at assembly and completion points. Preserve exact policy resolution, query terms, control-plane exclusion, source provenance, budget accounting, omissions, and `causal_influence: unknown`.

Wire no-change completion into the observatory as `state=no_change`, `curation_eligible=false`, and an explicit suppression reason. Keep policy resolution in `bell_retrieval.py` and keep the observatory explanatory rather than authoritative for retrieval.

### Step 3: Refactor and verify

Run observatory, context, bell, and full Runtime tests. Confirm migrations work for a fresh database and an existing database, and that malformed stored detail is reported as unavailable rather than crashing inspection.

### Step 4: Commit

```bash
git add VESTIGIA_Runtime/src/vestigia VESTIGIA_Runtime/tests
git commit -m "feat(runtime): persist bell observatory runs"
```

## Task 4: Expose Bell Observatory through read-only Runtime capabilities

**Files:**
- Modify `VESTIGIA_Runtime/src/vestigia/capability_contracts.py`
- Modify `VESTIGIA_Runtime/src/vestigia/house_tools.py`
- Modify `VESTIGIA_Runtime/src/vestigia/mcp_projection.py` only if projection filtering needs an additive adjustment
- Extend `VESTIGIA_Runtime/tests/test_mcp_projection.py`
- Extend Runtime capability/HousePort tests
- Add an MCP-to-Runtime integration test under `VESTIGIA_MCP_Server/tests/` if the existing fixture supports both packages

### Step 1: Write the failing tests

Add tests that require these read-only actions to appear in `runtime.capabilities` and be callable through `runtime.call`:

- `bell.runs.list` with bounded filters and limit.
- `bell.run.inspect` by run ID.
- `bell.run.replay` by run ID, returning decision inputs and replay limitations.
- `bell.policy.preview` for a bell/requested policy, returning requested/effective policy, semantic source, terms, and control-plane exclusion.

Assert they have filesystem/database read effects, no outward effect, no confirmation requirement, and no generic write route. Verify an MCP request can call them through HousePort and receives a request ID that joins the Runtime observatory record.

### Step 2: Implement the smallest passing projection

Register the four handlers in HousePort, add their field contracts and descriptions, and route to `BellObservatory`. Preserve existing MCP projection rules so the tools become callable because they are read-only Runtime capabilities; do not add a parallel MCP-side Runtime database reader.

Bound limits and replay payloads, redact raw prompt/response content by default, and return explicit unavailable/omitted fields. Ensure inspection and replay do not mutate Runtime state or send external requests.

### Step 3: Refactor and verify

Run focused Runtime projection tests, the cross-package integration test, and the full MCP/Runtime suites. Check that the catalog exposes the tools consistently and that unsupported filters fail with useful validation errors.

### Step 4: Commit

```bash
git add VESTIGIA_Runtime/src/vestigia VESTIGIA_Runtime/tests VESTIGIA_MCP_Server/tests
git commit -m "feat(runtime): expose bell observatory projection"
```

## Task 5: Documentation, integration fixtures, and release verification

**Files:**
- Add `VESTIGIA_MCP_Server/docs/SENSE_ORGANS_AND_RECEIPTS.md`
- Add `VESTIGIA_Runtime/docs/BELL_OBSERVATORY.md`
- Extend the approved design/spec references only where implementation details need correction
- Add a compact end-to-end fixture covering Porchlight manifest → MCP request receipt → Runtime bell observatory query
- Update package-facing catalogs or README tables if the repository convention requires it

### Step 1: Write the failing integration/documentation checks

Add checks that verify the documented tool names, action names, schema versions, and read-only guarantees match the implementation. Add an end-to-end test that confirms:

- Porchlight is discoverable as a bounded explicit organ.
- A request receipt exposes authorization, observed/included/omitted edges, and no causal claim.
- Bell Observatory reports the same request/turn join key and visible retrieval facts.
- Control-plane boilerplate is excluded from semantic terms.
- No-change remains non-curating.

### Step 2: Implement docs and fixture

Document the contracts, examples, limits, retention/storage boundaries, consent model, and failure modes. Make the fixture deterministic and synthetic; do not use live browser data or canonical Archive content.

### Step 3: Full verification

Run:

```bash
./.venv/bin/pytest -q VESTIGIA_MCP_Server/tests
./.venv/bin/pytest -q VESTIGIA_Runtime/tests
```

Run any repository lint/type/check commands found in package metadata. Review `git diff --check`, inspect the complete diff, and verify no generated databases, screenshots, virtualenv files, secrets, or raw payload fixtures are staged.

### Step 4: Commit and prepare PR

```bash
git add VESTIGIA_MCP_Server VESTIGIA_Runtime docs
git commit -m "feat: add sense organs receipts and bell observatory"
git status --short
```

Before opening the PR, summarize the implementation, tests, security/privacy invariants, and any known follow-up work. Do not claim the PR is green until CI has actually reported success.
