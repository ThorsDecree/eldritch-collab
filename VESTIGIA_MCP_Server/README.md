# VESTIGIA MCP Server

A local-first, consent-gated capability broker for VESTIGIA deployments.

The server is not intended to make a model "omnipotent on the desktop." It creates a narrow,
auditable semantic boundary between a deployment and explicitly exposed capabilities.

## Design invariant

```text
MCP host -> VESTIGIA MCP -> live policy / projection -> local system
                       \-> MCP audit receipt
```

The model-facing description of a tool is not authority. Executable policy is authority.
Unknown native MCP capabilities are denied by default. When Runtime is linked, Runtime's own
`CapabilityRegistry` / `HousePort` remains authoritative for Runtime actions.

The native MCP capability vocabulary is deliberately split into three effect classes:

- **PERCEIVE** - read or inspect without changing the target system.
- **PREPARE** - create a draft, staged action, crop, queue item, or other reversible working state.
- **ACT** - cause an externally consequential or canonical mutation.

Version `0.2.0.dev0` ("Lantern & Red Thread") remains PERCEIVE-only while perception,
provenance, and cross-layer identity are hardened before adding hands.

All current tools advertise MCP read-only/non-destructive/non-open-world annotations so hosts
can frame them accurately. Those annotations are descriptive hints only; executable server and
Runtime policy remain authoritative.

## Sensory surface

### Archive: live house vs. snapshot witness

The server can be pointed at:

- a **live** unpacked Archive directory; and
- a **snapshot** that is either an unpacked directory or a ZIP file.

Tools:

- `archive.status`
- `archive.list`
- `archive.read_text`
- `archive.search_text`
- `archive.diff`
- `archive.diff_detail`
- `archive.registry_status`

`archive.search_text` performs literal, line-oriented search across configured UTF-8 text-like
files. It is deliberately not fuzzy or semantic search. Results include path, line number, a
bounded excerpt, total matching lines, and explicit counts for oversized/non-UTF-8 files that
were skipped.

`archive.diff_detail(path)` hashes only the requested path on each side and reports whether it
is added, removed, changed, unchanged, or absent. It is intended for seam inspection after a
broad diff without re-hashing unrelated files.

`archive.registry_status(source)` reads the canonical `00_Bootloader/house_index.json`, resolves
its anchor/resident/Garden targets against the selected Archive source, and reports missing or
duplicate registered targets without repairing anything.

When the configured snapshot itself lives inside the live Archive root, the server excludes
that snapshot path from the live view automatically. The witness is not counted as house
content merely because it sits inside the house directory. `archive.status` reports active
exclusions explicitly.

Resources:

- `vestigia://archive/live/manifest`
- `vestigia://archive/snapshot/manifest`

### Runtime projection: one authority, another route

Optional tools:

- `runtime.status`
- `runtime.capabilities`
- `runtime.call`

This is intentionally **not** a second Runtime capability ontology.

```text
Runtime CapabilityRegistry / HousePort
                |
                v
        read projection adapter
                |
                v
             MCP host
```

`runtime.capabilities()` derives its surface from the live Runtime registry. The first
projection admits only capabilities that Runtime currently reports as callable, non-outward,
confirmation-free, and composed entirely of `filesystem:read` / `database:read` effects.
`runtime.capabilities(target)` returns the Runtime-owned full contract and JSON schema for one
projected action.

`runtime.call(action, arguments)` checks the same projection again, forces a non-continuing
`after=finish` invocation, and dispatches through `HousePort.dispatch`. Runtime validation,
policy/authorizers, and durable Runtime receipts therefore remain in force. MCP does not call a
provider or instantiate `CoreRuntime` through this bridge.

Each projected call receives one `request_id`. That ID is written into the MCP audit event and
passed into Runtime as the HousePort turn/request identifier, giving the two independent receipt
layers an explicit join key without pretending either receipt proves the other.

The embedded HousePort may maintain Runtime-derived indexes/schemas and action receipts. The
projection's "read-only" promise means no canonical resident/Archive/outward mutation through
the projected action surface; it does not promise a byte-for-byte untouched private Runtime
bookkeeping database.

See `docs/RUNTIME_PROJECTION.md` for the boundary and future Runtime -> MCP context-source plan.

### Receipts and proprioception

Additional read-only tools:

- `receipts.recent`
- `vestigia.status`

`receipts.recent` exposes recent MCP capability receipts for provenance/debugging. Receipts
contain the SHA-256 of canonicalized tool arguments rather than raw arguments. Filters are
available for capability, outcome, and cross-layer `request_id`.

`vestigia.status` reports the running server version, deployment ID, current executable MCP
policy surface, Archive configuration, optional Runtime linkage configuration, and bounded
audit-ledger health.

No tool in the current slice modifies either Archive source or any external system. Read tools
do append MCP-owned audit receipts outside the Archive roots. Runtime projected reads preserve
Runtime's own receipt path as a separate evidence layer.

## Setup

Requires Python 3.11+.

```powershell
cd VESTIGIA_MCP_Server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Fill in `.env` with the local live Archive, snapshot, state directory, and deployment ID.

The production package itself reads only normal process environment variables and does not
search the filesystem for `.env` files. The checked-in `dev_server.py` development entrypoint
loads the project-local `.env` before importing the MCP server so Inspector-launched stdio
processes receive the intended configuration.

### Optional Runtime linkage

Install the sibling Runtime package into the MCP virtual environment once:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "..\VESTIGIA_Runtime"
```

Then point MCP at one resident Home:

```text
VESTIGIA_MCP_RUNTIME_HOME=C:\path\to\VESTIGIA_Runtime\homes\resident
VESTIGIA_MCP_RUNTIME_ENV_FILE=C:\path\to\VESTIGIA_Runtime\.env
```

The env-file setting is optional. The bridge does not initialize a language-model provider, but
an explicit Runtime env file is preferable when the Home's effective configuration depends on
it.

For Inspector development, place those settings in this project's `.env`. For the production
stdio/tunnel launcher, set `VESTIGIA_MCP_RUNTIME_HOME` (and optional env-file path) in the
launching process or as ordinary Windows user environment variables. The batch launcher does
not parse `.env` files or embed credentials.

For an MCP host that launches local stdio servers:

```powershell
vestigia-mcp
```

For development with the official MCP CLI/Inspector, install the SDK CLI extra separately:

```powershell
python -m pip install "mcp[cli]>=2,<3"
mcp dev dev_server.py --with-editable .
```

The Inspector itself uses Node/npm/npx as development tooling; Node is not a runtime dependency
of the VESTIGIA MCP server.

## One-click Secure MCP Tunnel launcher

The repository includes:

```text
Start VESTIGIA MCP Tunnel.bat
```

The launcher expects the tunnel client at:

```text
tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe
```

and uses the existing `vestigia-local` profile by default. It resolves the current Garden
Archive layout relative to the project directory:

```text
GARDEN\
├── VESTIGIA\
│   └── Anima.zip
└── VESTIGIA_MCP_Server\
```

It intentionally does **not** contain, persist, or echo `CONTROL_PLANE_API_KEY`. Set that key as
a Windows user environment variable (or in the launching shell) before double-clicking the
batch file. An alternate tunnel profile may be supplied as the first argument.

## Safety properties

- Archive sources are read-only by construction.
- The configured snapshot witness is excluded from a nested live root automatically.
- Relative paths reject absolute paths and `..` traversal.
- Directory reads are containment-checked after path resolution.
- Symlink files are not enumerated.
- ZIP members are never extracted and unsafe/duplicate member paths are rejected.
- Arbitrary binary files are not returned through `archive.read_text`.
- Literal search only scans configured text-like suffixes and enforces the same per-file byte ceiling.
- Non-UTF-8 and oversized search candidates are reported as skipped rather than silently coerced.
- Canonical registry diagnostics report discrepancies without modifying the Archive.
- Runtime projection is derived from Runtime's own executable registry rather than copied into MCP.
- Runtime projected calls still pass through `HousePort.dispatch` and create Runtime receipts.
- The current Runtime projection cannot dispatch outward/confirmed/write capabilities.
- Unknown native MCP capabilities are denied by default.
- MCP audit receipts store an argument hash rather than raw tool arguments.
- Cross-layer Runtime calls preserve a shared request ID without blending receipt authority.
- MCP-owned state is kept outside the Archive roots.
- Current MCP tool annotations explicitly advertise read-only, non-destructive, closed-world behavior.

See `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, and `docs/RUNTIME_PROJECTION.md`.

## v0.2 - Lantern & Red Thread

Current / near-term work:

1. Exclude snapshot witnesses from nested live roots. **Done.**
2. Add one-path diff detail for fast seam inspection. **Done.**
3. Add bounded literal Archive text search with explicit skip accounting. **Done.**
4. Inspect canonical `00_Bootloader/house_index.json` targets against Archive contents. **Done.**
5. Make MCP audit receipts queryable. **Done.**
6. Add top-level VESTIGIA status/proprioception. **Done.**
7. Project Runtime's existing read capability contracts through MCP. **Initial bridge done.**
8. Preserve a request ID across MCP -> Runtime receipt layers. **Done for projected calls.**
9. Add `archive.health`, coverage canaries, orphan/unindexed, and broken-link diagnostics.
10. Add `system.identity` with source/config/capability fingerprints and qualification state.
11. Add bounded recent-change/watch views without turning the snapshot witness into a hidden mutable cache.
12. Add a Runtime context-source composition seam and optional MCP Archive source.

Before write-capable projection, the next load-bearing phase is the deployment Keyring: explicit
scoped grants, authority epochs, dry-run/preview semantics, hash-bound staged objects, and a final
dispatch recheck at the last reversible boundary.

Local execution should extend Runtime's existing Workshop/script shelf rather than introduce a
raw MCP shell. Staged filesystem patches and bounded execution profiles belong behind that same
Runtime authority/evidence model.
