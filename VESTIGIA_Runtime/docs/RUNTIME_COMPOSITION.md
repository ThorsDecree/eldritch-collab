# Runtime composition migration

Status: active for `0.8.0.dev0`

Issue: #25

## Why this exists

Several post-v0.7 features currently integrate by replacing private methods or mutating private registries during `import vestigia`. The wrappers are individually guarded, but the combined runtime depends on import order and private implementation details. Reloads, renamed methods, partial imports, and duplicate registration can therefore create silent or doubled behavior.

The v0.8 line freezes expansion of that pattern and replaces it with explicit composition.

## Transitional containment

`vestigia.bootstrap` is the only approved entry point for the remaining legacy `install_core()` adapters. The installation order is immutable, auditable, idempotent, and covered by tests.

No new production feature may:

- assign to a private method on `HousePort`, `CoreRuntime`, `MemoryService`, or another feature module;
- mutate `CapabilityRegistry._specs` directly;
- mutate capability-contract dictionaries after import;
- wrap `sensory_apparatus._observatory`;
- swallow a missing required integration with `except (ImportError, AttributeError): pass`.

The transitional bootstrap is containment, not the target architecture.

## Target registries

### BuiltinCapabilityRegistry

Registers a unique stable component name and a callable that installs capabilities into one `HousePort`. Duplicate names or capability collisions fail before the house becomes callable.

### ObservatoryPanelRegistry

Registers read-only panel providers by stable section name. Panel failures are isolated and reported as diagnostic evidence rather than silently dropping the panel.

### RuntimeHookRegistry

Owns explicit hook points with documented signatures and ordering:

- before chat;
- after chat;
- memory-extraction veto;
- curation veto;
- receipt filtering;
- startup and shutdown diagnostics.

Hooks must not widen authority. Exceptions follow declared fail-open or fail-closed semantics per hook type.

### DrawerModeRegistry

Registers `image.drawer` modes without replacing `HousePort._image_drawer`. Unknown modes reach the canonical handler; duplicate mode names fail fast.

### ContractContributionRegistry

Builds capability contracts once from immutable contributions. A feature cannot mutate shared contract dictionaries after startup.

## Migration sequence

1. Add registries and canonical core invocation points.
2. Migrate sensory apparatus.
3. Migrate attention router and attention keyring.
4. Migrate image drawer continuation.
5. Migrate Workshop sandbox.
6. Rebase resident script shelf onto the registries.
7. Remove legacy installers and package-import bootstrap side effects.

## Required tests

- bootstrap called repeatedly;
- duplicate component registration;
- duplicate capability, panel, drawer-mode, and contract names;
- alternative module import order;
- module reload after bootstrap;
- missing required component;
- partial startup failure;
- deterministic composition report in `doctor`;
- no production assignments to protected private integration targets.

## Completion condition

Issue #25 closes only when `vestigia.__init__` exports public symbols without installing behavior and all built-in features enter through explicit collision-checked registries.