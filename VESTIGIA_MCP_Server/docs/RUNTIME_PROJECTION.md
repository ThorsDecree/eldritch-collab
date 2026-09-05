# Runtime projection boundary

VESTIGIA MCP must not become a second Runtime capability ontology.

The load-bearing direction is:

```text
Runtime CapabilityRegistry / HousePort
                |
                v
        read projection adapter
                |
                v
             MCP host
```

One fact, one authority, many routes.

## Current v0.1 projection

The first bridge is deliberately read-heavy and optional.

MCP exposes three stable tools:

- `runtime.status`
- `runtime.capabilities`
- `runtime.call`

`runtime.capabilities` is derived from the live Runtime registry. A Runtime capability is
projectable only when its current executable contract says all of the following:

- callable now;
- dispatchable through the Runtime tool-action lane;
- `confirmation == none`;
- not outward-facing;
- every declared effect is `filesystem:read` or `database:read`.

The MCP layer does not maintain a second list of Runtime action names or copy Runtime input
schemas. `runtime.capabilities(target)` returns the Runtime-owned live contract for one projected
action. `runtime.call` then dispatches through `HousePort.dispatch`, so Runtime policy,
authorizers, validation, and durable Runtime receipts remain in force.

The generic `runtime.call` surface is intentional. Host applications may cache MCP tool schemas
for a conversation. Keeping Runtime's evolving action vocabulary behind a stable projection
avoids making the MCP descriptor cache authoritative over Runtime.

## Cross-layer evidence

Every `runtime.call` generates one MCP `request_id` and passes the same value into Runtime as the
HousePort `turn_id`/bridge request identifier.

```text
MCP request
  -> MCP policy decision / audit event
  -> Runtime HousePort dispatch
  -> Runtime receipt
```

The shared ID is a join key, not evidence laundering. A Runtime receipt proves what Runtime
recorded. An MCP receipt proves what MCP recorded. Neither silently proves the other's policy
or external effect.

## Important current boundary

The bridge embeds a Runtime `HousePort` in the MCP process. It does **not** instantiate
`CoreRuntime` or a language-model provider, and it does not grant provider calls.

Opening a HousePort may initialize/maintain Runtime-derived schemas, indexes, and receipts.
Therefore "read-only" here means no canonical resident/Archive/outward mutation through the
projected action surface; it does not mean the Runtime's private derived bookkeeping database
is byte-for-byte untouched.

A later local Runtime IPC/daemon port may replace the embedded bridge if stronger single-process
custody or cross-process coordination becomes desirable. That transport change must not change
the capability authority model.

## Runtime -> MCP context source

Do not special-case MCP reads inside `ContextAssembler`.

The next clean direction is a Runtime context-source composition seam, then an optional
`VestigiaArchiveMcpSource` implementation. The local-folder source remains available and MCP
remains replaceable/optional. Context receipts must preserve source class, query, truncation,
provenance, and authority/advisory status.

## Future consequence boundary

Write-capable projection is intentionally out of scope for this slice.

Before PREPARE/ACT projection exists, the shared architecture needs:

1. explicit scoped grants / authority epoch;
2. preview/dry-run semantics;
3. hash-bound staged objects for reviewable changes;
4. final dispatch recheck at the last reversible boundary;
5. cross-layer request IDs and separate receipts at every layer.

Local code execution should extend Runtime's existing Workshop/script-shelf architecture rather
than introduce arbitrary MCP shell access. The intended shape is named execution profiles,
bounded working directories, filtered environments, explicit network/write policies, process
containment, structured stdout/stderr/exit evidence, and separate promotion authority.

Receipt is not memory. Projection is not authority. Generated indexes are not source truth.
