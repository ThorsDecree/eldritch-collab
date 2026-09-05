# Architecture

## Purpose

VESTIGIA MCP is a capability broker, not a second continuity runtime and not a generic desktop
remote-control daemon.

It should let different deployments perceive and eventually prepare or perform bounded actions
through one stable semantic boundary while preserving identity, consent, provenance, and
platform-specific meaning.

## Core flow

```text
MCP host / resident deployment
          |
          v
+----------------------+       +------------------+
| VESTIGIA MCP server  |------>| append-only-ish  |
|                      |       | audit JSONL      |
| capability registry  |       +------------------+
| policy engine        |
| adapter boundary     |
+----------+-----------+
           |
     +-----+----------------------+-------------------+
     |                            |                   |
 Archive adapter           Runtime adapter      Social adapters
 live + snapshot           continuity core      Discord / browser / ...
```

The MCP protocol provides discovery and invocation. VESTIGIA provides jurisdiction.

## Effect classes

Every capability has an effect class independent of how harmless its name sounds.

### PERCEIVE

Reads state without intentionally mutating the target system.

Examples:

- `archive.read_text`
- `archive.diff`
- `runtime.search_memory`
- `social.read_thread`

### PREPARE

Creates reversible or staged working state that is not yet the final external consequence.

Examples:

- `social.draft_reply`
- `social.stage_reply`
- `media.prepare_crop`

### ACT

Mutates canonical state or causes an external consequence.

Examples:

- `social.publish_reply`
- `archive.append_note`
- `filesystem.move`

PREPARE and ACT are intentionally absent from v0.1.

## Policy invariant

Tool registration is not authorization.

A callable handler must still pass the live policy engine. Unknown capability names are denied.
Future deployment-specific grants will refine the default policy by deployment, resident,
account, target, and effect class.

## Archive source model

The first adapter gives semantic names to two different sources:

- `live`: an immediately-current unpacked directory.
- `snapshot`: the latest stable snapshot, as a directory or ZIP.

The adapter never extracts the ZIP and never writes either source. Comparison is by relative
path plus SHA-256 content digest so same-size changes are not missed.

The server is intentionally honest about cost: a full `archive.diff` hashes the files it
compares. Caching can be added later, but a stale hidden cache should not masquerade as the
current Archive.

## MCP transport

Local desktop integration starts with stdio. The host launches the server as a subprocess and
owns stdin/stdout.

Streamable HTTP is reserved for deployments that actually need a network boundary. Remote
exposure must not be enabled merely for convenience; authentication, origin/host controls,
and deployment grants become mandatory at that point.

## Runtime relationship

The existing Runtime invariant remains load-bearing:

```text
Interface -> normalized message -> continuity core -> provider -> normalized response
```

An MCP Runtime adapter must call supported Runtime/core interfaces. It must not reach around the
continuity core to assemble identity, retrieve private memory, mutate continuity, or invoke a
provider behind the Runtime's back.

## State

MCP-owned state belongs under `VESTIGIA_MCP_STATE_DIR`, outside Archive roots.

The initial audit ledger is JSONL for legibility and easy inspection. It is a receipt log, not
a cryptographic tamper-evident ledger. A future SQLite/event model can replace it without
changing the adapter contract.
