# VESTIGIA MCP Server

A local-first, consent-gated capability broker for VESTIGIA deployments.

The server is not intended to make a model "omnipotent on the desktop." It creates a narrow,
auditable semantic boundary between a deployment and explicitly exposed capabilities.

## Design invariant

```text
MCP host -> VESTIGIA MCP -> live policy -> adapter -> local/external system
                       \-> audit receipt
```

The model-facing description of a tool is not authority. The live server policy is authority.
Unknown capabilities are denied by default.

The capability vocabulary is deliberately split into three effect classes:

- **PERCEIVE** - read or inspect without changing the target system.
- **PREPARE** - create a draft, staged action, crop, queue item, or other reversible working state.
- **ACT** - cause an externally consequential or canonical mutation.

Version `0.2.0.dev0` ("Lantern & Red Thread") remains PERCEIVE-only while the Archive surface
is hardened before adding hands.

## Archive surface: live house vs. snapshot witness

The server can be pointed at:

- a **live** unpacked Archive directory; and
- a **snapshot** that is either an unpacked directory or a ZIP file.

Tools:

- `archive.status`
- `archive.list`
- `archive.read_text`
- `archive.diff`
- `archive.diff_detail`

`archive.diff_detail(path)` hashes only the requested path on each side and reports whether it
is added, removed, changed, unchanged, or absent. It is intended for seam inspection after a
broad diff without re-hashing unrelated files.

When the configured snapshot itself lives inside the live Archive root, the server excludes
that snapshot path from the live view automatically. The witness is not counted as house
content merely because it sits inside the house directory. `archive.status` reports active
exclusions explicitly.

Resources:

- `vestigia://archive/live/manifest`
- `vestigia://archive/snapshot/manifest`

No tool in the current slice modifies either Archive source.

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
layout relative to the project directory:

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
- UTF-8 text reads have a configured byte ceiling.
- Unknown capabilities are denied by default.
- Audit receipts store an argument hash rather than raw tool arguments.
- MCP-owned state is kept outside the Archive roots.

See `docs/ARCHITECTURE.md` and `docs/THREAT_MODEL.md`.

## v0.2 - Lantern & Red Thread

Current / near-term work:

1. Exclude snapshot witnesses from nested live roots. **Done.**
2. Add one-path diff detail for fast seam inspection. **Done.**
3. Add Archive text search and bounded recent-change views.
4. Inspect the canonical `00_Bootloader/house_index.json` registry against the live filesystem.
5. Add orphan/unindexed and broken-link diagnostics without repairing canonical content.
6. Make audit receipts queryable through read-only MCP tools.
7. Add top-level VESTIGIA status/diagnostics resources.

After the perception layer is strong, the next load-bearing phase is the deployment Keyring:
explicit scoped grants and hash-bound confirmations for PREPARE/ACT capabilities. Runtime and
social adapters come after that boundary exists.
