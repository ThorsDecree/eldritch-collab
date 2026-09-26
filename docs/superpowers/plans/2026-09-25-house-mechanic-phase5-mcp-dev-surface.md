# House Mechanic Phase 5 Stable MCP Dev Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose House Mechanic through the four stable MCP tools `dev.capabilities`, `dev.call`, `dev.process`, and `dev.logs`, with `dev.call` as the sole mutation surface and a wildcard-by-default deployment action filter.

**Architecture:** House Mechanic remains the authoritative host-side development supervisor and publishes machine-dispatch metadata in `GET /v1/capabilities`. VESTIGIA MCP adds one loopback-only `HouseMechanicClient` that validates protocol/routes, applies the deployment-scoped action filter, forwards canonical operation IDs without aliasing, and preserves a shared request ID across independent MCP and House Mechanic receipt layers.

**Tech Stack:** Python 3.11+, stdlib `http.client`, existing `mcp>=2,<3`, pytest 8+, existing House Mechanic JSON/HTTP contracts.

**Spec:** `docs/superpowers/specs/2026-09-25-house-mechanic-phase5-mcp-dev-surface-design.md`

## Global Constraints

- Stable MCP descriptor set is exactly `dev.capabilities`, `dev.call`, `dev.process`, and `dev.logs`.
- `dev.call` is the only MCP House Mechanic mutation surface.
- `dev.call.action` uses House Mechanic operation IDs verbatim; no MCP alias vocabulary.
- `VESTIGIA_MCP_DEV_ACTIONS` semantics: unset -> wildcard, `*` -> wildcard, explicit empty -> deny all, comma-separated names -> exact subset.
- Wildcard grants only currently advertised House Mechanic mutations accepted by the projection contract; it never grants arbitrary HTTP or shell authority.
- House Mechanic endpoint validation remains authoritative; MCP never rewrites a House Mechanic failure into success.
- MCP must re-read live House Mechanic capabilities before every `dev.call`.
- House Mechanic integration is loopback-only and bearer-token authenticated.
- MCP and House Mechanic receipts remain independent but share the same `mcp_req_<uuid>` request ID.
- No automatic retry of mutations; exactly-once behavior is not claimed.
- Python support remains `>=3.11`; tests must remain green on the repository's current cross-platform CI matrix.

## Review Focus

- An advertised operation with a syntactically valid but unsafe route (query string, fragment, traversal, non-`/v1/` path) must be excluded and never dispatched.
- An unset `VESTIGIA_MCP_DEV_ACTIONS` and an explicitly empty value must remain distinguishable, because they mean wildcard versus deny-all.
- A House Mechanic response with the wrong `request_id` must fail even when the HTTP status and JSON body otherwise look successful.
- A read-only operation advertised by House Mechanic must never become callable through `dev.call`, including under wildcard mode.
- A mutation that already occurred before House Mechanic/MCP receipt persistence fails must remain represented as an occurred action; no retry or false rollback claim may be introduced by the projection.

---

### Task 1: Make House Mechanic capabilities machine-dispatchable

**Files:**
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/api.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_api.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_task_api.py`
- Modify: `VESTIGIA_House_Mechanic/tests/test_deployment_api.py`
- Modify: `VESTIGIA_House_Mechanic/pyproject.toml`
- Modify: `VESTIGIA_House_Mechanic/src/house_mechanic/__init__.py`

**Interfaces:**
- Consumes: existing House Mechanic HTTP routes and endpoint validation.
- Produces: `GET /v1/capabilities -> {"protocol": str, "request_id": str, "operations": dict[str, OperationMetadata]}`, where every projectable entry includes `effect`, `mutation: bool`, `method`, `path`, and object `input_schema`.

- [ ] **Step 1: Add failing capability-contract tests for representative read and mutation operations**

Add assertions proving:

```python
ops["service.process_status"]["mutation"] is False
ops["service.process_status"]["method"] == "POST"
ops["service.process_status"]["path"] == "/v1/process-status"

ops["task.acquire"]["mutation"] is True
ops["task.acquire"]["method"] == "POST"
ops["task.acquire"]["path"] == "/v1/task-acquire"
ops["task.acquire"]["input_schema"]["type"] == "object"

ops["deployment.candidate"]["mutation"] is True
ops["deployment.candidate"]["path"] == "/v1/deploy-candidate"
ops["deployment.candidate"]["input_schema"]["additionalProperties"] is False
```

Also assert required-vs-optional fields for `deployment.candidate`, including optional nullable `expected_generation_id`.

- [ ] **Step 2: Run focused House Mechanic API tests and verify they fail**

Run:

```bash
cd VESTIGIA_House_Mechanic
python -m pytest tests/test_api.py tests/test_task_api.py tests/test_deployment_api.py -q
```

Expected: FAIL because capability entries do not yet expose the required dispatch metadata.

- [ ] **Step 3: Add a centralized operation metadata builder in `api.py`**

Create one function with this interface:

```python
def operation_capabilities(api: "HouseMechanicServer") -> dict[str, dict[str, Any]]:
    ...
```

Use canonical operation IDs already present in the server. Each projected operation gets fixed `method`, fixed `path`, explicit `mutation`, and an object `input_schema`. Keep current descriptive fields such as `effect`, `enabled`, authority requirements, and receipt properties.

Do not generate route metadata from caller input and do not introduce aliases.

- [ ] **Step 4: Make `GET /v1/capabilities` return the centralized operation map**

Replace the inline operation dictionary with `operation_capabilities(self.api)`.

Keep existing request-ID and protocol fields unchanged.

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd VESTIGIA_House_Mechanic
python -m pytest tests/test_api.py tests/test_task_api.py tests/test_deployment_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the full House Mechanic suite**

Run:

```bash
cd VESTIGIA_House_Mechanic
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Bump House Mechanic development version**

Update both version declarations from `0.10.0.dev0` to `0.11.0.dev0`. Increment the House Mechanic API protocol only if the implementation treats the enriched capability response as a protocol-breaking contract change; if so, update all protocol assertions in the same commit.

- [ ] **Step 8: Commit**

```bash
git add VESTIGIA_House_Mechanic
git commit -m "House Mechanic: publish machine-dispatch capability contracts"
```

### Task 2: Add MCP configuration and the loopback House Mechanic client

**Files:**
- Create: `VESTIGIA_MCP_Server/src/vestigia_mcp/house_mechanic.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/config.py`
- Create: `VESTIGIA_MCP_Server/tests/test_house_mechanic.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_config.py`

**Interfaces:**
- Consumes: House Mechanic `/health`, `/v1/capabilities`, and fixed routes advertised by Task 1.
- Produces:
  - `DevActionFilter(mode: Literal["wildcard", "deny_all", "exact"], actions: tuple[str, ...])`
  - `parse_dev_actions(raw: str | None) -> DevActionFilter`
  - `HouseMechanicClient(...)`
  - `HouseMechanicClient.status() -> dict[str, object]`
  - `HouseMechanicClient.capabilities(request_id: str | None = None) -> dict[str, Any]`
  - `HouseMechanicClient.projected_mutations(...) -> dict[str, dict[str, Any]]`
  - `HouseMechanicClient.call(action: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]`
  - read helpers for process status, health, process logs, recent receipts, and exact receipt inspection.

- [ ] **Step 1: Add failing config tests for all four `VESTIGIA_MCP_DEV_ACTIONS` modes**

Test environment parsing for:

```text
missing variable -> wildcard
"*"              -> wildcard
""               -> deny_all
"a,b,c"          -> exact ("a", "b", "c")
```

Also assert House Mechanic host normalization accepts `localhost` as loopback and rejects non-loopback hosts.

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_config.py -q
```

Expected: FAIL because House Mechanic settings/filter parsing do not exist.

- [ ] **Step 3: Add House Mechanic settings to `Settings` and exact unset/empty parsing**

Add:

```python
house_mechanic_enabled: bool
house_mechanic_host: str
house_mechanic_port: int
house_mechanic_token_path: Path | None
house_mechanic_timeout_seconds: int
house_mechanic_max_response_bytes: int
dev_actions: DevActionFilter
```

Use `os.environ.get("VESTIGIA_MCP_DEV_ACTIONS")` so absence and explicit empty remain distinct.

- [ ] **Step 4: Add failing client tests**

Build a local fixture HTTP server and cover:

- loopback-only host;
- readable/non-empty/bounded token;
- protocol match and mismatch;
- JSON response size ceiling;
- invalid JSON;
- transport failure;
- authenticated response request-ID mismatch;
- unknown action;
- wildcard projects mutations only;
- exact allowlist narrows mutations;
- deny-all projects none;
- unsafe advertised route is excluded;
- read-only advertised action is excluded under wildcard;
- denied action never causes an HTTP mutation request.

- [ ] **Step 5: Run client tests and verify failure**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_house_mechanic.py -q
```

Expected: FAIL because the client does not exist.

- [ ] **Step 6: Implement `house_mechanic.py`**

Define:

```python
class HouseMechanicClientError(RuntimeError):
    ...

@dataclass(frozen=True)
class DevActionFilter:
    mode: Literal["wildcard", "deny_all", "exact"]
    actions: tuple[str, ...]

def parse_dev_actions(raw: str | None) -> DevActionFilter:
    ...

class HouseMechanicClient:
    ...
```

Client constraints:

- normalize `localhost` to `127.0.0.1`; reject every other host;
- token file ceiling 4096 bytes;
- fixed configured host/port/token only;
- response byte ceiling from settings;
- preserve HTTP error status plus bounded House Mechanic error code/message;
- require exact expected House Mechanic protocol;
- validate advertised `method`, `path`, `mutation`, and object `input_schema`;
- safe path must begin `/v1/` and contain no scheme, authority, query, fragment, backslash traversal, or `..` path segment;
- `call` must refresh live capabilities before dispatch;
- arguments pass through unchanged;
- do not retry mutation requests.

- [ ] **Step 7: Run client + config tests**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_config.py tests/test_house_mechanic.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/config.py         VESTIGIA_MCP_Server/src/vestigia_mcp/house_mechanic.py         VESTIGIA_MCP_Server/tests/test_config.py         VESTIGIA_MCP_Server/tests/test_house_mechanic.py
git commit -m "MCP: add bounded House Mechanic client and dev action policy"
```

### Task 3: Project the four stable MCP dev tools

**Files:**
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py`
- Create: `VESTIGIA_MCP_Server/tests/test_dev_projection.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_policy.py`

**Interfaces:**
- Consumes: `Settings.house_mechanic_*`, `DevActionFilter`, and `HouseMechanicClient` from Task 2.
- Produces MCP tools:
  - `dev.capabilities() -> dict[str, object]`
  - `dev.call(action: str, arguments: dict[str, Any]) -> dict[str, object]`
  - `dev.process(service_id: str, include_health: bool = True) -> dict[str, object]`
  - `dev.logs(source: str, service_id: str | None = None, receipt_id: str | None = None, tail_bytes: int = 16_384, limit: int = 50) -> dict[str, object]`

- [ ] **Step 1: Add failing policy tests for the four stable capabilities**

Assert:

```text
dev.capabilities -> PERCEIVE / ALLOW
dev.process      -> PERCEIVE / ALLOW
dev.logs         -> PERCEIVE / ALLOW
dev.call         -> ACT / ALLOW
```

- [ ] **Step 2: Add failing projection integration tests**

Using a fixture House Mechanic HTTP server, cover:

1. `dev.capabilities` reports integration state, allowlist mode, advertised operations, and projected mutation schemas.
2. `dev.call` uses one generated `mcp_req_...` in the outgoing `X-Request-ID`, returned wrapper, and MCP audit lookup.
3. Canonical action string and `arguments` are forwarded unchanged.
4. `dev.call` refuses a read-only action even with wildcard mode.
5. Exact mode refuses an unlisted mutation before its route is hit.
6. A malformed advertised route is not projected and cannot be called.
7. House Mechanic HTTP errors remain tool failures with bounded status/code/message.
8. Wrong response request ID fails.
9. `dev.process` calls process-status and optional health only; it cannot mutate.
10. `dev.logs(source="process")` enforces the tail bound.
11. `dev.logs(source="receipts")` supports recent bounded receipts and exact receipt inspection.
12. Mutation result reporting preserves House Mechanic fields such as `action_occurred` and `receipt_persisted` unchanged.

- [ ] **Step 3: Run focused projection tests and verify failure**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_policy.py tests/test_dev_projection.py -q
```

Expected: FAIL because the dev capabilities/tools are absent.

- [ ] **Step 4: Register the four capabilities in `policy.py`**

Add the exact capability names/effects from the spec. Do not add per-operation MCP capabilities.

- [ ] **Step 5: Construct one `HouseMechanicClient` in `create_server`**

Use only `Settings` fields; do not accept tool-call host/port/token overrides.

Add `HouseMechanicClientError` to the bounded exceptions caught by `guarded` so failed dev calls get truthful MCP audit receipts.

- [ ] **Step 6: Implement `dev.capabilities`**

Return:

- configured/available/protocol evidence;
- action filter mode and exact actions only when mode is `exact`;
- raw validated operation metadata;
- projected mutations and their input schemas;
- bounded rejection reasons for non-projected operations.

No mutations occur.

- [ ] **Step 7: Implement `dev.call`**

Signature:

```python
def dev_call(action: str, arguments: dict[str, Any]) -> dict[str, object]:
    ...
```

Generate exactly one request ID, dispatch through `HouseMechanicClient.call`, pass that same ID to `guarded(..., request_id=request_id)`, and return:

```python
{
    "request_id": request_id,
    "action": action,
    "projection": {
        "allowed": True,
        "allowlist_mode": settings.dev_actions.mode,
    },
    "result": house_mechanic_response,
}
```

Do not retry.

- [ ] **Step 8: Implement `dev.process`**

The tool reads process status and, when `include_health=True`, health evidence. Keep lifecycle mutation absent from this surface.

- [ ] **Step 9: Implement `dev.logs`**

Validate `source` is exactly `process` or `receipts`.

Process mode requires `service_id`, refuses receipt-only inputs, and enforces `1 <= tail_bytes <= 16384`.

Receipts mode refuses `service_id`; with `receipt_id` inspect one receipt, otherwise return recent receipts with `1 <= limit <= 50`.

- [ ] **Step 10: Run focused projection tests**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_policy.py tests/test_dev_projection.py tests/test_house_mechanic.py tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 11: Run the full MCP suite**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 12: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py         VESTIGIA_MCP_Server/src/vestigia_mcp/server.py         VESTIGIA_MCP_Server/tests/test_policy.py         VESTIGIA_MCP_Server/tests/test_dev_projection.py
git commit -m "MCP: expose stable House Mechanic dev surface"
```

### Task 4: Prove the cross-layer request/receipt join and wildcard narrowing end to end

**Files:**
- Create: `VESTIGIA_MCP_Server/tests/test_dev_house_mechanic_integration.py`
- Modify only if a proven integration defect requires it: Task 1–3 implementation files.

**Interfaces:**
- Consumes: real `HouseMechanicServer` package code and MCP `create_server`.
- Produces: an integration fixture proving the stable MCP surface can drive an actual bounded House Mechanic mutation and correlate evidence.

- [ ] **Step 1: Add the end-to-end failing test**

Start a fixture House Mechanic server with a bounded recipe or tasking configuration, then an MCP server/client surface pointing at it.

The test must prove:

1. `dev.capabilities` discovers the real House Mechanic mutation schema.
2. Wildcard mode admits one chosen bounded mutation.
3. `dev.call` triggers that mutation.
4. The House Mechanic durable receipt contains the exact MCP request ID.
5. The MCP audit/Receipt Garden event can be found by the same request ID.
6. `dev.logs(source="receipts")` returns the House Mechanic evidence.
7. An exact allowlist excluding the action blocks it without changing the House Mechanic server.
8. Wildcard mode admits it again in a fresh server configuration.

Use a mutation whose fixture has no external side effects beyond the test workspace.

- [ ] **Step 2: Run the integration test and verify failure**

Run:

```bash
cd VESTIGIA_MCP_Server
python -m pytest tests/test_dev_house_mechanic_integration.py -q
```

Expected: FAIL until all cross-package wiring is complete.

- [ ] **Step 3: Make only the minimal integration fixes required**

Do not broaden the stable four-tool surface and do not add new authority. Fix only discrepancies exposed by the real cross-layer test.

- [ ] **Step 4: Run both package suites**

Run:

```bash
cd VESTIGIA_House_Mechanic
python -m pytest -q
cd ../VESTIGIA_MCP_Server
python -m pytest -q
```

Expected: both suites pass.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_House_Mechanic VESTIGIA_MCP_Server
git commit -m "Test Phase 5 MCP to House Mechanic receipt correlation"
```

### Task 5: Document and expose the operator configuration

**Files:**
- Modify: `VESTIGIA_MCP_Server/.env.example`
- Modify: `VESTIGIA_MCP_Server/README.md`
- Modify: `VESTIGIA_MCP_Server/pyproject.toml`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/__init__.py`
- Modify: `VESTIGIA_House_Mechanic/README.md`
- Modify: `07_Labs/active_experiments/House_Mechanic_Roadmap.md` only through the Archive staging/promotion workflow after the GitHub implementation is verified.

**Interfaces:**
- Consumes: final configuration names and tool contracts from Tasks 1–4.
- Produces: operator-facing setup instructions and version evidence.

- [ ] **Step 1: Update `.env.example`**

Document all exact variables:

```text
VESTIGIA_MCP_HOUSE_MECHANIC_ENABLED
VESTIGIA_MCP_HOUSE_MECHANIC_HOST
VESTIGIA_MCP_HOUSE_MECHANIC_PORT
VESTIGIA_MCP_HOUSE_MECHANIC_TOKEN_PATH
VESTIGIA_MCP_HOUSE_MECHANIC_TIMEOUT_SECONDS
VESTIGIA_MCP_HOUSE_MECHANIC_MAX_RESPONSE_BYTES
VESTIGIA_MCP_DEV_ACTIONS
```

Document the four dev-action semantics explicitly, including the intentionally permissive wildcard default.

- [ ] **Step 2: Update MCP README**

Add the four-tool stable surface, authority split, wildcard meaning, receipt join, and a minimal setup example. State clearly that `*` means all eligible House Mechanic mutations, not arbitrary host authority.

- [ ] **Step 3: Update House Mechanic README**

Document that `/v1/capabilities` is now a machine-dispatch contract for bounded projections while House Mechanic validation remains authoritative.

- [ ] **Step 4: Bump MCP development version**

Update both MCP version declarations from `0.9.0.dev0` to `0.10.0.dev0`.

- [ ] **Step 5: Run final verification**

Run:

```bash
cd VESTIGIA_House_Mechanic
python -m pytest -q
cd ../VESTIGIA_MCP_Server
python -m pytest -q
```

Expected: all tests pass in both packages.

- [ ] **Step 6: Commit documentation/version changes**

```bash
git add VESTIGIA_MCP_Server VESTIGIA_House_Mechanic
git commit -m "Document Phase 5 stable MCP dev projection"
```

- [ ] **Step 7: Update the canonical House Mechanic roadmap**

After CI is green and the implementation PR is opened, stage, inspect, and promote a roadmap update recording:

- #96 merged green as `aa1dc71d1caf249f3064d3adf5b50dd89d321df7`;
- the Phase 5 PR number/head commit;
- the four stable MCP tools;
- wildcard-by-default optional action filtering;
- next milestone: the bounded end-to-end house-building-the-house exercise.

## Final branch verification

- [ ] Run House Mechanic tests: `cd VESTIGIA_House_Mechanic && python -m pytest -q`
- [ ] Run MCP tests: `cd VESTIGIA_MCP_Server && python -m pytest -q`
- [ ] Confirm the diff contains no arbitrary shell/route/host authority.
- [ ] Confirm `dev.call` is the only new MCP mutation descriptor.
- [ ] Confirm read-only `dev.process` and `dev.logs` cannot dispatch mutations.
- [ ] Confirm wildcard mode still depends on House Mechanic `mutation=true` plus route validation.
- [ ] Confirm exact allowlist and explicit deny-all are tested.
- [ ] Confirm one integration test proves the shared request-ID join across both receipt layers.
- [ ] Open the implementation PR only after local/full CI-equivalent verification passes.
