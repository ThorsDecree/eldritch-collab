# Resident Script Shelf Runtime Boundary

The first runtime slice of the resident script shelf is deliberately **inert**.
It stores immutable source, provenance, typed input/output contracts, static
inspection evidence, quarantine decisions, and archive state. It does not run
Python.

## Available resident operations

- `draft`
- `receive`
- `list`
- `show`
- `read_source`
- `inspect`
- `quarantine`
- `archive`

The provider-facing capability does not expose `test`, `approve`, `activate`,
`run`, `disable`, or `supersede`.

## Why execution is absent

The existing `local.process` Workshop backend is honest ordinary-bug
containment. It does not enforce network denial, host-filesystem denial,
process-tree containment, CPU limits, or memory limits. Static AST inspection is
review evidence, not isolation.

A stored script therefore requests a future `hardened` execution profile and
carries no granted backend. Inspection never changes that. Source remains
non-callable until a separate design supplies:

1. enforceable hardened isolation;
2. a separately authenticated operator approval path;
3. hash-, schema-, backend-, limit-, and expiry-bound grants;
4. atomic execution lifecycle and revocation receipts.

## Atomicity

Draft version allocation and insertion occur under one `BEGIN IMMEDIATE`
transaction. Inspection evidence, state transition, and lifecycle event commit
or roll back together. State changes use an expected-state predicate so stale
writers fail rather than silently overwriting newer evidence.

## Privacy and authority

Source is content-addressed and private. Listing and Observatory views expose
metadata and hashes, not source bytes. Inspection performs no imports and no
execution. No provider call, outward action, memory adoption, publication, or
resident identity change follows from storage or inspection.
