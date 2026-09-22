# VESTIGIA House Mechanic

Status: v0.4 process-ownership development boundary.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It remains deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic exposes **named, operator-authored recipes**, not an arbitrary shell endpoint. Callers may select known recipes and declared services; they may not supply command text, extra argv, a working directory, environment variables, credentials, or a network bind address at invocation time.

## Current v0.4 slice

The current implementation includes the v0.3 observability surface plus explicit process-ownership semantics:

- strict named-recipe manifests and bounded subprocess execution;
- fixed-loopback authenticated API;
- loopback service health probing;
- durable append-only run/health receipts;
- typed service ownership: `external` or `mechanic_child`;
- legacy v0.1 service manifests remain readable and default to `external`;
- filename-safe service IDs before IDs are used in process-log paths;
- supervisor-instance process ownership backed by the exact retained `Popen` handle;
- process generation IDs separate from OS PIDs;
- read-only process status and bounded stdout/stderr tail inspection;
- truthful `running`, `exited`, `not_started`, and `external_unowned` states;
- no automatic process adoption after supervisor restart;
- graceful supervisor shutdown terminates only children actually owned by that supervisor instance.

The HTTP API still exposes **no lifecycle mutation**. There is no remote service start, stop, or restart route in v0.4.

## Why ownership is instance-scoped

A PID is not proof of identity.

If House Mechanic crashes or restarts, a new supervisor instance does not reconstruct process ownership from old PID state. A stale PID could later refer to an unrelated process. Therefore v0.4 reports:

```text
ownership_scope = supervisor_instance
ownership_survives_supervisor_restart = false
```

A `mechanic_child` service with no process launched by the current supervisor reports `not_started`, even if some externally-running process happens to resemble the declared service.

This is intentionally conservative. Durable reattachment, if ever implemented, requires stronger process identity evidence than PID alone.

## Service manifest v0.2

Services declare one of:

- `ownership: "external"` — observable, never claimed as a House Mechanic child.
- `ownership: "mechanic_child"` — eligible for future bounded lifecycle authority.

External services in v0.2 may not declare start/stop recipes. That prevents a manifest from simultaneously saying "I do not own this process" and "here is how I control it."

Legacy `vestigia.house-mechanic-services.v0.1` manifests remain accepted for compatibility and are interpreted as external/unowned.

## Process inspection

Authenticated read-only calls:

- `POST /v1/process-status` with exactly `{"service_id":"..."}`
- `POST /v1/process-logs` with exactly `{"service_id":"..."}`

Process logs expose bounded tails only (16 KiB per stream) plus byte counts and hashes. They do not create lifecycle authority.

Internally, v0.4 includes the process-launch primitive needed to test real ownership semantics, but there is deliberately no HTTP route that invokes it yet.

## Existing authenticated API

- `GET /v1/capabilities`
- `GET /v1/recipes`
- `GET /v1/services`
- `GET /v1/receipts`
- `POST /v1/run`
- `POST /v1/health-check`
- `POST /v1/receipt`
- `POST /v1/process-status`
- `POST /v1/process-logs`

The API protocol is `vestigia.house-mechanic-api.v0.3`.

## Next bounded slice

After v0.4 is reviewed and green:

1. persist lifecycle action receipts;
2. expose bounded `start` only for `mechanic_child` services whose declared start recipe is known;
3. verify the launched generation through configured health checks before calling start successful;
4. expose bounded stop for the exact current generation only;
5. compose restart from verified stop + verified start;
6. retain truthful failure states and never adopt unknown processes.

Rollback, worktree leases, stable MCP projection, credentials, arbitrary commands, public deployment, and supervisor self-update remain separate later milestones.
