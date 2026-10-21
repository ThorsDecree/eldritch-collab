# Lanternslide, Native Providers, and Recall Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile in-progress Lanternslide scans, add native Claude/Gemini text providers, and give residents bounded control over Runtime-memory retrieval volume.

**Architecture:** Keep these as three sequential, separately testable tasks. Lanternslide reconciles its persisted queue in place. Provider adapters implement the existing provider port and leave tool execution with the Runtime. Recall controls extend the existing context-control record and context receipt rather than creating a second memory authority.

**Tech Stack:** Python 3.11+, pytest, existing `urllib`/provider contracts, SQLite-backed resident controls, ArchiveSource and Lanternslide service.

**Spec:** `docs/superpowers/specs/2026-10-21-roadmap-build-slices.md`

## Global Constraints

- Preserve OpenAI as the default provider and preserve its current behavior.
- Provider calls remain text-only and use the Runtime's existing house-owned action-envelope loop.
- No automatic provider fallback/retry, native provider tool execution, or new outward authority.
- Resident retrieval settings remain bounded by operator ceilings and affect future turns only.
- Lanternslide retains its active scan ID and completed work where candidate paths still exist.

## Review Focus

- Mixed file addition/removal during an incomplete scan: test that scan ID and retained progress survive, stale outputs disappear, and new paths are eventually indexed.
- A renamed pending image: test that it is treated as a removed old path plus a new candidate, without a duplicate catalog path.
- Provider SDK/key/response failures: test missing credential, missing optional dependency, malformed response, and redacted API failure.
- Provider message/route translation: test system message placement, assistant role mapping, model selection, usage and response ID extraction for both providers.
- Resident settings above configured operator limits: test effective values are clamped, recorded, and used by the next context assembly.

---

### Task 1: Reconcile Lanternslide candidate changes

**Files:**
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/lanternslide.py`
- Test: `VESTIGIA_MCP_Server/tests/test_lanternslide.py`

**Interfaces:**
- Consumes: `LanternslideService.scan(*, scan_id: str | None = None) -> dict[str, object]` and persisted `candidate_paths`, `progress_offset`, `entries`, `omissions`.
- Produces: private `_reconcile_candidates(state: dict[str, Any], current_paths: tuple[str, ...]) -> None`; the scan ID remains stable and public counts reflect current candidates.

- [ ] Write failing tests for removal of an unprocessed path and for a rename (remove old/add new) between batches. Assert no exception, same scan ID, no stale indexed/omitted path, and eventual completion.
- [ ] Run `PYTHONPATH=src python -m pytest tests/test_lanternslide.py -q` in `VESTIGIA_MCP_Server`; observe the expected incompatibility failure.
- [ ] Implement reconciliation: retain processed paths still present in queue order, remove disappeared paths and their result records, then append newly discovered paths; set progress to the retained processed prefix and refresh the manifest.
- [ ] Re-run the Lanternslide tests, then the full MCP Server suite with `PYTHONPATH=src python -m pytest -q`.

### Task 2: Add native Claude and Gemini text providers

**Files:**
- Create: `VESTIGIA_Runtime/src/vestigia/providers/anthropic_provider.py`
- Create: `VESTIGIA_Runtime/src/vestigia/providers/gemini_provider.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/providers/__init__.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/runtime.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/config.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/diagnostics.py`
- Modify: `VESTIGIA_Runtime/pyproject.toml`
- Create: `VESTIGIA_Runtime/tests/test_native_providers.py`
- Modify: `VESTIGIA_Runtime/tests/test_config.py` and provider help/config docs as needed.

**Interfaces:**
- Consumes: `Provider.complete(ProviderRequest) -> ProviderReply`, `ResolvedConfig.secret(name)`, and the existing `provider.kind`/model route configuration.
- Produces: `AnthropicProvider(config)` and `GeminiProvider(config)`, both implementing `complete` and `model_for`; `CoreRuntime` selects these from `provider.kind`.

- [ ] Write deterministic request/response tests for both adapters, including developer/system mapping, route selection, usage/id extraction, and malformed/error responses.
- [ ] Run `PYTHONPATH=src python -m pytest tests/test_native_providers.py -q`; observe missing adapter/factory failures.
- [ ] Add the adapters with optional vendor SDK extras, provider-specific credentials, and safe error normalization. Keep network requests mocked at the transport boundary; do not test against live APIs.
- [ ] Add factory/config/diagnostic tests proving provider selection and credential presence reporting without revealing values.
- [ ] Run targeted provider/config tests, then the full Runtime suite with `PYTHONPATH=src python -m pytest -q`.

### Task 3: Add bounded resident retrieval controls

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/context_controls.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/context_sources.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/context.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/config.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/capability_contracts.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/house_tools.py`
- Test: `VESTIGIA_Runtime/tests/test_context_controls.py` (or focused tests in `test_vestigia.py`) and `VESTIGIA_Runtime/tests/test_context_introspection.py`.

**Interfaces:**
- Consumes: resident-scoped `context.control`, `ContextSourceRequest`, `RuntimeMemoryContextSource`, and persisted context receipts.
- Produces: resident-configurable `retrieval_limit` and `retrieval_tokens`; both are reported as requested/operator/effective values and applied to future context assembly.

- [ ] Write tests proving configured retrieval count/budget changes affect assembly and stay under operator ceilings.
- [ ] Run the focused context-control tests and observe missing field/configuration behavior.
- [ ] Extend defaults, persisted control loading, schemas, handlers, and source requests; preserve prior settings on partial configuration and reset to defaults on reset.
- [ ] Verify `retrieval.inspect` continues to expose source provenance and per-item inclusion/omission without claiming causal model use.
- [ ] Run focused context tests, then the full Runtime suite with `PYTHONPATH=src python -m pytest -q`.
