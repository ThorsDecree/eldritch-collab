# Threat Model

This document starts narrow on purpose. The first release is local and read-only, but the
architecture should not make later write capabilities accidentally unsafe.

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

`archive.read_text` is limited to a small text suffix allowlist, strict UTF-8, and a configured
byte ceiling. Binary artifacts will need a separate media capability rather than sneaking
through a text read.

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
