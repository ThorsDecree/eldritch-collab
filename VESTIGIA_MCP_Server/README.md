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

Version `0.9.0.dev0` expands the opt-in GameTable reference module with pending effects,
seat-filtered hidden-zone operations, bounded randomness receipts, state repair, atomic
tap bundles, and consented standing yields. It remains an event-sourced, rules-light Magic/Commander table engine: MCP brokers
bounded shared state without becoming either a generic desktop controller or a second continuity
runtime. Canonical Archive promotion remains independently staged and prefix-granted.

Sensory tools advertise read-only/non-destructive/non-open-world annotations. Staging and
Runtime workspace writes advertise local non-open-world mutation; `archive.promote` and
`archive.promote_directory` advertise potentially destructive canonical mutations. Those annotations are descriptive hints only;
executable MCP policy, deployment grants, base hashes, and target policy remain authoritative.

## Sensory surface

### Archive: live house vs. snapshot witness

The server can be pointed at:

- a **live** unpacked Archive directory; and
- a **snapshot** that is either an unpacked directory or a ZIP file.

Tools:

- `archive.status`
- `archive.list`
- `archive.read_text`
- `archive.read_bytes`
- `archive.read_media`
- `archive.search_text`
- `archive.diff`
- `archive.diff_detail`
- `archive.registry_status`
- `archive.write_capabilities`
- `archive.stage_text`
- `archive.stage_directory`
- `archive.stage_list`
- `archive.stage_inspect`
- `archive.stage_discard`
- `archive.promote`
- `archive.promote_directory`

`archive.search_text` performs literal, line-oriented search across configured UTF-8 text-like
files. It is deliberately not fuzzy or semantic search. Results include path, line number, a
bounded excerpt, total matching lines, and explicit counts for oversized/non-UTF-8 files that
were skipped.

Each search hit repeats its source ID and provenance class so extracted hits remain attributable
outside the surrounding response envelope.

`archive.list` and `archive.search_text` retain their existing digest-bound cursors. Archive
browse cursors from `archive.read_text` and `archive.read_bytes` are a separate, signed,
expiry-limited continuation format. They bind the source, normalized path, operation,
authorization-policy scope, snapshot hash, offset, and page size. Pass one back unchanged with
the same request. A valid continuation reports `same_snapshot`; if the file changes during the
browse, it reports `file_changed_during_browse` and returns no mixed-revision data.

Each browse page reports `budget.requested_bytes`, `returned_bytes`, `truncated`, and
`remaining_bytes`. A successful page is bounded evidence, not a statement that the whole file
was read. Text pages preserve UTF-8 and include exact raw byte spans plus best-effort line spans.
Byte pages return base64 transport data for regular files (including SQLite files); they are not
database queries, and their base64 expansion is included in the configured output ceiling.

`archive.search_text` remains literal and bounded. Its `skipped_oversize` and
`skipped_non_utf8` counters describe material it did not scan; paging does not make that search
exhaustive.

`archive.read_media(source, path)` returns one PNG, JPEG, GIF, or WebP as a native MCP image
content block plus bounded metadata. It enforces an independent byte ceiling and checks the
file suffix against its binary signature. SVG is intentionally excluded from this surface.

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

### Canonical Archive staging and promotion

Canonical mutation is a separate opt-in lane for bounded UTF-8 text and directory creation:

```text
candidate text -> durable MCP stage -> inspect/revalidate -> atomic live promotion
```

`archive.stage_text` records content, path, reason, content hash, and the target's current base
hash under `VESTIGIA_MCP_STATE_DIR/archive-stages/`. It does not modify the Archive.
`archive.promote` requires the returned `stage_id` and `proposal_sha256`, checks the deployment's
current prefix grant again, refuses if the live target changed after staging, and writes through
an atomic same-directory replacement. Stage inspection can return metadata alone or the bounded
candidate content. Promotion never targets the snapshot.

`archive.stage_directory` records the nearest existing parent and every missing directory without
touching the Archive. `archive.promote_directory` revalidates that plan, creates each component,
and attempts to roll back newly created empty directories if a later component fails.

Delete, move, binary/media writes, arbitrary paths, and a direct-write bypass remain excluded.
See `docs/CANONICAL_ARCHIVE_WRITES.md`.

Resources:

- `vestigia://archive/live/manifest`
- `vestigia://archive/snapshot/manifest`

### Named external read-only mounts

An operator may expose directories outside VESTIGIA through a JSON registry. Callers use a
stable mount ID plus relative paths; they cannot submit host absolute paths. Mounts support:

- `mount.status`
- `mount.list`
- `mount.read_text`
- `mount.read_media`
- `mount.search_text`

Mount responses name their source as an `operator_named_read_only_root`. They do not imply
canonical Archive membership, continuity authority, or write access.

### Optional GameTable: private tabletop state, not a rules oracle

When enabled, GameTable is MCP-owned shared state outside the Archive. It starts with two
profiles:

- `magic.commander.v0.1` — 40 life, opening hands of seven, Commander-visible zones, and
  Magic-shaped turn/priority steps;
- `generic.card-table.v0.1` — a smaller neutral profile for other card games.

Tools:

- `game.profiles`
- `game.status`
- `game.create`
- `game.load_deck`
- `game.start`
- `game.mulligan`
- `game.keep`
- `game.view`
- `game.events`
- `game.act`
- `game.pass_priority`
- `game.yield`
- `game.shortcut_propose`
- `game.shortcut_respond`
- `game.concede`

GameTable's state is an SQLite materialized view plus an event chain. `game.create` returns one
opaque **development seat token** per configured seat; each recipient then calls `game.load_deck`
with only their own token before the table starts. A public `game.view` returns public zones, life
totals, hand/library counts, and turn state; a view with a valid seat token adds only that seat's
hand. Public events say, for example, “Jeff drew a card”; card details are addressed only to
Jeff's event/view projection. Library order is never returned through ordinary views.

The first profile deliberately checks only table flow and control bounds: revision matching,
current priority, seat ownership/control, supported zones, counters, damage, life, passes,
standing yields, and explicitly consented shortcuts. Commander’s generic untap happens without a priority window and
its normal draw happens privately on entering the draw step. Mutations return public state by
default plus only the actor-addressed private event delta; `response_view="seat"` is explicit.
It
does **not** validate deck legality, ship card data, execute card text, resolve targets/triggers,
or claim to enforce Magic's comprehensive rules. Players remain the rules authority.

Standing yields provide the ergonomic fast path for routine empty windows without heuristic
automation. `game.yield` accepts `{"kind":"step"}`, `{"kind":"turn"}`, or an explicit
`{"kind":"target","turn_number":N,"step":"..."}` scope. Each active seat must record a
yield; the reducer advances only to the earliest target any seat requested, preserving consent.
The resulting event is compact and includes automatic profile actions (such as the private turn
draw) without exposing hidden cards. A normal action, shortcut, concession, or state-changing
priority pass clears outstanding yields, and pending effects remain a hard blocker.

Development seat tokens are bearer credentials, not a completed multi-principal Keyring. They are
stored only as SHA-256 verifiers and are absent from readable MCP audit receipts, but they do not
protect against the local machine operator who can read the server's SQLite state. A future
principal/Keyring layer can replace this binding without changing the GameTable event model. See
`docs/GAMETABLE.md`.

### Runtime projection: one authority, multiple routes

Optional tools:

- `runtime.status`
- `runtime.list`
- `runtime.capabilities`
- `runtime.call`
- `runtime.write_capabilities`
- `runtime.write`

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
`runtime.capabilities(target)` returns the projected action's wrapper-valid `input_schema`, its
authoritative native envelope as `runtime_input_schema`, and the machine-readable
`wrapper_owned_fields` distinction. Callers should place only `input_schema` fields inside
`runtime.call.arguments`.

Every Runtime tool accepts an optional `runtime_id`. `runtime.list` reports configured houses and
the default route. With legacy environment configuration the implicit ID is `default`; with a
registry file the operator supplies stable IDs and an explicit default.

`runtime.call(action, arguments, runtime_id?)` checks the same projection again, forces a non-continuing
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

`runtime.write_capabilities(target)` exposes only Runtime-local mutation contracts named in
`VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS` and still reported by Runtime as callable,
confirmation-free, non-outward, and limited to supported workspace/draft/audit effect classes.
Its focused contract uses the same `input_schema` / `runtime_input_schema` /
`wrapper_owned_fields` distinction as the read projection.
`runtime.write(action, arguments)` re-checks that projection at dispatch, forces
`after=finish`, passes through `HousePort.dispatch`, and preserves the shared request ID in both
receipt layers. An empty action allowlist disables the mutation surface without changing the
tool catalog.

This does not grant Archive mutation. A practical starting grant is
`fs.stage_patch,file.write,file.patch,fs.patch_discard`: proposal staging plus Runtime's existing
bounded `workspace/` text writers. Runtime continues to enforce accessible/writable roots,
maximum write bytes, schemas, optimistic hashes, and receipts.

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

Read tools append MCP-owned audit receipts outside the Archive roots. When explicitly granted,
`archive.promote` may create or replace text beneath configured live-Archive prefixes; it never
modifies the snapshot. Runtime projected reads and explicitly granted local mutations preserve
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

### Optional canonical Archive text promotion

Grant only the relative live-Archive prefixes this deployment may change:

```text
VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES=02_Journal,01_Residents/Liora
VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES=1000000
```

An empty prefix list disables both staging and promotion. Prefixes are path-segment-aware:
granting `02_Journal` covers that directory, not similarly named siblings. The state directory
must remain outside the live Archive. Text target parents must already exist; directory stages
may propose missing descendants.

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

To grant specific bounded Runtime-local mutations, add for example:

```text
VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS=fs.stage_patch,file.write,file.patch,fs.patch_discard
```

Omit the variable or leave it blank for a read-only deployment. Adding an outward-facing or
otherwise ineligible action name does not make it projectable.

For more than one house, copy `runtimes.example.json` to the ignored `runtimes.local.json`, set
absolute Home/env-file paths and per-house write grants, then set:

```text
VESTIGIA_MCP_RUNTIMES_FILE=C:\path\to\VESTIGIA_MCP_Server\runtimes.local.json
```

The registry supersedes the three legacy single-home variables. Restart after changing it.

### Optional named external mounts

Copy `mounts.example.json` to the ignored `mounts.local.json`, give each external directory a
path-safe lowercase ID, then set:

```text
VESTIGIA_MCP_MOUNTS_FILE=C:\path\to\VESTIGIA_MCP_Server\mounts.local.json
```

Each mount is read-only and may set its own text/media byte ceilings. `mount.status()` is cheap;
use `mount.status(include_stats=true)` when a full file-count/byte inventory is actually wanted.
Restart after changing the registry.

### Optional GameTable module

GameTable is disabled unless the deployment enables it:

```text
VESTIGIA_MCP_GAMETABLE_ENABLED=1
```

It stores game state under `VESTIGIA_MCP_STATE_DIR/gametable/` by default. To place that
non-canonical state elsewhere, set an explicit directory outside Archive roots:

```text
VESTIGIA_MCP_GAMETABLE_STATE_DIR=C:\path\to\vestigia-gametable-state
```

Restart the MCP server after changing either value. In the tunnel launcher, add the same settings
to the ignored `Start VESTIGIA MCP Tunnel.local.bat`; the checked-in default leaves GameTable off.

For Inspector development, place those settings in this project's `.env`. For the production
stdio/tunnel launcher, set Runtime and Archive write grants in the launching process or as
ordinary Windows user environment variables. The batch launcher does not parse `.env` files or
embed credentials.

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

The launcher restores this deployment's non-secret Archive and Runtime settings after a reboot,
including bounded Runtime actions, canonical Archive write prefixes, and byte ceilings. Existing
process or local overrides win over those defaults.

It intentionally does **not** contain or echo `CONTROL_PLANE_API_KEY`. For a reboot-persistent
local setup, copy `Start VESTIGIA MCP Tunnel.local.example.bat` to
`Start VESTIGIA MCP Tunnel.local.bat`, replace the placeholder with the key, and keep that ignored
sidecar on Jeff's machine. Alternatively, set the key as a Windows user environment variable or
in the launching shell. An alternate tunnel profile may be supplied as the first argument.

## Safety properties

- Snapshot Archive sources remain read-only by construction.
- Canonical live-Archive mutation is disabled when the path-prefix grant is empty.
- Canonical writes require a durable stage, exact proposal digest, current prefix grant, and
  unchanged optimistic base hash.
- Canonical text promotion is byte-bounded, symlink-refusing, containment-checked, and performed
  through atomic same-directory replacement.
- Delete, move, binary writes, and direct-write bypasses are absent.
- Directory creation uses a separate digest-bound stage/promote lifecycle and refuses stale plans.
- External mounts are operator-named, read-only, relative-path-only, and semantically distinct
  from the canonical Archive.
- Pagination cursors bind source/query/result digests and reject changed views as stale.
- Multi-house Runtime routing preserves a distinct HousePort and write grant set per Runtime ID.
- The configured snapshot witness is excluded from a nested live root automatically.
- Relative paths reject absolute paths and `..` traversal.
- Directory reads are containment-checked after path resolution.
- Symlink files are not enumerated.
- ZIP members are never extracted and unsafe/duplicate member paths are rejected.
- Arbitrary binary files are not returned through `archive.read_text`.
- Archive images use a separate byte ceiling, format allowlist, and binary signature check.
- Literal search only scans configured text-like suffixes and enforces the same per-file byte ceiling.
- Non-UTF-8 and oversized search candidates are reported as skipped rather than silently coerced.
- Canonical registry diagnostics report discrepancies without modifying the Archive.
- Runtime projection is derived from Runtime's own executable registry rather than copied into MCP.
- Runtime projected calls still pass through `HousePort.dispatch` and create Runtime receipts.
- The Runtime read projection cannot dispatch outward/confirmed/write capabilities.
- Runtime mutation requires both an explicit MCP deployment action grant and an eligible live
  Runtime-local contract; outward and confirmed actions remain excluded.
- Unknown native MCP capabilities are denied by default.
- MCP audit receipts store an argument hash rather than raw tool arguments.
- Cross-layer Runtime calls preserve a shared request ID without blending receipt authority.
- MCP-owned state is kept outside the Archive roots.
- MCP tool annotations distinguish sensory reads, reversible preparation, local Runtime
  mutation, and canonical Archive promotion.

See `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/RUNTIME_PROJECTION.md`, and
`docs/CANONICAL_ARCHIVE_WRITES.md`.

## v0.5 - More Rooms, Longer Shelves

1. Add stable cursor pagination for path lists, text files, and literal search. **Done.**
2. Add digest-bound staged directory creation and promotion. **Done.**
3. Add operator-named external read-only mounts. **Done.**
4. Add a multi-house Runtime registry/router with per-house grants. **Done.**
5. Add transactional directory-plus-file change sets. **Next.**
6. Add Archive-aware Git workbench operations. **Next.**
7. Add media metadata indexes/contact sheets and principal/policy introspection. **Next.**

## v0.4 - Canonical Stage & Promotion

1. Add empty-by-default relative path-prefix grants for canonical live-Archive text. **Done.**
2. Store durable create/replace proposals outside the Archive. **Done.**
3. Bind path, content, operation, reason, and base state into a proposal digest. **Done.**
4. Revalidate live base hashes and path grants immediately before promotion. **Done.**
5. Promote through atomic same-directory replacement with separate audit receipts. **Done.**
6. Add list, inspect, validation, and discard lifecycle surfaces. **Done.**
7. Add principal-specific grants, authority epochs, and interactive approval challenges. **Next.**

## v0.3 - Eyes & Bounded Hands

1. Return bounded PNG/JPEG/GIF/WebP Archive files as native MCP image content. **Done.**
2. Keep Archive mutation out of the media lane. **Done.**
3. Add an empty-by-default deployment allowlist for Runtime-local mutations. **Done.**
4. Intersect deployment grants with Runtime's live contract/effect boundary. **Done.**
5. Dispatch granted writes through Runtime `HousePort` with joined receipts. **Done.**
6. Surface Runtime staged-patch availability in house orientation. **Done.**
7. Add principal/target grants and authority epochs. **Partially advanced in v0.4.**

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

The Runtime projection uses explicit named deployment grants plus Runtime's live contract checks
and final `HousePort` dispatch. Canonical Archive promotion is a separate MCP-native, prefix- and
hash-bound lane. The next load-bearing Keyring work is richer principal scoping, authority
epochs, approval challenges, and promotion/release orchestration across multiple artifacts.

Local execution should extend Runtime's existing Workshop/script shelf rather than introduce a
raw MCP shell. Staged filesystem patches and bounded execution profiles belong behind that same
Runtime authority/evidence model.
