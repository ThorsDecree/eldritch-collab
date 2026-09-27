# House Mechanic Task Source Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let House Mechanic read, preview, and atomically apply bounded multi-file UTF-8 source changes inside an issued task worktree, including new files, then expose those operations through the existing MCP `dev.*` surface.

**Architecture:** Add a focused House Mechanic `source_ops.py` for path confinement, durable immutable patch proposals, and atomic application. Reuse the existing task lease/holder/generation/iteration authority and Git worktree model. Extend MCP `dev.call` so it can dispatch fixed safe read operations as well as allowlisted mutations; keep `VESTIGIA_MCP_DEV_ACTIONS` mutation-only.

**Tech Stack:** Python 3.11+, pathlib/hashlib/json/difflib/os/tempfile, pytest, existing House Mechanic + MCP packages.

**Spec:** `docs/superpowers/specs/2026-09-27-house-mechanic-task-source-ops-design.md`

## Global Constraints

- House Mechanic is the disk-write authority; Git supplies isolation/provenance/rollback.
- Every path resolves beneath the worktree issued by `task.acquire`; callers never choose host paths/worktree roots.
- `task.read` works during an active leased task without opening an iteration.
- `task.diff` and `task.patch` require the exact current `iteration_id`.
- `task.patch` applies only a prior immutable `task.diff` proposal identified by `proposal_id + proposal_digest`.
- Multi-file patches are all-or-nothing.
- Support modify-existing and create-new UTF-8 files; no delete, rename, binary, symlink, or special-file mutation.
- Ceilings: 32 files/proposal, 1 MiB resulting file, 4 MiB total patch payload.
- Ordinary API request ceiling remains 16 KiB; only `task.diff` gets a larger bounded request ceiling.
- Keep the four stable MCP descriptors; no arbitrary method/path/shell/argv/cwd/environment.
- Shared MCP request IDs remain visible in independent MCP and House Mechanic receipts.
- No supervisor self-update/deployment authority.

## Review Focus

- Windows path normalization plus symlink/junction escape must stay inside the issued worktree.
- Mid-apply failure must restore every already-touched path to exact pre-state.
- Larger `task.diff` bodies must not widen request limits on unrelated endpoints.
- `task.read` must be callable through `dev.call` without weakening mutation allowlists.
- Generation/iteration/pre-state drift and consumed proposals must all fail closed.

---

### Task 1: Task authority helper + bounded source reads

**Files:**
- Create: `VESTIGIA_House_Mechanic/src/house_mechanic/source_ops.py`
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/tasking.py`
- Create: `VESTIGIA_House_Mechanic/tests/test_source_ops.py`

**Interfaces:**
- Produce `TaskSupervisor.authorized_worktree(*, task_id: str, holder_id: str, authority_generation: int, iteration_id: str | None = None, require_open_iteration: bool = False) -> tuple[TaskRecord, Path]`.
- Produce `SourceOpError(code: str, message: str)`.
- Produce `TaskSourceWorkspace.read(*, task_id: str, holder_id: str, authority_generation: int, paths: list[str]) -> dict[str, Any]`.

- [ ] Write failing tests for successful multi-file UTF-8 reads, wrong holder/stale generation/expired lease, parent/absolute escape, symlink escape, non-UTF-8, and >1 MiB files.
- [ ] Run `python -m pytest tests/test_source_ops.py -q`; verify RED because source operations do not exist.
- [ ] Implement `authorized_worktree` by reusing existing `_authorize`, branch verification, and exact-current-iteration checks when requested.
- [ ] Implement one shared relative-path resolver and `TaskSourceWorkspace.read`; return normalized relative path, bytes, SHA-256, text, and truncation metadata, never absolute host paths.
- [ ] Run `python -m pytest tests/test_source_ops.py tests/test_tasking.py -q`; verify GREEN.
- [ ] Commit: `feat: add task-scoped source reads`.

### Task 2: Durable immutable diff proposals

**Files:**
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/source_ops.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_source_ops.py`

**Interfaces:**
- Produce `PatchProposalStore(directory: Path)` with strict `create/get/save`.
- Produce proposal schema `vestigia.house-mechanic-patch-proposal.v0.1` and IDs `hm_patch_<32 hex>`.
- Produce `TaskSourceWorkspace.diff(*, task_id: str, holder_id: str, authority_generation: int, iteration_id: str, mutations: list[dict[str, Any]]) -> dict[str, Any]`.

- [ ] Write failing tests: no source mutation during diff; modify+create proposal; duplicate-path refusal; stale hash refusal; create-target-exists refusal; missing/wrong iteration refusal; 32-file/1 MiB/4 MiB ceilings.
- [ ] Run source-op tests; verify RED.
- [ ] Persist proposals atomically under `task_state/patch_proposals` using temp file + fsync + `os.replace`, matching `TaskLedger` durability style.
- [ ] Accept only:
  - modify: `op,path,expected_sha256,old,new`
  - create: `op,path,expected_state="absent",content`
- [ ] Canonicalize ordered proposal JSON and compute `proposal_digest`; store task/holder/generation/iteration, exact pre-state, rendered unified diff, timestamp, and `state="ready"`.
- [ ] Run source-op tests; verify GREEN.
- [ ] Commit: `feat: add immutable task patch proposals`.

### Task 3: Atomic proposal application

**Files:**
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/source_ops.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_source_ops.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_tasking.py`

**Interface:**
- Produce `TaskSourceWorkspace.patch(*, task_id: str, holder_id: str, authority_generation: int, iteration_id: str, proposal_id: str, proposal_digest: str) -> dict[str, Any]`.

- [ ] Write failing tests for exact modify+create application, digest mismatch, replay, changed pre-state, authority/iteration drift, and injected failure on the second file with zero net filesystem change.
- [ ] Add a checkpoint test proving a newly created file becomes tracked via existing `git add -A`.
- [ ] Run tests; verify RED.
- [ ] Before writing, revalidate the complete proposal: task authority, current iteration, digest/state, every hash/absent assertion, path confinement, UTF-8/file/patch limits.
- [ ] Implement transactional application: stage results, retain exact backups, apply deterministically, and on any failure restore touched existing files byte-for-byte and remove files created by this proposal.
- [ ] Mark proposal `consumed` only after every source write succeeds; return ordered pre/post hashes.
- [ ] Run `python -m pytest tests/test_source_ops.py tests/test_tasking.py -q`; verify GREEN.
- [ ] Commit: `feat: apply atomic task patch proposals`.

### Task 4: House Mechanic HTTP routes, capability metadata, and receipts

**Files:**
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/api.py`
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/receipts.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_task_api.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_receipts.py`

**Interfaces:**
- `task.read` -> `POST /v1/task-read`, `mutation=false`, effect `bounded_worktree_read`.
- `task.diff` -> `POST /v1/task-diff`, `mutation=true`, effect `bounded_worktree_patch_proposal`.
- `task.patch` -> `POST /v1/task-patch`, `mutation=true`, effect `bounded_worktree_source_mutation`.
- Add `ReceiptStore.append_source_operation(...)`, receipt kind `dev_source_operation`.

- [ ] Write failing API tests for all three closed schemas/routes, typed errors, receipt evidence, and source-text omission from read receipts.
- [ ] Add failing request-ceiling tests proving ordinary routes still cap at `16_384` bytes while only `task.diff` admits a bounded body sufficient for the 4 MiB patch payload plus JSON overhead.
- [ ] Run `python -m pytest tests/test_task_api.py tests/test_receipts.py -q`; verify RED.
- [ ] Change the JSON-body helper to accept a per-call maximum while retaining `MAX_REQUEST_BYTES=16_384` as default.
- [ ] Construct `PatchProposalStore(task_dir / "patch_proposals")` and `TaskSourceWorkspace` whenever tasking is configured; require no new CLI path argument.
- [ ] Add handlers, typed `SourceOpError` mapping, fixed routes, capability metadata, and bounded receipts containing IDs/digests/path lists/pre/post hashes rather than copied source text.
- [ ] Run targeted tests, then the full House Mechanic suite `python -m pytest -q`; verify GREEN.
- [ ] Commit: `feat: expose task source operations`.

### Task 5: MCP safe operation dispatch

**Files:**
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/house_mechanic.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_house_mechanic.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_dev_projection.py`

**Interfaces:**
- Add `HouseMechanicClient.projected_calls(response: dict[str, Any]) -> dict[str, dict[str, Any]]`.
- Keep `projected_mutations` backward-compatible and mutation-only.
- Make `HouseMechanicClient.call(...)` admit a fixed safe read operation or an allowlisted fixed mutation.

- [ ] Write failing tests showing safe `task.read` appears in `projected_calls` and remains callable under wildcard/exact/deny-all mutation filters.
- [ ] Assert `task.diff`/`task.patch` remain filtered by `VESTIGIA_MCP_DEV_ACTIONS`.
- [ ] Assert unsafe method, query-string path, open schema, and malformed metadata are still rejected.
- [ ] Run `python -m pytest tests/test_house_mechanic.py tests/test_dev_projection.py -q`; verify RED.
- [ ] Generalize the internal projectability validator to fixed safe operations; apply mutation filtering only when metadata says `mutation=true`.
- [ ] Add `projected_calls` to `dev.capabilities`; preserve `projected_mutations`.
- [ ] Run targeted tests; verify GREEN.
- [ ] Commit: `feat: project fixed House Mechanic read operations`.

### Task 6: End-to-end proof, versioning, and docs

**Files:**
- Modify: `VESTIGIA_MCP_Server/tests/test_dev_house_mechanic_integration.py`
- Modify: `VESTIGIA_House_Mechanic/pyproject.toml`
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/api.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/house_mechanic.py`
- Modify: `VESTIGIA_House_Mechanic/README.md`
- Modify: `VESTIGIA_MCP_Server/README.md`

- [ ] Extend the real integration fixture into a Git repo with configured repositories/worktree/task state.
- [ ] Write a failing MCP-only integration test for `task.acquire -> task.read -> iteration.begin -> task.diff -> task.patch -> iteration.checkpoint`, including one modified and one newly created file.
- [ ] Assert the worktree is unchanged after `task.diff`, changed only after `task.patch`, the new file is tracked by the checkpoint commit, and House Mechanic/MCP receipts share request IDs.
- [ ] Prove an exact mutation allowlist can deny `task.patch` while `task.read` remains callable.
- [ ] Run `python -m pytest tests/test_dev_house_mechanic_integration.py -q`; verify RED until protocol/version wiring is updated.
- [ ] Bump House Mechanic package to `0.12.0.dev0` and House Mechanic HTTP protocol to `vestigia.house-mechanic-api.v0.10` in both packages; update protocol assertions.
- [ ] Document the source-op lifecycle, create-file support, atomic proposal semantics, read-vs-mutation filtering, and non-goals.
- [ ] Run the integration test, then both complete package suites; verify all GREEN.
- [ ] Commit: `feat: complete task-scoped source edit loop`.

### Task 7: Live Phase 6 proving task — Runtime retrieval

**Files:** no committed machine-local config is required; the Runtime fix happens on the House Mechanic-created task branch.

- [ ] Point House Mechanic's tasking repo root at Jeff's real `eldritch-collab-dev` clone while leaving Archive write-prefix grants unchanged.
- [ ] Restart House Mechanic and verify protocol v0.10 plus `task.read`, `task.diff`, and `task.patch` in live `dev.capabilities`.
- [ ] Acquire a Runtime retrieval task and read:
  - `VESTIGIA_Runtime/src/vestigia/mcp_context_source.py`
  - `VESTIGIA_Runtime/src/vestigia/config.py`
  - `VESTIGIA_Runtime/tests/test_mcp_context_source.py`
- [ ] Begin an iteration; proposal/apply a regression test where an important late prompt term survives bounded selection and early filler does not monopolize terms.
- [ ] Run a configured targeted Runtime test recipe and observe RED. If no named test recipe exists, stop at that configuration boundary rather than adding arbitrary shell authority.
- [ ] Proposal/apply the minimal deterministic fix: consider the full prompt, filter low-information terms more aggressively, choose a bounded high-signal set, and change default Archive-MCP `max_terms` from 5 to 8 without increasing item/token budgets.
- [ ] Run targeted + full Runtime tests, checkpoint, inspect joined receipts, then push/open the Runtime PR. Do not auto-merge without separate authorization.
