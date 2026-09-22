# VESTIGIA House Mechanic

Status: v0.2 development supervisor boundary.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It is deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic exposes **named, operator-authored recipes**, not an arbitrary shell endpoint. Callers may select a known recipe; they may not provide command text, extra argv, a working directory, environment variables, credentials, or a network bind address at invocation time.

## v0.2 slice

The current implementation provides:

- strict JSON recipe manifests with unknown-field rejection;
- repository-relative cwd validation;
- immutable recipe SHA-256 digests;
- bounded subprocess execution with `shell=False`;
- stripped environment profiles;
- wall-time and stdout/stderr ceilings;
- best-effort process-tree termination with explicit truth about containment;
- typed service manifests whose recipe references must resolve at startup;
- fixed-loopback authenticated HTTP on `127.0.0.1`;
- authenticated recipe/capability/service discovery;
- authenticated execution of one exact known recipe;
- caller-supplied request-ID correlation;
- a hard parallel-run ceiling;
- a small CLI for list/run/serve;
- Windows + Ubuntu CI.

This slice still does **not** grant service process authority. Service manifests are descriptive objects only: the API does not start, stop, restart, deploy, roll back, install software, change credentials, open arbitrary listeners, or self-modify.

## Authority boundary

Recipes and service manifests are startup configuration. Invocation chooses only an exact recipe ID. There is no arbitrary-command lane.

The API listener has no caller-selectable host. It always binds `127.0.0.1`. The bearer token must already exist on disk; House Mechanic does not create or rotate credentials in this slice.

The runner does not claim formal process-tree containment: receipts report `process_tree_containment_proven: false`. On Windows, timeout/output overflow invokes `taskkill /T /F` as best-effort cleanup; on other platforms the direct child is killed.

## CLI

From this directory:

```powershell
python -m pip install -e ".[dev]"

house-mechanic --manifest recipes.example.json --repo-root .. list
house-mechanic --manifest recipes.example.json --repo-root .. run runtime.compile
```

To start the v0.2 API, first provision a token file yourself, then:

```powershell
house-mechanic --manifest recipes.example.json --repo-root .. serve \
  --services services.example.json \
  --token-file C:\path\to\house-mechanic.token \
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
- `POST /v1/run` with exactly `{"recipe_id":"..."}`

The API deliberately rejects caller-supplied argv/cwd/environment material.

## Next bounded slice

After this listener/manifests slice is reviewed and CI-green:

1. add truthful read-only health probing for service manifests;
2. define durable execution/receipt storage;
3. add typed service lifecycle state and process ownership;
4. only then introduce bounded start/stop/restart for explicitly operator-authorized services.

Worktree leases, rollback, stable MCP projection, credential management, arbitrary commands, and supervisor self-update remain separate later milestones.
