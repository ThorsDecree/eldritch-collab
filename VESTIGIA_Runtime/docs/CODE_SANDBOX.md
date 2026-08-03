# Resident code-sandbox contract

Status: design contract for the second Workshop Within implementation stage.

The code sandbox lets a resident execute bounded computation without handing code ambient access to
the host, house, network, secrets, providers, doorways, memory, or identity systems.

The central honesty requirement is:

> Process separation is not automatically hostile-code isolation. The Runtime must name which
> guarantees a backend actually provides.

## Isolation profiles

The sandbox advertises one of three profiles.

### `expression`

No general-purpose language runs. The restricted ritual value language evaluates bounded data
transformations. This is the safest default and is suitable for ordinary ritual plumbing.

### `local_process`

Code runs in a fresh child process with an ephemeral working directory, explicit mounts, stripped
environment, time and resource limits, and no intentionally provided network or host handles.

This profile protects the Runtime from ordinary bugs and accidental damage. It is **not** approved
for hostile, unknown, or socially imported code unless the platform backend can enforce all
`hardened` requirements. Language-level filtering, monkey-patching, and a child process alone are
not treated as a security boundary.

### `hardened`

A platform-specific isolation backend provides independently verifiable controls for:

- no network unless an explicit destination port is granted;
- no host filesystem outside declared mounts;
- no inherited process handles or environment secrets;
- process-tree containment and termination;
- CPU, memory, wall-time, output, file, and process ceilings;
- read-only inputs and controlled output extraction;
- no privilege escalation or host service access within the declared threat model.

Imported or third-party scripts require `hardened` unless operator policy explicitly accepts a
weaker profile after previewing the risk. If no hardened backend is installed, such scripts remain
inert rather than silently falling back.

## Backend descriptor

Each backend exposes a side-effect-free descriptor:

```json
{
  "schema_version": "vestigia.sandbox-backend.v0.1",
  "backend_id": "local.process",
  "version": "0.1.0",
  "profiles": ["expression", "local_process"],
  "languages": ["python"],
  "guarantees": {
    "network_deny_enforced": false,
    "filesystem_mounts_enforced": true,
    "environment_stripped": true,
    "process_tree_contained": true,
    "memory_limit_enforced": true,
    "cpu_limit_enforced": false,
    "wall_limit_enforced": true,
    "output_limit_enforced": true,
    "hostile_code_approved": false
  },
  "platform": "windows",
  "health": {
    "configured": true,
    "callable_now": true,
    "reason": null
  }
}
```

A guarantee is `true` only when the backend enforces and tests it. Unsupported guarantees are not
inferred from intent.

## Invocation contract

A sandbox request follows `schemas/workshop-execution.schema.json` and identifies:

- immutable script ID, version, and content hash;
- language and required isolation profile;
- typed input values and mounted object references;
- requested/effective grants;
- working-directory policy;
- resource limits;
- expected output contract;
- timeout and cancellation deadline;
- parent ritual and execution IDs when applicable.

The sandbox receives no Runtime object. The orchestrator materializes only the declared inputs and
ports.

## Filesystem model

The sandbox starts with an empty ephemeral root. Optional mounts are explicit:

```text
/input/<mount-name>   read-only object materialization
/output/              writable extraction directory
/tmp/                 bounded ephemeral scratch
```

No host absolute path is exposed to the script. Mount names are normalized, collision-checked, and
cannot contain traversal, separators, device names, alternate data streams, symlinks, or Unicode
confusables that escape the virtual layout.

Outputs are harvested only from `/output`. Each output is checked for:

- path safety;
- file count and total bytes;
- individual size limit;
- declared media/type contract;
- symlinks and special files;
- executable or archive classification;
- content hash;
- secret-shaped material according to policy.

Harvested files become private workshop artifacts. They are not copied into workspace or executed
without a separate capability.

## Network and provider access

Default network policy is `none`. The script does not receive raw sockets, provider clients, API
keys, or inherited proxy configuration.

Future network access uses a scoped broker port rather than ambient network. The grant declares
scheme, destination, port, method, purpose, request/response limits, redirect policy, cost class,
and receipt behavior. The broker remains outside the sandbox process.

Provider calls likewise use a provider broker capability. Scripts never receive provider secrets.
The orchestrator records provider usage and effects in the parent trace.

The initial implementation provides neither network nor provider broker ports.

## Secrets and identity-bearing material

Secret values are unavailable. When a future operation needs a secret, the script may request a
narrow broker operation through an opaque handle; the value is not returned to the script when a
signing or exchange operation can suffice.

Identity, memory, transcript, and relationship material enters only as explicit mounted objects or
typed values under current privacy policy. A script cannot search the house merely because the
resident can.

## Language policy

The first general-purpose language is Python. A language descriptor declares:

- interpreter version and digest;
- standard-library policy;
- allowed imports;
- package environment identity;
- startup flags;
- encoding;
- deterministic settings where available.

No package installation occurs during an invocation. Package environments are built and reviewed
separately. Native extensions, FFI, dynamic libraries, subprocesses, and runtime package download
are disabled in the initial profile.

Import allowlists are defense in depth, not the primary hostile-code boundary. A `local_process`
script that defeats an import filter remains limited only by the actual OS/backend guarantees.

## Resource limits

Every request has hard ceilings for:

- wall-clock seconds;
- CPU seconds when enforceable;
- resident memory;
- child processes;
- open files;
- files created;
- bytes written;
- stdout/stderr bytes;
- artifact count and size;
- input bytes;
- execution trace events.

Exceeding a limit terminates the process tree and returns `failed` or `partial` according to whether
harvestable outputs or broker effects already occurred. Truncation is explicit.

## Input and output protocol

Structured inputs are written to a runtime-owned file or pipe using a versioned JSON envelope. The
script writes one structured result envelope and may create declared output files.

Example:

```json
{
  "schema_version": "vestigia.script-input.v0.1",
  "execution_id": "exec_...",
  "arguments": {"name": "Jeff"},
  "mounts": [{"name": "notes", "path": "/input/notes", "read_only": true}]
}
```

The output protocol distinguishes:

- typed return value;
- private artifacts;
- resident-facing notes;
- warnings;
- requested follow-up actions.

A requested follow-up is data. It does not execute another capability.

## Execution states and effects

The orchestrator records:

```text
planned -> starting -> running -> collecting -> terminal
```

Terminal states are `succeeded`, `partial`, `failed`, `cancelled`, `not_run`, `expired`, or
`quarantined`.

Sandbox-local computation has `outward_effect: none`. Broker calls or imported host artifacts are
separate child receipts and may change the parent effect to `possible` or `confirmed`.

Exit code zero is not sufficient for success. The result envelope, output validation, resource
state, and declared contract must all pass.

## Cancellation and restart

Cancellation terminates the complete contained process tree, stops brokers, collects safe partial
metadata, and records what may already have happened. Cancellation does not delete prior receipts.

After Runtime restart:

- a `planned` execution may be safely abandoned or started according to policy;
- a `running` execution without a live contained process becomes `interrupted`;
- no script is blindly replayed;
- idempotent local-only scripts may be explicitly restarted from the same immutable inputs;
- broker or outward effects require reconciliation before retry.

## Static inspection

Before testing or activation, script inspection records:

- content hash and size;
- language and parse result;
- imports and requested packages;
- filesystem, network, process, environment, reflection, serialization, and dynamic-code signals;
- embedded URLs and secret-shaped strings;
- declared input/output contract;
- requested sandbox profile and grants;
- warnings and policy violations.

Static inspection does not prove safety. It explains why a script is or is not eligible for a
particular sandbox profile.

## Dynamic testing

Testing runs with synthetic inputs and stricter limits than activation. Tests cover:

- valid outputs;
- malformed inputs;
- timeout and memory pressure;
- output overflow;
- denied network and filesystem access;
- attempted environment and secret reads;
- subprocess attempts;
- cancellation;
- deterministic or intentionally nondeterministic behavior;
- receipt completeness;
- absence of host mutation.

Imported scripts require test receipts produced by the local Runtime. A sender's receipt is
provenance, not local authorization.

## Canonical say-hi script

```python
from __future__ import annotations

import json
import sys

payload = json.load(sys.stdin)
name = str(payload["arguments"].get("name", "friend"))[:80]
json.dump(
    {
        "schema_version": "vestigia.script-output.v0.1",
        "value": {"text": f"I made this machine make a machine say hi to {name}."},
        "artifacts": [],
        "warnings": [],
    },
    sys.stdout,
)
```

The acceptance version may be even smaller, but it must use the real input/output protocol and
produce a complete execution receipt.

## Contract tests

The sandbox framework must prove:

- no code runs during import, inspection, or approval;
- unsupported profiles fail closed;
- imported scripts cannot fall back from hardened to local-process isolation silently;
- environment and secrets are absent;
- undeclared files and outputs are rejected;
- path traversal, symlinks, special files, and output bombs are rejected;
- time, process, memory, and output limits terminate correctly where advertised;
- child processes cannot outlive cancellation where advertised;
- network denial is tested rather than assumed;
- scripts cannot call Runtime capabilities directly;
- requested follow-up actions remain inert data;
- restart does not blindly replay interrupted work;
- support bundles exclude source and input/output content by default;
- backend descriptors never claim guarantees the tests do not verify.

## Non-goals

The initial sandbox is not a proof of safety for arbitrary hostile Python, a package manager, a
network client, a root shell, an extension loader, an invisible autonomy loop, or an identity and
memory editor. General third-party code remains inert unless a hardened backend and explicit policy
approve it.
