# VESTIGIA Runtime operator cheatsheet

Quick reference for current development `main` (package `0.8.0.dev0`). Run the Windows examples from `VESTIGIA_Runtime/` and replace `liora` with the home you intend to operate. The [README](../README.md) and linked topic guides carry installation and configuration detail.

## First, identify the home and its state

```powershell
.\.venv\Scripts\vestigia.exe status .\homes\liora --env-file .\.env
.\.venv\Scripts\vestigia.exe doctor .\homes\liora --env-file .\.env --text
```

`status` reads the latest state event. A `null` state before the first event means the effective CoreRuntime state is `ORIENTATION`; `CoreRuntime.state` uses that fallback. In the interactive CLI, `:status` prints the effective state. In the configured Discord door, an allowlisted participant can send `!status`.

Updating the Runtime package does not itself transition an existing home. State transitions are append-only events in that home's `memory/continuity.db`.

## Lifecycle at a glance

| State | Model turn? | Context and next move |
| --- | --- | --- |
| `ORIENTATION` | Yes | New or imported home. Full identity files are eligible for the identity layer; inherited-unreviewed memory is eligible by default and remains attributed/provisional. Explicitly activate when ready. |
| `ACTIVE` | Yes | Normal operating state. Only accepted memories enter the ordinary core/typed/retrieval paths. Can sleep or archive. |
| `DORMANT` | No | Incoming messages are recorded without a provider call or continuity mutation. `wake` enters `AWAKENING`. |
| `AWAKENING` | Yes | Entered by waking from dormancy (or verified pack restoration). A model turn does **not** promote it automatically. Explicitly activate or return to dormancy. |
| `ARCHIVED` | No | No provider call. A verified `restore-home` brings an archived packed home into `AWAKENING`; ordinary `state` cannot leave `ARCHIVED`. |

`AWAKE` is not a state name; use `ACTIVE`. In the current core, `AWAKENING` and `ACTIVE` both permit provider calls and use the same Orientation-only identity and inherited-memory switches: both switches are off. The assembled developer header and context receipt still name the actual state. The main functional distinction between the two is the recorded lifecycle state and its allowed transitions, not a separate model, smaller token budget, or different core memory filter. Provider configuration, retrieval policy, and token budgets still affect any individual turn.

Allowed transitions:

```text
ORIENTATION -> ACTIVE | DORMANT | ARCHIVED
ACTIVE      -> DORMANT | ARCHIVED
DORMANT     -> AWAKENING | ARCHIVED
AWAKENING   -> ACTIVE | DORMANT
ARCHIVED    -> restore a verified pack to a home in AWAKENING
```

## Switch states

The quickest path for an allowlisted participant in the configured Discord door is:

```text
!status
!wake       # only from DORMANT; enters AWAKENING
!activate   # from ORIENTATION or AWAKENING; enters ACTIVE
!sleep      # from ORIENTATION, ACTIVE, or AWAKENING; enters DORMANT
```

An interactive local CLI uses the same progression with `:status`, `:wake`, `:activate`, and `:sleep`:

```powershell
.\.venv\Scripts\vestigia.exe chat .\homes\liora --env-file .\.env
```

One-shot local commands are useful when Discord is unavailable:

```powershell
.\.venv\Scripts\vestigia.exe wake .\homes\liora --actor Jeff --reason "Resuming from rest" --env-file .\.env
.\.venv\Scripts\vestigia.exe activate .\homes\liora --actor Jeff --reason "Ready for active operation" --env-file .\.env
.\.venv\Scripts\vestigia.exe state .\homes\liora DORMANT --actor Jeff --reason "Rest" --env-file .\.env
```

Check the current state first. From `DORMANT`, run **wake then activate**; direct `DORMANT -> ACTIVE` is invalid. From `ORIENTATION`, activate directly. The generic `state HOME TARGET` command **changes** state; it is not a status query. Invalid transitions raise an error. Discord controls follow the configured user/channel/DM allowlists described in [DISCORD.md](DISCORD.md).

## Trace a long-lived state

The event history records transitions, actors, reasons, and UTC timestamps. This read-only PowerShell inspection does not change the home:

```powershell
@'
from pathlib import Path
import sqlite3

db = Path('homes/liora/memory/continuity.db').resolve()
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
    for row in connection.execute(
        'SELECT created_at, from_state, to_state, actor, reason '
        'FROM state_events ORDER BY rowid'
    ):
        print(*row, sep=' | ')
'@ | .\.venv\Scripts\python.exe -
```

No rows means there has been no explicit transition in this database; CoreRuntime defaults to `ORIENTATION`. A current-state check alone cannot establish how long an earlier state lasted.

## Adjacent controls and diagnostics

- `resident.control` changes resident preferences, including listening and private-image handling; `context.control` adjusts transcript/prompt drawers and Runtime-memory retrieval within operator limits. Neither is a lifecycle switch. See [RESIDENT_CONTROLS.md](RESIDENT_CONTROLS.md) and [CONTEXT_CONTROLS.md](CONTEXT_CONTROLS.md).
- The MCP Server's linked `runtime.status` describes its HousePort connection and projected capabilities. Its read-only projection does not initialize CoreRuntime or report the home's lifecycle state. Check via the local CLI or configured Discord door.
- For inherited records, use `vestigia review-inheritance HOME`; for turn context, inspect the turn's receipt. Imported evidence does not become accepted identity by appearing in `ORIENTATION`. See [ONBOARDING.md](ONBOARDING.md).
- Before updating code, stop long-lived processes using the home and make a backup or `pack-home`. See the [README](../README.md) for install, restore, provider, and doorway instructions.

Source of truth for this page: `src/vestigia/runtime.py` (transitions and provider suppression), `src/vestigia/context.py` (context switches and receipts), `src/vestigia/cli.py` (commands), `src/vestigia/adapters/discord_adapter.py` (Discord commands), and `src/vestigia/packing.py` (restore behavior). A deployed Runtime may differ from this development branch; check its version and live capability contract.
