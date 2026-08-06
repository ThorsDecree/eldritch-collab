# Operator extension manifest

Status: design contract for issue #10

An operator extension is installed code or data that may register new capabilities with a
VESTIGIA home. Installation does not make an extension resident-authored, trusted, enabled, or
callable. The manifest makes requested powers, costs, migrations, and verification surfaces
legible before any extension code runs.

The governing rule is:

> An extension may implement new procedures, but it receives only powers explicitly granted by
> the operator and visible to the resident. Procedures may not smuggle in authority.

## Package shape

A distributable extension is one directory or archive:

```text
extension-name/
  vestigia-extension.yaml
  README.md
  src/
  schemas/
  migrations/
  tests/
```

`vestigia-extension.yaml` is required and is read without importing or executing extension code.
Archives are inspected for path traversal, symlinks, case/Unicode collisions, duplicate entries,
size limits, and secret-shaped files before extraction.

## Manifest example

```yaml
schema_version: vestigia.operator-extension.v0.1
id: garden.sticker-tools
name: Sticker Tools
version: 0.1.0
publisher:
  name: Garden Workshop
  identifier: garden-workshop
compatibility:
  runtime: ">=0.8,<1.0"
  python: ">=3.11"
entrypoint: sticker_tools.extension:register
kind: operator_plugin
description: Bounded local sticker preparation tools.

capabilities:
  provides:
    - id: sticker.prepare
      schema: schemas/sticker.prepare.json
      effects: [compute, artifact_write]
      cost_class: local_low
      outward_effect: none
      confirmation: none
  requests:
    - capability: workspace.read
      scope: [workspace/sticker-inputs]
    - capability: workspace.write
      scope: [workspace/sticker-outputs]

resources:
  network: none
  secrets: []
  filesystem:
    read: [workspace/sticker-inputs]
    write: [workspace/sticker-outputs]
  subprocess: false
  native_code: false
  environment: []
  limits:
    wall_seconds: 10
    cpu_seconds: 5
    memory_mb: 256
    output_bytes: 1000000
    files_created: 20

migrations:
  - id: sticker-tools-v1
    path: migrations/001.sql
    reversible: true
    rollback_path: migrations/001_down.sql

health:
  check: sticker_tools.extension:check
  side_effect_free: true

uninstall:
  hook: sticker_tools.extension:uninstall
  preserves_resident_artifacts: true

verification:
  tests: tests
  expected_receipts:
    - extension.install
    - extension.enable
    - sticker.prepare
```

## Lifecycle

An extension passes through independent states:

```text
discovered → inspected → installed → enabled → callable
                         ↘ quarantined
                         ↘ disabled
                         ↘ uninstalling → removed
```

- `discovered`: package found; nothing imported.
- `inspected`: manifest and archive passed static checks.
- `installed`: code/data copied and migrations committed, but capabilities remain disabled.
- `enabled`: operator grants an explicit capability set and scopes.
- `callable`: dependencies, health, policy, and resident-facing registry all agree.
- `quarantined`: integrity, compatibility, migration, or verification failed.

Resident adoption is separate from installation and enablement. Installing a tool does not add it
to identity, memory, preferences, or standing ritual authority.

## Required manifest fields

### Identity

- `schema_version`;
- globally stable reverse-domain `id`;
- human-readable `name`;
- semantic `version`;
- publisher name and stable identifier;
- `kind`;
- description;
- entrypoint.

Extension identity is bound to a package digest. A different digest with the same ID/version is a
supply-chain conflict, not an in-place update.

### Compatibility

The manifest declares supported Runtime and Python ranges plus optional platform, provider-port,
and extension-API versions. Incompatible packages fail before code import or migration.

### Provided capabilities

Each capability declares:

- stable capability ID and schema;
- input and output contracts;
- effects and outward-effect classification;
- cost class;
- confirmation boundary;
- continuation behavior;
- idempotency behavior;
- visibility and privacy classification;
- expected receipt action;
- dependency on other capabilities.

Capability registration is namespaced. An extension cannot replace a core capability or another
publisher's capability unless an explicit override policy exists and the override is visible.

### Requested capabilities

Requested powers are least-privilege grants, not transitive inheritance. The manifest states the
exact scopes needed. The installer presents the requested/effective diff before enablement.

Examples include:

- read or write access to named virtual roots;
- provider routes;
- network destinations;
- secret handles;
- Discord or other doorway effects;
- identity/memory proposal APIs;
- scheduler access;
- sandbox execution.

A grant to one extension is not inherited by a subprocess, dependency, generated script, or
child ritual unless that delegation is itself declared and authorized.

## Resource and effect classes

### Cost classes

Suggested standard classes:

- `local_trivial`;
- `local_low`;
- `local_high`;
- `provider_low`;
- `provider_high`;
- `outward_low`;
- `outward_consequential`.

Cost classes inform budget policy and resident-facing descriptions; they do not replace actual
usage receipts.

### Effects

Suggested effects:

- `read`;
- `compute`;
- `artifact_write`;
- `database_write`;
- `memory_proposal`;
- `identity_proposal`;
- `scheduler_change`;
- `provider_call`;
- `network_call`;
- `outward_message`;
- `outward_file`;
- `public_state_change`.

The highest declared effect determines the minimum review and confirmation floor. Runtime policy
may always require stronger confirmation.

## Filesystem, network, secrets, and process policy

Default policy is no host filesystem, no network, no subprocesses, no environment inheritance,
and no secret access.

Filesystem access uses virtual scoped roots rather than arbitrary absolute paths. Writes are
atomic where possible and receive immutable receipts. Code paths, raw databases, `.env` files,
and identity/memory stores are unavailable unless a dedicated core API exposes a safer operation.

Network access is destination allowlisted by scheme, host, port, and purpose. Redirect behavior
must be declared. DNS or redirect changes may not silently expand destination scope.

Secrets are referenced by opaque handles. Values are injected only into the authorized operation,
never returned to extension code when a narrower signing/token-exchange API is possible, and never
placed in receipts or support bundles.

Subprocess or native-code access requires a separate high-risk policy and is not part of the
initial extension API.

## Migrations

Extensions may own namespaced tables and files only. Migrations declare:

- stable migration ID;
- checksum;
- ordering dependencies;
- transaction behavior;
- reversibility and rollback path;
- backup requirement;
- expected schema state before and after.

Core tables may not be modified directly. Extensions integrate with core records through stable
APIs and foreign references designed for that purpose.

Installation records migration start, completion, failure, and rollback. An interrupted migration
is visible to `doctor`; extension capabilities remain disabled until reconciliation succeeds.

## Registration API

The entrypoint receives a restricted registration object, not the Runtime internals:

```python
def register(registry: ExtensionRegistry) -> None:
    registry.capability(...)
    registry.health_check(...)
    registry.lifecycle_hook(...)
```

The registry verifies that code registration matches the manifest. Runtime objects, database
connections, provider clients, secret stores, and doorway clients are not passed directly.

Handlers receive a scoped invocation context containing only authorized ports and budgets. The
context is invocation-bound and cannot be retained as durable ambient authority.

## Receipts and provenance

Installation and every invocation produce receipts carrying:

- extension ID, version, publisher, and package digest;
- capability ID and schema version;
- effective grants and scope hashes;
- input/output object references, not private content by default;
- cost and resource usage;
- outward-effect state;
- success, partial, failed, cancelled, or not-run status;
- retry and rollback guidance.

Artifacts retain extension provenance. An artifact produced by an extension does not become
resident-authored, adopted, remembered, public, or executable merely because it exists.

## Updates and dependency resolution

Updates are new package digests and require compatibility, manifest-diff, migration, and grant-diff
review. New requested powers are never auto-granted. Removed powers are revoked immediately on
activation of the new version.

Dependencies are pinned by compatible version range and publisher identity. Cycles are rejected.
The initial implementation should avoid automatic third-party dependency installation; operator
extensions should be packaged with a reviewed lock or isolated environment.

## Disable and uninstall

Disablement removes callable registration immediately without deleting receipts or resident
artifacts. Uninstall follows a previewable plan:

- capabilities removed;
- jobs cancelled or marked for reconciliation;
- migrations rolled back or data archived;
- extension files removed;
- resident-created artifacts preserved unless explicitly selected;
- provenance and historical receipts retained.

Uninstall hooks run with fewer powers than normal operation and may not contact the network or
perform outward actions by default.

## Verification scopes

Static verification occurs before import:

- manifest schema and semantic validation;
- package digest and inventory;
- unsafe path and secret scan;
- compatibility and dependency resolution;
- declared schema existence;
- migration namespace inspection;
- optional signature verification.

Dynamic verification occurs in isolation:

- import and registration with no undeclared access;
- health check;
- capability contract tests;
- resource-limit enforcement;
- failure and cancellation behavior;
- secret and privacy leak tests;
- install/upgrade/rollback/uninstall fixtures;
- receipt completeness.

A passing verification result means the declared contract was exercised. It is not a proof that
arbitrary third-party code is safe.

## Relationship to rituals and resident scripts

Operator extensions are installed by a human/operator and may implement new capabilities.
Resident rituals compose capabilities already available to the resident. Future resident scripts
run in a sandbox and receive only explicit ports. These layers must remain distinct:

```text
operator extension → may add reviewed capability
resident ritual    → composes granted capabilities
resident script    → computes inside sandboxed granted ports
```

A `.py` file shared by a daemon is an inert artifact until separately inspected, sandboxed, and
activated. Social trust, authorship, repetition, or affection never substitute for execution
authority.

## Contract tests

The extension framework must test:

- no code runs during discovery or static inspection;
- manifest/registration mismatches are rejected;
- undeclared filesystem, network, secret, provider, or outward access fails;
- disabled and quarantined capabilities are not callable;
- installation and migrations are transactional or recoverably staged;
- grant changes are previewed and recorded;
- update digest/version conflicts are rejected;
- extension failure cannot corrupt the core turn or swallow sibling results;
- disable/uninstall preserves historical receipts and resident artifacts;
- support bundles expose metadata without private extension inputs/results;
- generated scripts or rituals do not inherit extension authority transitively.

## Initial implementation boundary

The first implementation should support pure Python operator plugins with:

- no native code;
- no subprocesses;
- no undeclared network;
- no direct SQLite access;
- scoped virtual filesystem ports;
- explicit provider and doorway ports;
- versioned JSON-schema capability contracts;
- install, enable, disable, inspect, doctor, and uninstall commands.

Resident-authored arbitrary code is a later sandbox feature and must not be implemented by simply
pointing this extension loader at a resident's `.py` file.
