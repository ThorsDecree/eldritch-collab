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
> `748e5d74392ad4f0a98c75b187f82b91606e9e39`. GitHub tag creation and final
> post-merge artifact publication remain release-custody follow-up.

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

* **Status**: `released` — official development canon as of 2026-08-04
* **Description**: Resident-owned context partitioning, explicit ambient-source trust boundaries, stable Discord interaction/reaction delivery, private-image confirmation challenges, durable receipts, and portable-home compatibility.
* **Release evidence**:
  - [x] Run and document the live Windows Discord trust-boundary canary.
  - [x] Change `0.7.0.dev0` to `0.7.0` in a dedicated release PR.
  - [x] Date the changelog entry.
  - [x] Verify the wheel through isolated Windows installation with declared extras.
  - [x] Confirm genuine v0.6.1 → v0.7.0 upgrade, turn, pack, restore, and durable-ledger preservation.
  - [x] Merge the exact validated release head into `main`.
  - [ ] Create the `v0.7.0` GitHub tag and release.
  - [ ] Attach or publish the exact verified post-merge release artifacts and checksums.

The two unchecked items are packaging and release-custody follow-up. They do not change the Runtime version currently carried by development canon.

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

* **Status**: `active` (design and contract review)
* **Description**: Controlled extensibility and bounded resident automation through versioned contracts. This milestone introduces the foundations for a resident-scoped code execution environment without granting an unrestricted host shell.

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

* **Exit criterion**:
  - A resident can run reviewed code inside an explicitly granted workspace, receive typed results and private artifacts, and inspect exactly what authority, resources, and outward effects were used—without gaining arbitrary access to the host.

### 0.9: Explicit Plurality

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
