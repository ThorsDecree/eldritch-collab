# House Mechanic Phase 5 — Stable MCP Dev Surface Design

Date: 2026-09-25
Status: design for review
Repository: `ThorsDecree/eldritch-collab`

## Purpose

Phase 5 exposes the already-bounded House Mechanic development loop through a small, stable MCP descriptor set so callers do not need a new MCP tool every time House Mechanic gains an internal recipe, task action, deployment action, or lifecycle action.

The intended operator outcome is unchanged:

> Jeff should not have to act as a human command runner for ordinary bounded development work.

The stable public MCP surface is:

- `dev.capabilities` — read-only discovery of the live House Mechanic contract and the MCP projection;
- `dev.call` — the **only MCP mutation surface** for House Mechanic;
- `dev.process` — read-only process/service/health observation;
- `dev.logs` — read-only bounded process logs and House Mechanic evidence retrieval.

The design deliberately does not add arbitrary shell authority, caller-defined argv/cwd/environment, production deployment, process reattachment, supervisor self-modification, or new host authority. Phase 5 projects existing House Mechanic authority; it does not broaden it.

## Design principles

1. **One mutation surface.** All House Mechanic mutations cross MCP through `dev.call`.
2. **Canonical names stay canonical.** `dev.call.action` uses House Mechanic's advertised operation IDs verbatim.
3. **House Mechanic owns the operation ontology.** MCP filters and relays it rather than maintaining aliases.
4. **Live contract before mutation.** Every `dev.call` re-reads House Mechanic capabilities before dispatch so stale catalog state cannot grant an action.
5. **Default-simple deployment policy.** The dev action filter defaults to wildcard allow-all over already-bounded House Mechanic mutations.
6. **Optional narrowing remains available.** Operators can deny all or allow an exact subset without changing House Mechanic.
7. **Observation is not authority.** `dev.process` and `dev.logs` never mutate process, task, deployment, or service state.
8. **Receipts correlate; they do not collapse layers.** MCP and House Mechanic preserve independent evidence with a shared request ID.
9. **Fail closed on contract uncertainty.** Missing token, protocol mismatch, malformed advertised routes, request-ID mismatch, or non-mutating actions sent to `dev.call` are refused.

## Architecture

```text
ChatGPT / resident / MCP caller
              |
              v
      stable MCP dev surface
   +-------------------------+
   | dev.capabilities        |
   | dev.call                |
   | dev.process             |
   | dev.logs                |
   +-------------------------+
              |
              v
      HouseMechanicClient
  authenticated loopback only
              |
              v
      House Mechanic API
   live typed capability map
              |
   +----------+----------+
   | tasks / worktrees    |
   | recipes / tests      |
   | lifecycle / health   |
   | deployments / LKG    |
   | receipts / cleanup   |
   +----------------------+
```

The MCP server remains an authority broker and projection layer. House Mechanic remains the host-side development supervisor.

## House Mechanic capability contract

Phase 5 extends `GET /v1/capabilities` so each operation intended for machine projection carries enough metadata for a generic client to dispatch it safely.

Each operation entry includes:

```json
{
  "effect": "owned_service_deployment",
  "mutation": true,
  "method": "POST",
  "path": "/v1/deploy-candidate",
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "service_id",
      "task_id",
      "holder_id",
      "authority_generation"
    ],
    "properties": {
      "service_id": {"type": "string"},
      "task_id": {"type": "string"},
      "holder_id": {"type": "string"},
      "authority_generation": {"type": "integer"},
      "expected_generation_id": {
        "type": ["string", "null"]
      }
    }
  }
}
```

### Required metadata

Projected operations must provide:

- `effect` — existing descriptive House Mechanic effect classification;
- `mutation` — explicit boolean;
- `method` — currently `GET` or `POST`;
- `path` — fixed local API path beginning with `/v1/`;
- `input_schema` — JSON-schema-shaped input contract for caller arguments.

The schema is descriptive for MCP callers; House Mechanic's existing endpoint validation remains authoritative. MCP must not reinterpret a House Mechanic rejection into success.

### Route safety

The MCP client accepts only:

- the configured loopback House Mechanic host;
- advertised methods from a fixed supported set;
- advertised paths beginning with `/v1/`;
- paths without a scheme, authority, fragment, query-string injection, or traversal semantics.

An advertised malformed route makes that operation ineligible for projection.

## Canonical operation IDs

MCP does not rename House Mechanic actions.

Examples include:

```text
recipe.run
service.start
service.stop
service.restart
task.acquire
task.resume
task.recover
task.renew
task.extend_budget
task.refresh_base
task.abort_refresh
iteration.begin
iteration.checkpoint
handoff.offer
handoff.respond
task.finish
task.cleanup
deployment.candidate
deployment.promote
deployment.rollback
deployment.cleanup_retry
```

Read-only operations remain unavailable through `dev.call`, even if House Mechanic advertises them. They are projected through the read surfaces.

## Deployment-scoped mutation filter

New MCP setting:

```text
VESTIGIA_MCP_DEV_ACTIONS
```

Semantics:

```text
variable unset        -> wildcard allow-all
variable = "*"        -> wildcard allow-all
variable = ""         -> deny all mutations
variable = "a,b,c"    -> allow exactly a,b,c
```

Whitespace is stripped and explicit action names are normalized only for surrounding whitespace, not renamed or case-folded into a separate vocabulary. House Mechanic operation IDs are expected to use their canonical lowercase form.

### Meaning of wildcard

`*` means:

> all **mutation operations currently advertised by the configured House Mechanic instance and accepted by the MCP projection contract**.

It does **not** mean:

- arbitrary HTTP routes;
- arbitrary shell commands;
- arbitrary host filesystem access;
- caller-supplied argv/cwd/environment;
- unconfigured repositories or services;
- bypass of House Mechanic leases, generations, health checks, or deployment rules.

An explicit exact-name allowlist is available for shared or compartmentalized deployments.

## HouseMechanicClient

Add a dedicated MCP adapter, analogous in role to the existing Daemon-Bridge client.

Responsibilities:

- require loopback host;
- read the House Mechanic bearer token from the configured token path;
- enforce request/response byte ceilings;
- call `/health` and `/v1/capabilities`;
- verify the expected House Mechanic protocol;
- attach the MCP-generated `X-Request-ID`;
- require the returned `request_id` to match for authenticated calls;
- expose typed helpers for capabilities, mutation dispatch, process status, health, logs, recent receipts, and receipt inspection;
- convert transport/protocol failures into one bounded `HouseMechanicClientError` class for the MCP guard layer.

The client never accepts a caller-supplied host, port, token path, HTTP method, or route.

## MCP tools

### `dev.capabilities`

Read-only.

No mutation authority is exercised.

Returns:

- whether House Mechanic integration is configured;
- availability and protocol compatibility;
- House Mechanic protocol/version evidence;
- current dev-action filter mode:
  - `wildcard`,
  - `deny_all`, or
  - `exact`;
- the exact configured action names when mode is `exact`;
- current House Mechanic operation metadata;
- projected mutation actions after applying:
  1. protocol validation,
  2. mutation flag,
  3. route safety,
  4. deployment allowlist;
- input schema for each projected mutation action;
- explicit reasons for advertised operations that are not projected when useful for diagnosis.

This tool is the caller's source of truth for what `dev.call` may currently attempt.

### `dev.call`

The sole mutation tool.

Input:

```json
{
  "action": "deployment.candidate",
  "arguments": {}
}
```

Contract:

1. MCP creates one `mcp_req_<uuid>` request ID.
2. MCP fetches live House Mechanic capabilities.
3. The named action must exist.
4. The action must advertise `mutation=true`.
5. Its method/path must satisfy the projection contract.
6. The deployment action filter must allow it.
7. `arguments` must be an object.
8. MCP sends the arguments unchanged to the advertised bounded House Mechanic route, with the shared request ID.
9. House Mechanic performs its own authoritative validation and mutation.
10. MCP requires the response request ID to match.
11. MCP writes its own audit event using the same request ID.

Response shape:

```json
{
  "request_id": "mcp_req_...",
  "action": "deployment.candidate",
  "projection": {
    "allowed": true,
    "allowlist_mode": "wildcard"
  },
  "result": {}
}
```

The `result` object is House Mechanic's response body, not a rewritten success model.

### `dev.process`

Read-only service/process observation.

Input:

```json
{
  "service_id": "runtime-dev",
  "include_health": true
}
```

Returns bounded evidence from:

- House Mechanic process status;
- declared service ownership;
- current supervisor-instance process ownership;
- generation ID when present;
- health observation when requested.

It cannot start, stop, restart, deploy, adopt, or reconcile a process.

### `dev.logs`

Read-only bounded development evidence.

One stable tool supports two explicit modes:

```text
source = "process"
source = "receipts"
```

#### Process mode

Inputs:

- `service_id`;
- bounded `tail_bytes`.

Returns House Mechanic's bounded process-log projection for that service.

#### Receipts mode

Inputs:

- optional `receipt_id`;
- bounded `limit`.

Behavior:

- with `receipt_id`: inspect one exact House Mechanic receipt;
- without it: return recent House Mechanic receipts up to the bounded limit.

The tool does not mutate receipts or process state.

This mode exists so a caller can inspect House Mechanic's own evidence layer without adding a fifth stable MCP descriptor.

## Request and receipt correlation

For every `dev.call`:

```text
MCP audit event
     |
     | request_id = mcp_req_...
     v
House Mechanic API request
     |
     | X-Request-ID = same value
     v
House Mechanic durable receipt(s)
```

The shared request ID is a join key only.

The MCP audit receipt does not prove the House Mechanic receipt, and the House Mechanic receipt does not prove the MCP audit event. Either layer may truthfully report its own persistence failure.

Read operations also receive request IDs when the House Mechanic route supports them, preserving correlation without pretending observation is mutation authority.

## Configuration

Add MCP settings:

```text
VESTIGIA_MCP_HOUSE_MECHANIC_ENABLED
VESTIGIA_MCP_HOUSE_MECHANIC_HOST
VESTIGIA_MCP_HOUSE_MECHANIC_PORT
VESTIGIA_MCP_HOUSE_MECHANIC_TOKEN_PATH
VESTIGIA_MCP_HOUSE_MECHANIC_TIMEOUT_SECONDS
VESTIGIA_MCP_HOUSE_MECHANIC_MAX_RESPONSE_BYTES
VESTIGIA_MCP_DEV_ACTIONS
```

Defaults:

- enabled: false unless explicitly configured/enabled;
- host: `127.0.0.1` only;
- timeout: bounded operator default;
- response ceiling: bounded operator default;
- dev actions: wildcard allow-all when the variable is absent.

The implementation must distinguish an **unset** `VESTIGIA_MCP_DEV_ACTIONS` from an explicitly present-but-empty value.

Enabling integration still requires a readable token and reachable compatible House Mechanic. Wildcard action policy does not make an unavailable integration available.

## MCP policy entries

Add stable MCP capabilities:

```text
dev.capabilities   PERCEIVE / allow
dev.process        PERCEIVE / allow
dev.logs           PERCEIVE / allow
dev.call           ACT / allow
```

The MCP policy engine remains an independent outer authority layer. The dev-action filter is an additional deployment-scoped projection rule, not a replacement for MCP policy.

## Error behavior

The dev surface fails closed when:

- integration is disabled;
- token path is absent, unreadable, empty, or oversized;
- House Mechanic is unreachable;
- protocol is missing or incompatible;
- capabilities response is malformed;
- action is unknown;
- action is not marked as a mutation;
- action is denied by the dev-action filter;
- advertised method/path is outside the accepted projection contract;
- response exceeds byte ceilings;
- response is not valid JSON;
- authenticated response request ID does not match;
- House Mechanic itself rejects the request.

House Mechanic HTTP failures are surfaced as bounded errors preserving the status and House Mechanic error code/message when present. MCP does not retry a mutation automatically, because exactly-once execution is not proven.

## Compatibility and evolution

The stable MCP descriptor set is intended not to grow every time House Mechanic grows.

Adding a future House Mechanic mutation should normally require:

1. defining it in House Mechanic's capabilities map with a safe fixed route and input schema;
2. implementing and testing its House Mechanic endpoint;
3. no MCP tool-catalog change;
4. optional deployment allowlist change only when the operator uses exact mode.

If the future operation needs a new authority class that the current generic projection cannot safely represent, it must fail projection until this design is explicitly revised.

## Testing strategy

### House Mechanic tests

Add contract tests that every projected mutation:

- has `mutation=true`;
- advertises a supported method;
- advertises a fixed `/v1/` path;
- has an object `input_schema`;
- marks required and optional fields correctly;
- continues to enforce existing endpoint validation.

Add regression tests preventing read-only actions from accidentally being marked as mutations.

### MCP adapter tests

Test:

- loopback-only host validation;
- token size/readability;
- protocol match/mismatch;
- response byte ceiling;
- invalid JSON;
- request-ID mismatch;
- transport failure;
- safe/unsafe advertised routes;
- capabilities parsing.

### Allowlist tests

Test all four semantics:

- variable unset -> wildcard;
- `*` -> wildcard;
- explicit empty string -> deny all;
- comma-separated names -> exact subset.

Also verify:

- wildcard does not project read-only operations;
- exact mode cannot invent an operation absent from House Mechanic;
- denied action never reaches the HTTP mutation route.

### MCP tool tests

Test:

- `dev.capabilities` projects current mutation schemas;
- `dev.call` forwards canonical action names and arguments unchanged;
- one request ID joins MCP and House Mechanic evidence;
- `dev.call` refuses read-only actions;
- `dev.call` refuses malformed advertised routes;
- `dev.process` is observational only;
- `dev.logs` process mode is byte-bounded;
- `dev.logs` receipt mode is count-bounded;
- House Mechanic errors remain failures rather than being normalized into successful MCP results.

### End-to-end Phase 5 acceptance

With a fixture House Mechanic and MCP server:

1. discover a task/deployment mutation through `dev.capabilities`;
2. call it through `dev.call`;
3. inspect resulting process state with `dev.process`;
4. inspect correlated House Mechanic evidence with `dev.logs`;
5. inspect the MCP audit receipt by shared request ID;
6. prove a narrowed allowlist blocks the same mutation without changing House Mechanic;
7. prove wildcard mode admits it again.

## Out of scope

Phase 5 does not add:

- automatic PR merge;
- automatic public release;
- production deployment;
- supervisor self-update;
- arbitrary shell/argv/cwd/env;
- durable process reattachment;
- credential creation or rotation;
- caller-defined HTTP routes;
- a second MCP-side action alias vocabulary;
- automatic retry of mutations;
- new authority over Archive writes.

## After Phase 5

Once this stable surface is implemented and green, the next milestone is an end-to-end bounded self-maintenance exercise:

```text
observe defect
  -> inspect evidence/source
  -> acquire task/worktree
  -> reproduce
  -> patch
  -> compile/test
  -> inspect failure
  -> iterate within budget
  -> deploy candidate
  -> verify health/version/capabilities
  -> rollback if unhealthy
  -> promote if healthy
  -> inspect receipts/live behavior
  -> open/update PR or stop at an operator boundary
```

Success means Jeff no longer needs to carry ordinary commands between the MCP-facing resident and House Mechanic during that loop.
