# VESTIGIA House Mechanic

Status: v0.3 development supervisor observability boundary.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It remains deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic exposes **named, operator-authored recipes**, not an arbitrary shell endpoint. Callers may select known recipes and declared services; they may not supply command text, extra argv, a working directory, environment variables, credentials, or a network bind address at invocation time.

## Current v0.3 slice

The current implementation provides:

- strict JSON recipe manifests with unknown-field rejection;
- repository-relative cwd validation;
- immutable recipe SHA-256 digests;
- bounded subprocess execution with `shell=False`;
- stripped environment profiles;
- wall-time and stdout/stderr ceilings;
- best-effort process-tree termination with explicit truth about containment;
- execution evidence including PID, environment-profile name, Git commit/branch when available, dirty-state evidence, output byte counts, and output hashes;
- typed service manifests whose recipe references must resolve at startup;
- fixed-loopback authenticated HTTP on `127.0.0.1`;
- authenticated recipe/capability/service discovery;
- authenticated execution of one exact known recipe;
- truthful read-only HTTP health probing of declared loopback services;
- expected HTTP-status and optional protocol verification without redirects;
- append-only durable JSONL receipts for recipe runs and health observations;
- recent/inspect receipt APIs;
- caller-supplied request-ID correlation;
- a hard parallel-run ceiling;
- a small CLI for list/run/serve;
- Windows + Ubuntu CI.

This slice still does **not** grant service process authority. Service manifests remain descriptive objects: the API does not start, stop, restart, deploy, roll back, install software, change credentials, open arbitrary listeners, or self-modify.

## Authority boundary

Recipes and service manifests are startup configuration. Invocation chooses only an exact recipe or service ID. There is no arbitrary-command lane.

The API listener has no caller-selectable host. It always binds `127.0.0.1`. The bearer token must already exist on disk; House Mechanic does not create or rotate credentials in this slice.

Health probes are also restricted to manifest-validated loopback HTTP destinations and do not follow redirects.

The runner does not claim formal process-tree containment: receipts report `process_tree_containment_proven: false`. On Windows, timeout/output overflow invokes `taskkill /T /F` as best-effort cleanup; on other platforms the direct child is killed.

## Durable receipt policy

API-served recipe executions and service-health observations are appended to an operator-selected JSONL receipt file.

Recipe receipts preserve:

- request ID;
- recipe ID and digest;
- cwd and environment-profile identifier;
- Git commit/branch when available;
- dirty-state evidence;
- PID and timing;
- exit/timeout/output-limit state;
- output byte counts and SHA-256 hashes;
- bounded tail excerpts;
- process-tree termination facts.

The live recipe response still contains the runner's already-bounded stdout/stderr for immediate diagnosis.

The durable store does **not** persist the full raw output stream by default. It keeps hashes, sizes, and at most a 4096-byte tail excerpt of each stream, and marks that policy explicitly with `raw_full_output_persisted: false`.

## CLI

From this directory:

```powershell
python -m pip install -e ".[dev]"

house-mechanic --manifest recipes.example.json --repo-root .. list
house-mechanic --manifest recipes.example.json --repo-root .. run runtime.compile
```

To start the v0.3 API, first provision a bearer-token file yourself and choose a durable receipt path:

```powershell
house-mechanic --manifest recipes.example.json --repo-root .. serve \
  --services services.example.json \
  --token-file C:\path\to\house-mechanic.token \
  --receipt-file C:\path\to\house-mechanic-receipts.jsonl \
  --port 8770
```

The bind address remains fixed to loopback.

## HTTP surface

Unauthenticated:

- `GET /health`

Authenticated bearer token:

- `GET /v1/capabilities`
- `GET /v1/recipes`
- `GET /v1/services`
- `GET /v1/receipts` — most recent durable receipts
- `POST /v1/run` with exactly `{"recipe_id":"..."}`
- `POST /v1/health-check` with exactly `{"service_id":"..."}`
- `POST /v1/receipt` with exactly `{"receipt_id":"..."}`

The API deliberately rejects caller-supplied argv/cwd/environment material.

If a recipe finishes but durable receipt persistence fails afterward, the API reports `execution_occurred: true` and `receipt_persisted: false` rather than disguising the completed execution as an ordinary pre-execution failure.

## Next bounded slice

After this observability slice is reviewed and CI-green:

1. define typed service lifecycle state and explicit process ownership;
2. distinguish externally-running services from Mechanic-owned child processes;
3. add status/log inspection for owned processes;
4. only then introduce bounded start/stop/restart for explicitly operator-authorized services.

Worktree leases, rollback, stable MCP projection, credential management, arbitrary commands, and supervisor self-update remain separate later milestones.
