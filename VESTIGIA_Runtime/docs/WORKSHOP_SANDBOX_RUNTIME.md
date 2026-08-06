# Workshop local-process sandbox v0.1

Status: initial runtime implementation of the second Workshop Within stage.

This slice gives a resident one real process-within-process acceptance path while keeping the
broader resident script shelf closed. It implements a truthful `local_process` backend for
resident-authored or locally reviewed code and ordinary-bug containment. It does **not** claim
hostile-code isolation.

The governing boundary remains:

> A resident may automate powers already granted to them. Automation may not manufacture,
> inherit, or conceal powers that were never granted.

## Resident-facing capability

The Runtime registers `workshop.sandbox` with four modes:

- `describe` — return the backend descriptor, guarantees, health, and callable acceptance script;
- `run_acceptance` — run the bundled immutable `vestigia.canonical.say-hi` script;
- `list` — list resident-scoped execution metadata without source or raw arguments;
- `inspect` — inspect one execution, its receipts, and private artifact metadata.

The capability does not accept inline or imported source. General script import remains inert until
the resident script shelf can represent immutable provenance, inspection, testing, approval,
activation, quarantine, disablement, and supersession.

## Honest backend descriptor

The backend advertises profile `local_process` and reports only guarantees enforced by this slice:

- environment stripping — enforced;
- wall-time ceiling — enforced;
- stdout and stderr ceilings — enforced;
- output-file count and byte ceilings — enforced;
- network denial — **not claimed**;
- host-filesystem denial — **not claimed**;
- process-tree containment — **not claimed**;
- memory and CPU ceilings — **not claimed**;
- hostile-code approval — **false**.

The process receives no Runtime object, provider client, Discord client, database connection,
credential, secret store, memory port, identity port, or outward doorway. That absence is useful,
but it is not represented as an OS security boundary the backend cannot prove.

## Process layout and invocation

Each run receives a fresh temporary working root:

```text
/input/   runtime-owned structured input
/output/  declared private output files
/tmp/     ephemeral scratch and process TEMP/TMP
```

Python starts through the current interpreter with isolated mode, site loading disabled, and
bytecode writing disabled. The environment contains only the small Python/runtime set required for
the invocation, plus platform variables that CPython or the operating system may insert.

No package installation, provider call, network broker, mount import, or outward action occurs.

## Structured input and output

Input is one UTF-8 JSON envelope:

```json
{
  "schema_version": "vestigia.script-input.v0.1",
  "execution_id": "workshop_exec_...",
  "arguments": {"name": "Jeff"},
  "mounts": []
}
```

Successful stdout must be exactly one UTF-8 JSON object using
`vestigia.script-output.v0.1`. A script may return a typed value, warnings, notes, inert requested
follow-ups, and declared file artifacts.

Every file under `/output` must be declared by normalized relative path and media type. The
collector rejects:

- undeclared files;
- declared files that were not created;
- traversal, absolute, alternate-stream, or non-normalized paths;
- duplicate declarations;
- symlinks and special files;
- file-count or total-byte overflow.

No unexpected file is substituted, ignored, or quietly adopted.

## Artifacts and transactional adoption

Validated outputs become private workshop artifacts. Bytes are written atomically to the private
workshop artifact area, then the execution, artifact rows, and workshop receipt are adopted in one
parent-first database transaction. If adoption fails, staged artifact files are removed.

Artifact results expose IDs, hashes, media types, sizes, kinds, and privacy state. They do not
expose host storage paths. Producing an artifact does not publish it, place it in workspace, adopt
it into memory or identity, make it executable, or authorize another action.

## Execution evidence

Each run records:

- immutable script identity, version, and source hash;
- input, plan, grant, output, and backend-guarantee hashes;
- resident and room hashes;
- backend/profile identity;
- status, timestamps, wall time, exit state, and bounded error category;
- trace events and private artifact references;
- one `sandbox.run` workshop receipt;
- `outward_effect: none`.

Ordinary list and Observatory views contain metadata only. Source and raw arguments are omitted.

Requested follow-ups are returned as inert data with `follow_up_executed: false`. The sandbox does
not dispatch another capability.

## Failure behavior

The backend fails closed for:

- unavailable or disabled local-process execution;
- oversized input;
- timeout;
- stdout or stderr overflow;
- nonzero process exit;
- malformed or non-UTF-8 result envelopes;
- invalid artifact declarations;
- missing, undeclared, unsafe, or excessive output files;
- failed transactional adoption.

Timeout and output-limit failures terminate the child. On platforms where complete descendant-tree
termination is not independently enforced and tested, the descriptor continues to report
`process_tree_contained: false`.

## Canonical acceptance run

The bundled immutable script reads the input envelope and returns:

```text
I made this machine make a machine say hi to <name>.
```

The returned value is collected as a private JSON workshop artifact with a complete execution trace
and receipt. No network, provider, Discord, memory, identity, publication, or outward authority is
involved.

## Operator ceilings

Operators may narrow the built-in ceilings through the existing `home.yaml` overlay mechanism:

```yaml
workshop:
  enabled: true
  local_process_enabled: true
  max_wall_seconds: 5
  max_input_bytes: 65536
  max_stdout_bytes: 65536
  max_stderr_bytes: 32768
  max_artifact_files: 16
  max_artifact_bytes: 1048576
```

Implementation hard ceilings remain in force even when larger values are requested.

## Deliberate next seam

The next Workshop slice is the resident script shelf:

1. immutable source import and provenance;
2. static inspection without execution;
3. synthetic dynamic testing under stricter limits;
4. explicit resident/operator grant intersection;
5. approval, activation, quarantine, disablement, and supersession;
6. invocation only by immutable approved version and content hash.

Until that shelf exists, arbitrary resident/imported source remains unavailable through the
resident-facing capability.
