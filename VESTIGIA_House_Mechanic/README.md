# VESTIGIA House Mechanic

Status: v0.1 development supervisor foundation.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It is deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic therefore exposes **named, operator-authored recipes**, not an arbitrary shell endpoint. Callers may select a known recipe; they may not provide command text, extra argv, a working directory, environment variables, credentials, or a network bind address at invocation time.

## This slice

The initial implementation provides:

- a strict JSON recipe manifest with unknown-field rejection;
- repository-relative cwd validation;
- immutable recipe SHA-256 digests;
- a bounded subprocess runner using `shell=False`;
- stripped environment profiles;
- wall-time and stdout/stderr ceilings;
- best-effort process-tree termination;
- receipts with recipe digest, timing, exit state, bounded output, and explicit containment truth;
- a small CLI for list/run inspection;
- cross-platform CI.

It deliberately does **not** yet provide a listener/API, service start/stop/restart, worktree leases, deployment rollback, MCP projection, credential management, or a self-update path.

## Authority boundary

Recipes are startup configuration. Invocation chooses only an exact recipe ID. There is no arbitrary-command lane.

The runner does not claim formal process-tree containment: receipts always report `process_tree_containment_proven: false`. On Windows, timeout/output overflow invokes `taskkill /T /F` as best-effort cleanup; on other platforms the direct child is killed.

## Example

See `recipes.example.json`.

From this directory:

```powershell
python -m pip install -e ".[dev]"
house-mechanic --manifest recipes.example.json --repo-root .. list
house-mechanic --manifest recipes.example.json --repo-root .. run runtime.compile
```

The compile recipes are illustrative named operations. This branch does not execute or deploy them on the operator host.

## Next bounded slice

The next step, after this foundation is reviewed and CI-green, is a fixed-loopback authenticated API plus typed service manifests. That should preserve the same rule: operator-authored recipes and manifests determine authority; callers select from them rather than supplying shell material.
