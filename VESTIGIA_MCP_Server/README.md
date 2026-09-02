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

Version `0.1.0.dev0` exposes only PERCEIVE-class Archive capabilities.

## First vertical slice: live Archive vs. snapshot

The server can be pointed at:

- a **live** unpacked Archive directory; and
- a **snapshot** that is either an unpacked directory or a ZIP file.

Initial tools:

- `archive.status`
- `archive.list`
- `archive.read_text`
- `archive.diff`

Initial resources:

- `vestigia://archive/live/manifest`
- `vestigia://archive/snapshot/manifest`

No tool in this slice modifies either Archive source.

## Setup

Requires Python 3.11+.

```powershell
cd VESTIGIA_MCP_Server
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set the environment variables in `.env` or in your shell. The package itself reads normal
environment variables and does not require dotenv at runtime.

For an MCP host that launches local stdio servers:

```powershell
vestigia-mcp
```

For development with the official MCP CLI/Inspector, install the SDK CLI extra separately:

```powershell
python -m pip install "mcp[cli]>=2,<3"
mcp dev src/vestigia_mcp/server.py:mcp
```

## Safety properties in the scaffold

- Archive sources are read-only by construction.
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

## Near-term roadmap

1. Harden and exercise the Archive adapter against a real VESTIGIA live tree and snapshot.
2. Add an explicit deployment grant registry and confirmation tokens for PREPARE/ACT.
3. Add a VESTIGIA Runtime adapter without bypassing the Runtime continuity core.
4. Add Discord as the first social adapter.
5. Add a browser/local bridge for contexts where an official platform API is insufficient.
6. Keep platform-specific semantics behind adapters; do not pretend every social surface is the same.
