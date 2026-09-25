# VESTIGIA House Mechanic

Status: v0.6 task-lease/worktree development boundary.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It remains deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic exposes named, operator-authored recipes and typed service lifecycle actions. It still does not expose arbitrary command text, caller-supplied argv/cwd/environment, credentials, or a caller-selectable bind address.

## Current v0.6 slice

v0.6 begins Phase 4A by adding durable development-task coordination without adding deployment authority:

- operator-configured repository manifests;
- exact base-commit pinning at task acquisition;
- one disposable Git worktree and feature branch per task;
- durable task records with restart suspension;
- holder + authority-generation mutation checks;
- lease expiry as an admission boundary, not a kill switch;
- explicit patch/verification iterations;
- local checkpoint commits even when verification fails;
- no pointless commit for no-change diagnostic iterations;
- consensual handoff with generation rotation;
- explicit interrupted-iteration recovery after supervisor restart;
- terminal task states separate from cleanup;
- cleanup that preserves the task branch and refuses dirty worktrees.

The holder/generation tuple protects against stale mutation authority. It is not claimed as cryptographic multi-principal authentication.

This slice keeps task coordination internal; authenticated HTTP task projection and durable task/iteration receipts remain the next bounded step.

## Prior v0.5 lifecycle slice

v0.5 adds the first bounded service-control surface on top of the v0.4 ownership model:

- `mechanic_child` versus `external` service ownership;
- supervisor-instance ownership proven by the retained child-process handle;
- generation IDs separate from OS PIDs;
- no PID adoption after supervisor restart;
- lifecycle recipes reserved from generic recipe execution;
- durable lifecycle receipts;
- bounded start for declared mechanic-owned children only;
- required pre-launch health check;
- refusal to launch if the declared health endpoint is already healthy;
- post-launch expected-status/protocol verification;
- exact-generation stop;
- restart composed from verified stop plus a new verified start;
- bounded process status and log-tail inspection;
- one lifecycle mutation at a time per supervisor instance.

The API protocol is `vestigia.house-mechanic-api.v0.4`.

## Lifecycle authority

Lifecycle mutation is exposed only for services declared:

```json
{
  "ownership": "mechanic_child"
}
```

External services remain observable only.

A mechanic-owned service must have a declared `start_recipe` and a health probe before remote start is allowed.

Recipes referenced as `start_recipe` or `stop_recipe` remain forbidden through generic `POST /v1/run`; lifecycle authority cannot be smuggled through the ordinary recipe lane.

The local operator CLI remains a separate explicit operator surface.

## Start contract

`POST /v1/process-start`

Payload:

```json
{"service_id":"example"}
```

Start succeeds as *verified* only when House Mechanic observes:

1. no currently running child owned by this supervisor;
2. the declared health endpoint is not already healthy before launch;
3. House Mechanic launches and retains the exact child process;
4. the expected health status/protocol becomes healthy within the bounded wait;
5. the same owned generation is still running.

This is stronger than checking health only after launch, because an endpoint that was healthy beforehand could belong to some unrelated process.

It is still not cryptographic generation binding. Receipts explicitly report:

```text
health_generation_bound = false
health_attribution = temporal_after_owned_launch
```

A future protocol may bind a health response directly to the generation.

If a child is launched but health never verifies, House Mechanic returns `started_unverified`. It does **not** silently kill the process and call that rollback. The action occurred, the exact generation remains inspectable, and an explicit exact-generation stop is required.

## Stop contract

`POST /v1/process-stop`

Payload:

```json
{
  "service_id":"example",
  "generation_id":"hm_proc_<32 lowercase hex characters>"
}
```

House Mechanic will only stop the generation currently retained as owned by this supervisor instance.

A stale, malformed, unknown, or different generation cannot authorize a stop.

Stop verification requires:

- the exact owned child reaches `exited`; and
- when a health probe exists, the endpoint becomes unhealthy/absent within the bounded wait.

The receipt distinguishes:

- `stopped`
- `process_exit_unverified`
- `process_stopped_endpoint_still_healthy`

The last case is important evidence that another process may still be answering at the declared endpoint.

## Restart contract

`POST /v1/process-restart`

Restart requires the exact current generation ID.

It is composed as:

```text
verified stop of generation A
        ->
bounded start of generation B
        ->
health verification for B
```

The new generation must differ from the old one.

If stop is unverified, restart does not proceed to start.

If stop succeeds but the new start is blocked, the receipt says `stopped_start_blocked` rather than pretending nothing changed.

## Durable lifecycle evidence

Lifecycle calls append `service_lifecycle` records to the same append-only JSONL receipt store used for recipe and health evidence.

Receipts include, as applicable:

- request ID;
- lifecycle action;
- service ID and manifest digest;
- start recipe ID and digest;
- old/new generation IDs;
- before/after process state;
- health preflight/final observations;
- exact-generation termination evidence;
- verification outcome;
- whether an action actually occurred.

If the lifecycle action occurs but durable receipt persistence fails afterward, the HTTP response preserves:

```text
action_occurred = true
receipt_persisted = false
```

Receipt loss is never represented as proof that the action did not happen.

## Process ownership limitations

A PID is not durable identity.

Ownership is scoped to one supervisor instance:

```text
ownership_scope = supervisor_instance
ownership_survives_supervisor_restart = false
```

House Mechanic never reconstructs kill authority from a remembered PID after restart.

Process-tree containment also remains explicitly unproven. Graceful termination targets the exact retained child; forced cleanup is best-effort and platform-specific.

## Authenticated HTTP surface

Read/observe:

- `GET /v1/capabilities`
- `GET /v1/recipes`
- `GET /v1/services`
- `GET /v1/receipts`
- `POST /v1/health-check`
- `POST /v1/receipt`
- `POST /v1/process-status`
- `POST /v1/process-logs`

Bounded execution:

- `POST /v1/run`

Owned lifecycle mutation:

- `POST /v1/process-start`
- `POST /v1/process-stop`
- `POST /v1/process-restart`

All lifecycle requests are bearer-authenticated and loopback-only.

## Serve options

The v0.5 server adds:

```text
--lifecycle-health-wait 10.0
--lifecycle-stop-timeout 5.0
```

Both are bounded at startup.

## Still out of scope

v0.6 still does not add:

- automatic rollback;
- durable process reattachment;
- arbitrary commands;
- system package installation;
- credential creation/rotation;
- non-loopback listeners;
- public deployment;
- MCP dev projection;
- supervisor self-update.

## Next bounded slice

After the v0.6 task/worktree foundation is green:

1. project task operations through the authenticated fixed-loopback API;
2. add durable task-transition and iteration-checkpoint receipts;
3. finish explicit renewal/budget-extension/base-refresh semantics;
4. add last-known-good deployment references and explicit rollback semantics;
5. expose a small stable MCP dev surface;
6. close the inspect -> patch -> test -> deploy -> verify loop without requiring Jeff to carry commands between systems.

🏮🔧
