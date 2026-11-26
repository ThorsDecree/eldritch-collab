# Eldritch Collaboration

The development home for the **VESTIGIA Runtime**, **VESTIGIA MCP Server**, and
**House Mechanic**: local-first tools for resident continuity, bounded access,
and reviewable development work.

VESTIGIA gives a resident a portable home for identity anchors, memory,
transcripts, artifacts, tools, and explicit boundaries. The inference provider
can be local or remote. The project treats source material as data rather than
as authority, and uses capabilities, grants, and receipts to make consequential
actions visible and bounded.

## Where to start

- [VESTIGIA Runtime](VESTIGIA_Runtime/README.md) — a portable continuity
  runtime for one active resident per turn. Start with its
  [setup guide](VESTIGIA_Runtime/ELI5_SETUP.md),
  [operator cheatsheet](VESTIGIA_Runtime/docs/OPERATOR_CHEATSHEET.md), or
  [resident cheatsheet](VESTIGIA_Runtime/docs/RESIDENT_CHEATSHEET.md).
- [VESTIGIA MCP Server](VESTIGIA_MCP_Server/README.md) — a local-first
  capability broker for the Archive and connected runtimes, with executable
  policy, scoped access, and audit receipts.
- [VESTIGIA House Mechanic](VESTIGIA_House_Mechanic/README.md) — a separate
  host-side development plane with named recipes and bounded operations.
- [Daemon-Bridge](https://github.com/ThorsDecree/Daemon-Bridge) — the separate
  multi-resident Discord runtime; the MCP Server can connect to it through an
  explicitly configured loopback interface.

## Start a Windows pilot

From the root of the pilot checkout, prepare the two local virtual environments
before creating or updating the pilot tunnel profile.

```powershell
.\Start-VESTIGIA-Suite.ps1 -PrepareOnly
```

Use the pilot MCP Server's `.venv\Scripts\vestigia-mcp.exe` as the stdio
command in its existing tunnel profile. The checked-in [tunnel launcher](VESTIGIA_MCP_Server/README.md#one-click-secure-mcp-tunnel-launcher)
loads its ignored local sidecar. For combined startup it expects explicit
`VESTIGIA_MCP_LIVE_ARCHIVE_ROOT`, `VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT`,
`VESTIGIA_MCP_STATE_DIR`, and `VESTIGIA_MCP_DEPLOYMENT_ID` settings for the
pilot. Configure any pilot write grants in the sidecar as well.

```powershell
.\Start-VESTIGIA-Suite.ps1 -Profile Vex-local `
  -RuntimeHome .\VESTIGIA_Runtime\homes\vex `
  -RuntimeEnvFile .\VESTIGIA_Runtime\.env -Mode Discord
```

The launcher opens the tunnel in its own console and runs the selected Runtime
interface in the current session. Omit `-Mode Discord` for the local web UI on
port 8767, or supply `-WebPort` to select another port. Close each service
through its own console. The launcher expects an existing `home.yaml` and
Runtime environment file.

To use an operator-maintained combined Porchlight launcher, pass
`-McpLauncher .\VESTIGIA_MCP_Server\Start-VESTIGIA-MCP-Porchlight.bat` with
the same arguments. The custom launcher receives the selected profile and
Runtime paths in its environment. Check its sidecar and its own routing
settings before using it, since those settings are managed by that launcher.

## Current development and release status

The Runtime package on development `main` is **`0.8.0.dev0`**. This is a
development version, not a claim that a stable v0.8 release has shipped. The
last formally validated Runtime release milestone is **v0.7.0 — The Resident's
Drawers**; its [validation](VESTIGIA_Runtime/docs/releases/v0.7.0-validation.md)
and [custody record](VESTIGIA_Runtime/docs/releases/v0.7.0-custody.md) preserve
the exact evidence. Consult the [Runtime README](VESTIGIA_Runtime/README.md)
and [changelog](VESTIGIA_Runtime/CHANGELOG.md) for current development status.

The MCP Server package on `main` is **`0.10.0.dev0`**. Each component has its
own configuration, dependencies, and authority boundaries; installing one does
not automatically enable or authorize the others.

Current Runtime development includes resident-controlled context drawers,
portable homes and continuity ledgers, local house and workspace tools, image
and vision features, bells, bounded workshop tooling, and optional MCP Archive
access. Availability and authority depend on the deployed configuration: the
live executable capability contract and operator grants take precedence over
summary descriptions here.

## Project documentation

- [Runtime architecture](VESTIGIA_Runtime/ARCHITECTURE.md),
  [configuration](VESTIGIA_Runtime/docs/CONFIGURATION.md), and
  [providers](VESTIGIA_Runtime/docs/PROVIDERS.md)
- [MCP Server documentation](VESTIGIA_MCP_Server/README.md), including
  [Daemon-Bridge integration](VESTIGIA_MCP_Server/docs/DAEMON_BRIDGE.md)
- [Repository roadmap](ROADMAP.md) — milestone history and planned work; some
  status descriptions may lag current development
- [Contributing](CONTRIBUTING.md)

Development canon is `main`. Work enters through a branch and pull request;
release tags and verified artifacts identify packaged distributions. Keep
credentials out of the repository and consult each component's setup guide
before enabling external connections.
