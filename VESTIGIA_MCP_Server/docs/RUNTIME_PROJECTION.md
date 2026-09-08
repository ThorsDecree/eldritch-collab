# Runtime projection boundary

VESTIGIA MCP must not become a second Runtime capability ontology.

The load-bearing direction is:

```text
Runtime CapabilityRegistry / HousePort
                |
                v
       read/mutation projection
                |
                v
             MCP host
```

One fact, one authority, many routes.

## Current projections

The bridge is optional. Its read lane remains the default.

MCP exposes three stable tools:

- `runtime.status`
- `runtime.capabilities`
- `runtime.call`

The opt-in mutation lane adds:

- `runtime.write_capabilities`
- `runtime.write`

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

The mutation projection also derives contracts from the live Runtime registry, but intersects
them with `VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS`. A named action is projectable only when its
Runtime contract is callable, tool-dispatchable, confirmation-free, non-outward, contains at
least one supported local mutation effect, and contains no unsupported effect. An empty
deployment allowlist grants nothing.

`runtime.write` re-checks that intersection at final dispatch and then calls
`HousePort.dispatch`. Runtime therefore retains authority over schemas, writable roots, byte
ceilings, optimistic hashes, and Runtime receipts. MCP contributes the deployment-scoped named
grant and its separate audit receipt.

## Cross-layer evidence

Every `runtime.call` and `runtime.write` generates one MCP `request_id` and passes the same value
into Runtime as the HousePort `turn_id`/bridge request identifier.

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

## Runtime consequence boundary

The first local mutation slice is deliberately narrower than a complete Keyring. It supports
explicit named grants to Runtime-local workspace/draft actions and a final live contract check.
It does not itself grant Archive mutation, outward actions, confirmed actions, arbitrary
filesystem paths, provider calls, or a shell. Canonical Archive staging and promotion now exist
as a separate MCP-native boundary documented in `CANONICAL_ARCHIVE_WRITES.md`; they do not widen
Runtime's projection.

Future widening still requires principal/target-scoped grants, authority epochs, approval
challenges, and separate promotion authority for canonical or outward effects. Runtime's staged
patch objects already provide preview, optimistic base hashes, and proposal durability; applying
one to canonical state remains a distinct capability boundary.

Local code execution should extend Runtime's existing Workshop/script-shelf architecture rather
than introduce arbitrary MCP shell access. The intended shape is named execution profiles,
bounded working directories, filtered environments, explicit network/write policies, process
containment, structured stdout/stderr/exit evidence, and separate promotion authority.

Receipt is not memory. Projection is not authority. Generated indexes are not source truth.
