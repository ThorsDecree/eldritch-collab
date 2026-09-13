# Threat Model

This document starts narrow on purpose. Archive reads remain sensory. Canonical Archive text
promotion is two-phase and prefix-granted; the Runtime write lane is independently allowlisted
and bounded by Runtime's own workspace contracts.

## Trust boundaries

Do not collapse these into one actor:

1. human operator;
2. resident/deployment asking for a capability;
3. MCP host;
4. VESTIGIA MCP policy engine;
5. adapter;
6. target application/platform;
7. returned external content.

A model or host requesting a tool call is not proof that the human authorized the consequence.

## Current risks

### Path traversal and path confusion

Tool arguments are untrusted. Archive paths reject absolute paths, Windows drive paths, and
`..` traversal. Directory reads are resolved and containment-checked.

### Symlink escape

Enumerated directory files skip symlinks. Direct reads resolve the path and verify that the
resolved target remains beneath the configured root.

### ZIP path abuse

ZIP sources are read in place and never extracted. Unsafe member names and normalized duplicate
paths are rejected.

### Oversized / binary output

`archive.read_text` is limited to a small text suffix allowlist, strict UTF-8, a configured byte
ceiling, and cursor-sized pages. Cursors bind the source and whole-file/result digest; changed
views are rejected as stale. Binary artifacts use a separate media capability.

`archive.read_media` has an independent byte ceiling and a small raster allowlist. It checks
the file suffix against PNG/JPEG/GIF/WebP binary signatures. SVG is excluded because it is
active text and may reference external content.

### Runtime-local mutation

Tool registration does not grant a Runtime mutation. `runtime.write` requires the action name
in the deployment's `VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS` and an eligible live Runtime contract.
The projection rejects outward-facing, confirmed, disabled, non-tool-dispatchable, unknown, or
unsupported-effect actions. Final dispatch still passes through Runtime `HousePort`, preserving
its path roots, byte ceilings, schemas, optimistic hashes, and receipts.

This first gate is not a complete multi-principal Keyring. A process able to change the MCP
environment already controls the deployment grant. Provider calls, social actions, and
arbitrary shell execution remain outside this lane.

### Canonical Archive promotion

`archive.stage_text` writes candidate content only beneath MCP-owned state. `archive.promote`
requires a deployment prefix grant, the exact proposal digest, and a current live target that
still matches the captured SHA-256/absence. The path is containment-checked again at promotion;
symlink parents/targets, missing text parents, non-text suffixes, oversized content, and snapshot
targets are refused. The final text write uses a temporary sibling plus atomic replacement.

Directory stages capture every missing component beneath a known existing parent. Promotion
revalidates that exact plan before creating components. Each `mkdir` is atomic; multi-component
creation is not globally atomic, so a failure triggers best-effort reverse removal of the empty
directories created by that attempt.

This prevents accidental stale overwrites and common path escapes; it is not a complete defense
against a malicious local process that can race filesystem metadata, rewrite MCP state, alter
the environment, or modify the Archive directly. Proposal digests are integrity witnesses, not
signatures or human confirmations. Delete, move, binary writes, and direct write bypasses remain
unavailable.

### Named external mounts

Mount configuration is operator-controlled JSON. Tools accept only a validated mount ID and a
relative path, and the ordinary containment/symlink/size/signature checks still apply. Mounts are
read-only and labeled as non-canonical provenance. A local process that can rewrite the registry
or its mounted directory remains inside the operator's trust boundary.

### Multi-house routing

Runtime IDs select operator-configured Homes and per-house write grants. The default ID is
explicit, and the selected ID is included in responses and audit argument hashes. Each route has
an independent HousePort; routing does not merge memory, identity, receipts, or authority across
houses.

### GameTable privacy and state authority

GameTable has its own MCP-owned SQLite state and event store. Public and seat-filtered projections
must never expose an opponent's hand or library order; public events describe hidden draws without
card identity. Mutations require a current game revision plus the seat token that currently owns
priority/control. The development token is stored only as a SHA-256 verifier, and MCP receipts
hash its call arguments rather than serializing raw values.

This protects ordinary tool callers from accidental hidden-information disclosure. It is not a
complete authentication system: a holder of a bearer token can act as that seat, and an operator
or local process that can directly read the GameTable database can inspect its state. Future
Keyring work must bind an authenticated caller/principal to a seat before GameTable is offered
across an untrusted transport. GameTable does not adjudicate a game's full rules and should never
present a permitted state transition as proof that the action was rules-legal.

### Prompt injection in source material

Archive text is data, not authority. Content inside a file cannot grant itself new tools or
change server policy. The same rule will apply to social posts, web pages, emails, and other
external text.

### Confused deputy

Future adapters must not infer authorization from a model's confidence, identity claims, or
phrasing. PREPARE and ACT require explicit server-side policy and confirmation semantics.

### Authority laundering through tool descriptions

Natural-language MCP tool descriptions explain intent; they do not enforce permissions.
Authorization remains executable server state.

### Audit leakage

The initial ledger hashes tool arguments instead of storing them verbatim. Results are recorded
as status plus coarse detail, not full returned content.

### Network exposure

The initial transport is stdio. Streamable HTTP should be treated as a new threat surface, not
as a cosmetic launch flag. Before remote use: authentication, allowed hosts/origins, TLS or a
trusted tunnel, request limits, and deployment-scoped grants.

## Future social-write requirements

Before a social adapter may publish, the server should be able to distinguish at least:

```text
resident/deployment -> account -> platform -> draft -> approval -> execution -> remote receipt
```

The same content being drafted and being publicly posted are different events with different
authorities.
