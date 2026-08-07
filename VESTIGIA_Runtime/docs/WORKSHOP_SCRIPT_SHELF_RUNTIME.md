# Resident script shelf runtime v0.1

The resident script shelf gives a resident a private place to write and preserve executable Python
without turning source text into ambient execution authority.

The governing rule remains:

> Source existence is not callability. A resident script becomes callable only when the exact
> immutable version has current inspection, test, approval, and activation evidence.

## Resident lifecycle

```text
draft -> inspected -> tested -> approved -> active
received/imported -> inspected -> hardened-only until a hardened backend exists
any unsafe or inconsistent version -> quarantined
active old version + active replacement -> explicit supersede
```

Editing does not mutate a version. `draft` against an existing script ID creates the next integer
version, stores new content-addressed source bytes, and starts that version at `draft`. Prior test,
approval, and activation records do not transfer. An older active version remains active until the
resident explicitly disables or supersedes it.

Quarantine is sticky in v0.1. Static inspection alone does not clear it. Recovery uses a new
immutable version; a future explicit quarantine-review workflow may provide a receipted alternative.

## Capability

The resident-facing capability is `script.shelf` with these modes:

- `draft` — store resident-authored Python as an inert immutable version;
- `receive` — preserve supplied/imported source and provenance as inert evidence;
- `list` / `show` / `read_source` — inspect shelf metadata or explicitly read private source;
- `inspect` — parse and statically classify the exact source hash without executing it;
- `test` — execute an eligible resident-authored exact hash under stricter lifecycle control;
- `approve` — approve the exact tested hash and local-only effective grant;
- `activate` — bind the exact source, inspection ruleset, test, approval, backend guarantees, and
  interpreter environment into a callable activation;
- `run` — invoke only an active hash-bound version through the Workshop sandbox;
- `disable` / `supersede` / `quarantine` / `archive` — explicit lifecycle controls.

No mode infers approval from repetition, location, authorship, or prior successful runs.

## What the resident can write

A resident-authored draft may use the current `local.process` backend only after inspection marks it
`local_process_eligible`. The program receives the same versioned JSON input envelope as the
canonical acceptance script and must write one valid script-output envelope to stdout.

```python
import json
import sys

payload = json.load(sys.stdin)
name = str(payload["arguments"].get("name", "friend"))[:80]
json.dump(
    {
        "schema_version": "vestigia.script-output.v0.1",
        "value": {"text": f"hello {name}"},
        "artifacts": [],
        "warnings": [],
    },
    sys.stdout,
)
```

A normal resident workflow is:

```text
draft source
-> inspect exact hash
-> test with synthetic arguments
-> review the test receipt
-> approve the exact local-compute grant
-> activate that exact version
-> run with declared arguments
```

Testing is real sandbox execution. It is not activation, and a successful test does not silently
approve or activate the script.

## Static inspection boundary

The v0.1 inspector uses Python's AST without importing or executing the source. It records parse
status, imports, observed call names, complexity, URL hashes, secret-shaped literal hashes, and
bounded warnings. Literal URL or secret-shaped values are not copied into ordinary evidence.

Resident-authored source is upgraded to `hardened_only` when inspection sees signals such as:

- sockets, TLS, HTTP/URL stacks, subprocesses, multiprocessing, `ctypes`, or dynamic import;
- `eval`, `exec`, `compile`, `__import__`, process-spawn APIs, or similar dynamic execution;
- third-party imports outside the reviewed standard-library environment.

Filesystem APIs produce an explicit warning because the current local-process backend does not
enforce host-filesystem denial. Static inspection is explanatory defense in depth, not proof of
safety.

Received/imported source is always hardened-only in this slice regardless of how harmless it looks.
The current Runtime has no hardened backend, so such code can be stored and inspected but not tested,
approved, activated, or run.

## Input and output contracts

The shelf v0.1 uses `vestigia.value-contract.v0.1`, a deliberately bounded JSON-value contract
subset. It supports ordinary object/array/scalar type declarations, properties, required fields,
additional-property rejection, item contracts, enum/const, length/item ceilings, and numeric
minimum/maximum values.

This is **not** an implementation of full JSON Schema. Unsupported schema keywords fail validation
instead of being silently ignored.

Test and live-run arguments are validated before execution. The returned `value` is validated after
sandbox execution. A live active version that violates its declared output contract is quarantined
and loses callability.

## Provenance and immutable evidence

The shelf preserves distinct authorship lanes for who authored and supplied source. Source bytes are
stored privately under a content-addressed SHA-256 object. Ordinary list, card, receipt, and
Observatory views expose hashes and metadata rather than source text. `read_source` is the explicit
private source-reading action.

Every activation binds:

- script ID, version, and source hash;
- input/output contract hashes;
- inspection receipt and ruleset hash;
- successful local test receipt;
- approval and effective grant hash;
- backend ID/version and guarantee hash;
- interpreter environment identity;
- maximum wall time and optional expiry.

A backend, guarantee, interpreter, inspector-ruleset, or source mismatch fails closed and requires a
new review cycle.

## Authority and outputs

The approved v0.1 grant contains only local sandbox computation and private artifact creation.
Provider calls and outward actions remain zero. The script receives no Runtime object, provider
client, Discord client, database connection, identity editor, memory port, or credential store.

Sandbox output is labeled `script_generated` and private. It does not become resident-authored,
memory, identity, published material, or an outward message automatically. A requested follow-up is
returned as inert data with `follow_up_executed: false`.

## Local-process honesty

`local.process` remains an ordinary-bug containment backend, not hostile-code isolation. Environment,
wall-time, and output ceilings are enforced. Network denial, host-filesystem denial, process-tree
containment, CPU limits, memory limits, and hostile-code approval are not claimed.

The shelf therefore does not use social trust to make imported code executable and does not market
static inspection as a fortress.

## Operator ceilings

Existing `home.yaml` overlay keys constrain the underlying sandbox, including:

```yaml
workshop:
  max_script_source_bytes: 131072
  max_wall_seconds: 5
  max_input_bytes: 65536
  max_stdout_bytes: 65536
  max_stderr_bytes: 32768
  max_artifact_files: 16
  max_artifact_bytes: 1048576
```

The source ceiling is additionally hard-capped at 1 MiB and activation lifetime at 30 days when an
expiry is requested.

## Observatory

`house.observatory` gains a `script_shelf` panel containing lifecycle counts, recent active versions,
and the latest lifecycle event. It never includes source text. Imported-code execution is reported as
unavailable while no hardened backend exists.

## Non-goals

This slice is not a package manager, hostile-code sandbox, dependency installer, automatic update
channel, collaborative code editor, code-signing authority, proof of authorship, invisible autonomy
loop, memory editor, identity editor, or outward-action mechanism.
