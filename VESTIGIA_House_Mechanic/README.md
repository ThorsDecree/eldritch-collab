# VESTIGIA House Mechanic

Status: v0.10 restart reconciliation and disposable-checkout cleanup evidence boundary.

House Mechanic is the small host-side execution plane for VESTIGIA development work. It remains deliberately separate from the MCP Server and Runtime Workshop.

The governing rule is:

> Development autonomy may expand faster than consequential authority.

House Mechanic exposes named, operator-authored recipes and typed service lifecycle actions. It still does not expose arbitrary command text, caller-supplied argv/cwd/environment, credentials, or a caller-selectable bind address.

## Current v0.10 slice

v0.10 hardens the Phase 4B failure/restart seam without granting process reattachment authority:

- restart suspension now records the prior active deployment state and suspension timestamp;
- reconciliation inspection observes current supervisor ownership, declared health, and active checkout provenance without adopting a remembered PID/process generation;
- a healthy endpoint after restart is reported as possible pre-restart/external service presence, not proof of ownership;
- an unhealthy endpoint after restart is not treated as proof that the old process is dead or that its checkout is safe to remove;
- active suspended deployment checkouts are never cleaned automatically;
- failed cleanup after a verified stop, observed owned-process exit, or pre-launch failure is persisted as retryable cleanup evidence;
- each pending cleanup records deployment ID, exact expected commit, path, safety basis, attempts, and last error;
- cleanup retries refuse paths outside the deployment root, refuse dirty checkouts, refuse commit mismatches, and preserve the currently active checkout;
- missing already-retired paths are pruned conservatively rather than treated as fatal;
- reconciliation and cleanup retry are projected through the authenticated API and leave durable `dev_deployment` receipts.

The restart rule remains intentionally strict: durable deployment metadata survives; process-control authority does not.

## Prior v0.9 deployment slice

v0.9 begins Phase 4B by connecting clean task commits to designated mechanic-owned development services without exposing arbitrary deployment paths or commands:

- service schema v0.3 adds an operator-owned deployment binding to one configured repository;
- candidate source admission requires the current task holder/generation, a live lease, no open iteration, the matching repository, and a clean checkpointed worktree;
- candidate execution is materialized into a detached disposable deployment worktree at the exact task commit, so the running service is not coupled to a mutable task checkout;
- a running service must be stopped by exact owned generation before replacement;
- candidate start still requires health transition verification;
- a verified candidate can be explicitly promoted to last-known-good only while the exact generation is still running and healthy;
- unhealthy candidates are stopped and, when a last-known-good commit exists, House Mechanic automatically attempts a verified rollback;
- explicit rollback can also relaunch the exact last-known-good commit;
- deployment state is durable, but process authority is not reconstructed after supervisor restart; active deployment records become `suspended_unverified` and require operator reconciliation;
- deployment actions append durable `dev_deployment` receipts;
- authenticated deployment status/candidate/promote/rollback routes are exposed through the fixed loopback API.

Candidate deployment does not push, merge, publish, modify the supervisor, or auto-resolve Git conflicts.

## Prior v0.8 coordination slice

v0.8 closes the remaining Phase 4A coordination seams before deployment work:

- explicit lease renewal that must move expiry forward;
- explicit iteration-budget extension within the configured ceiling;
- paused-budget tasks reactivate only after an admitted extension;
- exact original acquisition base is preserved as historical provenance;
- `current_base_commit` tracks the presently integrated base separately;
- explicit base refresh resolves only operator-allowed refs;
- refresh refuses dirty or open-iteration worktrees;
- clean refresh uses a bounded Git rebase and rotates authority generation;
- conflicts are preserved as `blocked_rebase_conflict`; House Mechanic never auto-resolves them;
- explicit refresh abort restores the prior task branch and rotates authority generation;
- paused-budget and blocked-refresh authority generations are invalidated across supervisor restart;
- renewal, budget changes, refresh, and abort are projected through the authenticated API and durable task-transition receipts.

No automatic upstream following is added: base movement remains an explicit task operation.

## Prior v0.7 task API/evidence slice

v0.7 projects the Phase 4A task foundation through the authenticated fixed-loopback API and adds durable task evidence:

- authenticated task listing and inspection;
- bounded task acquisition from operator-configured repositories;
- resume and interrupted-iteration recovery;
- iteration begin/checkpoint;
- consensual handoff offer/respond;
- terminal disposition and explicit cleanup;
- durable `dev_task_transition` receipts;
- durable `dev_iteration_checkpoint` receipts;
- tasking fails closed unless repository/worktree configuration is present;
- task capabilities truthfully report whether tasking is enabled.

Task mutation still requires the current holder and authority generation. That tuple is stale-authority protection inside the already-authenticated local channel, not cryptographic resident identity.

## Prior v0.6 task/worktree slice

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

v0.10 still does not add:

- durable process reattachment;
- arbitrary commands;
- system package installation;
- credential creation/rotation;
- non-loopback listeners;
- public deployment;
- MCP dev projection;
- supervisor self-update.

## Next bounded slice

After the v0.10 reconciliation/cleanup surface is green:

1. expose the small stable MCP dev surface;
2. project task + deployment operations behind that stable descriptor set;
3. exercise the complete inspect -> patch -> test -> deploy -> verify -> rollback/promote loop end-to-end without requiring Jeff to carry commands between systems;
4. keep operator boundaries explicit for restart reconciliation that would require asserting an unowned process is truly gone.

🏮🔧
