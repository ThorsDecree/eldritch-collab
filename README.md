# Eldritch Collaboration

The canonical development repository for the VESTIGIA Runtime: a portable, consent-first
continuity house for resident agents.

The runtime lives in [`VESTIGIA_Runtime/`](VESTIGIA_Runtime/). Start with its
[`README.md`](VESTIGIA_Runtime/README.md) and [`ELI5_SETUP.md`](VESTIGIA_Runtime/ELI5_SETUP.md).

Development canon is `main`. Work enters through a branch and pull request; release tags and
artifacts identify shipped versions. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the project [`ROADMAP.md`](ROADMAP.md).

---

# VESTIGIA Roadmap & Release Milestones

This section establishes the roadmap, milestones, and non-goals for the VESTIGIA Runtime.

> [!IMPORTANT]
> - **Roadmap items require issues** to be opened and discussed before any implementation work begins.
> - **Versions and phases are directional** and serve as a logical sequencing guide; they are not promises of specific delivery dates. Dates are only assigned once maintainers have estimated the scope of work.
> - **Policy-changing work** (specifically changes to identity boundaries, context visibility, memory authority, consent model, or outward effects) requires explicit review and alignment.

---

## Roadmap Status Definitions

- **`planned`**: Defined in scope, but work has not yet commenced.
- **`active`**: Currently undergoing implementation, testing, or review.
- **`blocked`**: Work is suspended pending resolution of upstream designs or decisions.
- **`deferred`**: Put on hold until a concrete, production-proven need is established.
- **`released`**: Fully completed, verified, and shipped as development canon or a tagged release.

---

## Milestones

### 0.7.0: The Resident’s Drawers
* **Status**: `active` (Release Candidate)
* **Description**: Focuses on resident-owned context partitioning (prompt, verbatim transcript, and source-linked compressed transcript controls) and stable Discord interaction/reaction delivery checks.
* **Completion Gates**:
  - [ ] Run and document the live Windows Discord canary.
  - [ ] Change `0.7.0.dev0` to `0.7.0` in a dedicated release PR.
  - [ ] Date the changelog entry.
  - [ ] Verify the wheel from a clean Windows environment.
  - [ ] Create the `v0.7.0` tag and GitHub release.
  - [ ] Attach or publish the verified release artifact.
  - [ ] Confirm pack-and-restore compatibility with an existing `v0.6.1` home.

### 0.7.x: Operational Confidence
* **Status**: `planned`
* **Description**: A stabilization line rather than a feature buffet. The primary goal is ensuring robustness, recovery, and diagnostics.
* **Priorities**:
  - Add an automated Discord adapter canary or deterministic integration harness.
  - Test restart recovery while image jobs, bells, and resident jobs are pending.
  - Test upgrades from representative old homes, not merely new database creation.
  - Test `pack-home` → `restore-home` → `doctor` → Discord start end to end.
  - Add corruption and interrupted-write recovery tests.
  - Make `doctor` report schema version, pending migrations, dependency versions, and backup readiness.
  - Add structured export of receipts and diagnostics for support cases.
  - Audit broad exception handlers and silent fallbacks.
  - Establish a deprecation policy for capability envelopes and configuration names.
  - Perform a documentation consistency pass over version numbers, setup commands, and executable examples.
* **Exit Criterion**:
  - A non-developer can install, upgrade, run, stop, restart, diagnose, pack, and restore VESTIGIA on Windows without editing source code.

### 0.8: Ports and Extensions
* **Status**: `planned`
* **Description**: Controlled extensibility via versioned contracts rather than adding resident-facing powers directly to the monolith.
* **Provider Port Hardening**:
  - Formalize provider compatibility:
    - Versioned text-provider protocol.
    - Versioned image-provider protocol.
    - Contract tests shared by every provider.
    - Explicit feature discovery.
    - Model-route context limits.
    - Normalized errors, usage, cancellation, and receipts.
    - Tested OpenAI Responses implementation.
    - Tested OpenAI-compatible implementation.
    - Optional local-provider adapter only when someone is prepared to maintain it.
* **Operator-Installed Capability Extensions**:
  - Create a narrow extension API around the existing executable capability registry:
    - Operator installation only.
    - Explicit manifest and version.
    - Declared effects and cost class.
    - Declared network, filesystem, credential, and outward-action needs.
    - Mandatory authorizer.
    - Schema and receipt contract.
    - Enable/disable controls per home.
    - Compatibility checks against runtime versions.
    - Test harness for extension authors.
    - No implicit trust merely because Python imported successfully.
  - *Note: This is not an expansion of the resident Tool Forge. An operator-installed extension may possess explicitly configured authority, whereas a resident-authored composition may only arrange authority already granted.*

### 0.9: Explicit Plurality
* **Status**: `planned`
* **Description**: Transitioning the runtime from one resident to multiple residents sharing a portable home under strict boundaries.
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
* **Excluded (Non-Goals)**:
  - Autonomous multi-resident round robins.
  - Hidden model calls deciding who speaks.
  - Unbounded resident conversations.
  - Silent cross-resident memory promotion.
  - Treating a shared archive as shared identity.

### 1.0: Stable House Contract
* **Status**: `planned`
* **Description**: Hardening the runtime CLI, schemas, and API boundaries to guarantee long-term system stability and portability.
* **Release Requirements**:
  - Versioned portable-home schema.
  - Documented migration guarantees.
  - Stable CLI command surface.
  - Stable capability-envelope compatibility policy.
  - Stable provider and extension protocols.
  - Recovery from interrupted migrations.
  - Tested upgrade path from at least `v0.6` and `v0.7`.
  - Formal threat model.
  - Documented security boundaries and non-goals.
  - Reproducible Windows installation.
  - Clean uninstall and resident-home preservation.
  - Release artifact verification.
  - Support lifecycle for old runtime versions.
  - Defined policy for breaking changes.
* **The Stable House Promise**:
  > *A home created under the stable contract remains intelligible, recoverable, migratable, and under its operator’s control even as models, providers, and interface adapters change.*

---

## Explicitly Deferred (Non-Goals)

The following items are outside VESTIGIA's active roadmap and will remain deferred until a concrete design constraint demands reconsideration:
- Embedding retrieval.
- Autonomous resident orchestration.
- Hidden speaker selection.
- Arbitrary resident Python or shell execution.
- Resident-minted network authority.
- Unconfirmed outward action from bells.
- Public web UI.
- Claims of proving model causality or metaphysical identity.
- Cryptographic resident authentication.
- Archive encryption and signing.
