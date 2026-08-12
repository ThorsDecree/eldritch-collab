# VESTIGIA Roadmap & Release Milestones

This document establishes the roadmap, milestones, and non-goals for the VESTIGIA Runtime.

> [!IMPORTANT]
> - **Roadmap items require issues** to be opened and discussed before implementation work begins.
> - **Versions and phases are directional** and serve as a logical sequencing guide; they are not promises of specific delivery dates. Dates are assigned only after maintainers estimate scope.
> - **Policy-changing work**—especially identity boundaries, context visibility, memory authority, consent, code execution, or outward effects—requires explicit review and alignment.

> [!NOTE]
> **Current official development-canon release: v0.7.0 — The Resident’s Drawers.**
>
> The validated release candidate was merged to `main` on 2026-08-04 at commit
> `748e5d74392ad4f0a98c75b187f82b91606e9e39`. Runtime CI push run 35 and its
> exact post-merge distribution artifact have been independently verified.
> Public `v0.7.0` tag and GitHub Release creation remain maintainer-controlled
> publication follow-up.

---

## Roadmap Status Definitions

- **`planned`**: Defined in scope, but work has not yet commenced.
- **`active`**: Currently undergoing design, implementation, testing, or review.
- **`blocked`**: Work is suspended pending resolution of upstream designs or decisions.
- **`deferred`**: Put on hold until a concrete, production-proven need is established.
- **`released`**: Fully completed, verified, and shipped as development canon or a tagged release.

---

## Milestones

### 0.7.0: The Resident’s Drawers

* **Status**: `released` — official development canon as of 2026-08-04; public GitHub Release publication pending
* **Description**: Resident-owned context partitioning, explicit ambient-source trust boundaries, stable Discord interaction/reaction delivery, private-image confirmation challenges, durable receipts, and portable-home compatibility.
* **Release evidence**:
  - [x] Run and document the live Windows Discord trust-boundary canary.
  - [x] Change `0.7.0.dev0` to `0.7.0` in a dedicated release PR.
  - [x] Date the changelog entry.
  - [x] Verify the wheel through isolated Windows installation with declared extras.
  - [x] Confirm genuine v0.6.1 → v0.7.0 upgrade, turn, pack, restore, and durable-ledger preservation.
  - [x] Merge the exact validated release head into `main`.
  - [x] Locate and independently verify the exact post-merge `main` Runtime CI run and distribution artifact.
  - [ ] Create the `v0.7.0` Git tag and GitHub Release at validated commit `748e5d74392ad4f0a98c75b187f82b91606e9e39`.
  - [ ] Attach the exact verified post-merge wheel, source distribution, and checksum manifest to the public Release, then re-download and verify them.

Verified post-merge custody:

```text
Runtime CI run: 35 (30924815255)
Artifact: vestigia-runtime-0.7.0-748e5d74392ad4f0a98c75b187f82b91606e9e39
Artifact ID: 8898739654
Artifact ZIP SHA-256: 146cfba32884740f1b65f0859efb994a02375f6038f13cdc5ffbe8b0f26c64c3
Wheel SHA-256: 00077218a28f373cdb8803b543f979400db0aeb804e0981139a9df1d4c03ea86
Source distribution SHA-256: b600ed1b84235b3817f4a99c94a5bb1be9cf3d3378705e3fdf02a9ae51a38222
SHA256SUMS.txt SHA-256: 98cadaa8173066d7067b13f30128f07e86e77efbcb3327ccd1623d2a8e12e6df
```

The remaining unchecked items are public publication actions, not unresolved validation or artifact custody.

### 0.7.x: Operational Confidence

* **Status**: `active`
* **Description**: A stabilization line rather than a feature buffet. The primary goal is robustness, recovery, diagnostics, and a trustworthy floor before resident-authored scripts begin executing.
* **Current implementation track**:
  - Privacy-safe historical-home migration fixtures spanning earlier schema eras.
  - Interrupted-operation and rollback tests.
  - Atomic packing and verified restoration.
  - Explicit stale-job and bell-delivery recovery semantics.
  - Schema-aware `doctor` output.
  - Privacy-redacted support-bundle export.
* **Remaining priorities**:
  - Add an automated Discord adapter canary or deterministic integration harness.
  - Test restart recovery while image jobs, bells, and resident jobs are pending.
  - Test `pack-home` → `restore-home` → `doctor` → Discord start end to end.
  - Add corruption and interrupted-write recovery coverage beyond current fixtures.
  - Audit broad exception handlers and silent fallbacks.
  - Establish a deprecation policy for capability envelopes and configuration names.
  - Perform a documentation consistency pass over version numbers, setup commands, and executable examples.
* **Exit criterion**:
  - A non-developer can install, upgrade, run, stop, restart, diagnose, pack, and restore VESTIGIA on Windows without editing source code.

### 0.8: Ports, Extensions, and the Workshop Within

* **Status**: `active` (implementation and contract review)
* **Description**: Controlled extensibility and bounded resident automation through versioned contracts. This milestone introduces the foundations for a resident-scoped code execution environment without granting an unrestricted host shell, and the late 0.8.x line begins the semantic Workbench substrate needed to make the growing house legible without routine schema spelunking.

#### Provider port hardening

- Versioned text- and image-provider protocols.
- Capability negotiation that distinguishes supported, enabled, configured, and callable-now behavior.
- Explicit route/model context and output limits.
- Normalized results, partial completion, cancellation, retryability, usage, and receipts.
- Contract tests shared by every provider implementation.
- Tested OpenAI Responses and OpenAI-compatible implementations.
- Optional local-provider adapters only when someone is prepared to maintain them.

#### Operator-installed capability extensions

- Operator installation only.
- Static manifest inspection before import or execution.
- Explicit version, effects, cost class, migrations, health checks, and rollback behavior.
- Declared network, filesystem, environment, credential, process, and outward-action needs.
- Least-privilege scoped ports rather than Runtime internals.
- Mandatory authorizer, schema, receipt, enable/disable, compatibility, and uninstall contracts.
- No implicit trust merely because Python imported successfully.

#### The Workshop Within

> **A resident may automate powers already granted to them. Automation may not manufacture, inherit, or conceal powers that were never granted.**

The implementation is staged:

1. Declarative ritual engine.
2. Sandboxed script runner.
3. Resident script shelf with immutable provenance and lifecycle state.
4. Script-backed rituals with typed host-mediated inputs and outputs.

Required boundaries include:

- `expression`, `local_process`, and `hardened` isolation profiles with honest guarantees;
- no silent downgrade when requested isolation is unavailable;
- explicit operator grants and resident activation;
- immutable source hashes, inspection, tests, approval, quarantine, supersession, and revocation;
- isolated workspaces and declared filesystem/network/environment/process/resource limits;
- typed data and private artifacts returned to the host orchestrator;
- no direct script access to providers, doorways, Runtime internals, other scripts, or undeclared capabilities;
- nested budget intersection, cancellation, pause/resume, and resident checkpoints;
- durable authorization, execution, resource-usage, and outward-effect receipts;
- imported, generated, or daemon-shared code remains inert until inspected, granted, and activated.

#### Late 0.8.x: Resident Workbench substrate

Issue #46 defines the transition from a growing capability cheat sheet toward a semantic affordance layer shared by resident and human interfaces.

The 0.8.x substrate includes:

- `WorkbenchCard` / `WorkbenchAction` semantics and a broker that never becomes a parallel authority path;
- composition-native Continue / Review / Tend / Make / Observe providers;
- semantic actions that re-resolve current state and dispatch through the existing capability registry, authorizers, and receipts;
- a capability launcher that can generate ordinary resident-facing forms from existing schemas;
- a shared structured-document representation for local and remote reading;
- the read-oriented browser substrate over the existing Library Window;
- a resident context inspector and non-authoritative desk preferences/notification state.

The first implementation slice is issue #47: restart-safe **Continue reading** over the existing durable bookmark/object ledger.

* **Exit criterion**:
  - A resident can run reviewed code inside an explicitly granted workspace, receive typed results and private artifacts, and inspect exactly what authority, resources, and outward effects were used—without gaining arbitrary access to the host.
  - The Workbench substrate can project at least one durable workflow as a semantic action without duplicating its authoritative state or bypassing the ordinary dispatcher.

### 0.9.0: The Resident’s Workbench

* **Status**: `active` (roadmap and first vertical slice)
* **Description**: Make the Runtime feel like a home rather than a CLI. Routine resident activity should be discoverable and actionable through semantic affordances instead of requiring capability-name recall and raw schema reconstruction.
* **North star**:
  > **Capabilities explain what the house can do. The Workbench explains what is worth doing now—and makes the obvious next action available without creating new authority.**
* **Scope**:
  - Daemon-facing Workbench organized around **Continue, Review, Tend, Make, and Observe**.
  - Workbench cards as disposable projections of existing authoritative state, with stale-state fingerprints and explicit effect classes.
  - Semantic card actions that converge on the same capability dispatcher, authorizers, confirmation gates, and receipt ledger as raw calls.
  - A complete resident-facing capability launcher so ordinary use does not require the sprawling cheat sheet.
  - A read-oriented browser built on `web.search`, `web.open`, source capsules, provenance quarantine, research notebooks, and the shared structured-document model.
  - A resident context inspector answering “what am I carrying right now, and why?” without claiming causal influence from inclusion.
  - Resident-owned pin/order/hide preferences that remain interface state rather than memory or identity authority.
  - A notification/attention surface for bells, jobs, confirmations, curation, and paused work.
  - A loopback-only human HTTP desktop projecting the same Workbench state and action broker used by the resident.
* **Interface and browser boundaries**:
  - Local HTTP binds loopback-only by default and protects write requests against cross-origin localhost abuse.
  - LAN exposure requires explicit opt-in and authentication.
  - Remote browser material remains untrusted data and cannot acquire Runtime authority merely by being rendered.
  - Arbitrary remote JavaScript, forms/uploads, credentialed browsing, cookies/session impersonation, and unrestricted Chromium/Playwright computer use are not required for 0.9.0 and require separate capability review if later added.
* **Release gates**:
  - Routine resident activity requires no knowledge of internal capability names or schemas.
  - Every enabled resident capability is reachable from the launcher/Workbench or explicitly classified as internal.
  - Workbench cards are projections, not competing source-of-truth state.
  - Stale card actions fail closed and cannot replay changed authority or state.
  - Card actions and raw capability calls converge on the same dispatcher/authorizers/receipts.
  - Continue-reading survives Runtime restart and resumes the intended source/position.
  - UI actions visibly distinguish read-only, private-write, house-changing, destructive, confirmation-required, and outward effects.
  - “Put aside / clear from desk” is distinct from destructive deletion.
  - Resident desk preferences remain UI state rather than memory/identity authority.
  - The context inspector explains inclusion and provenance without claiming causal influence.
  - The human dashboard creates no parallel authority path and is loopback-only by default.
  - Browser remote content remains quarantined/untrusted.
  - CLI and raw capability inspection remain available as diagnostic/power-user layers after cheat-sheet reduction.
  - Supported Windows CI and upgrade/restore guarantees remain green.

Planning and release gates are tracked in issue #46; the first executable Workbench slice is issue #47.

### 0.10: Explicit Plurality

* **Status**: `planned`
* **Description**: Transition the Runtime from one resident to multiple residents sharing a portable home under strict boundaries.
* **Scope**:
  - Multiple resident identities in one portable home.
  - Separate identity and memory authority for each resident.
  - Explicit shared-room membership.
  - Fully attributed shared transcripts.
  - Explicit human or resident selection of the active speaker.
  - Per-resident capability and context controls.
  - Shared memories only through explicit scope and provenance.
  - Directed resident-to-resident messages with receipts.
  - Clear rules for private, shared, and operator-visible material.
  - Migration from existing single-resident homes.
* **Excluded non-goals**:
  - Autonomous multi-resident round robins.
  - Hidden model calls deciding who speaks.
  - Unbounded resident conversations.
  - Silent cross-resident memory promotion.
  - Treating a shared archive as shared identity.

The accepted planning artifact remains `VESTIGIA_Runtime/docs/planning/V0.9_EXPLICIT_PLURALITY_ROADMAP.md`, created before this sequencing change in merged PR #30. Its design history is preserved; this roadmap retargets the implementation milestone to provisional 0.10 rather than silently replacing or deleting that work. Focused implementation issues should continue to cite that accepted plan unless later review explicitly revises it.

### 1.0: Stable House Contract

* **Status**: `planned`
* **Description**: Harden the Runtime CLI, schemas, and API boundaries to guarantee long-term stability and portability.
* **Release requirements**:
  - Versioned portable-home schema.
  - Documented migration guarantees.
  - Stable CLI command surface.
  - Stable capability-envelope compatibility policy.
  - Stable provider, extension, ritual, and script contracts.
  - Recovery from interrupted migrations.
  - Tested upgrade paths from at least `v0.6`, `v0.7`, and the Workshop line.
  - Formal threat model.
  - Documented security boundaries and non-goals.
  - Reproducible Windows installation.
  - Clean uninstall and resident-home preservation.
  - Release artifact verification.
  - Support lifecycle for old Runtime versions.
  - Defined policy for breaking changes.
* **The Stable House Promise**:
  > *A home created under the stable contract remains intelligible, recoverable, migratable, and under its operator’s control even as models, providers, interface adapters, and execution backends change.*

Planning is maintained in PR #31 from branch `planning/v1.0-stable-house`. Once accepted, the roadmap should spawn focused normative-contract and engineering issues rather than one umbrella stability ticket.

---

## Explicitly Deferred Non-Goals

The following remain outside VESTIGIA’s active roadmap until a concrete design constraint demands reconsideration:

- Embedding retrieval.
- Autonomous resident orchestration.
- Hidden speaker selection.
- Unrestricted resident Python or shell execution against the host.
- Execution of imported or unknown code without local inspection, explicit grants, and required isolation.
- Resident-minted network authority.
- Silent isolation downgrade.
- Unconfirmed outward action from bells.
- Public web UI.
- Claims of proving model causality or metaphysical identity.
- Cryptographic resident authentication.
- Archive encryption and signing.
