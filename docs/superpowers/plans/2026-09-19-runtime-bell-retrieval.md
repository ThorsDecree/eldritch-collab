# Bell Retrieval and Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduled Runtime bells retrieve from resident-meaningful context, expose deterministic provenance, and preserve explicit no-change outcomes.

**Architecture:** Persist an explicit bell retrieval policy and construct a typed bell retrieval envelope at delivery time. Context assembly receives a separate retrieval request instead of treating the rendered invitation as a search query; the same request shapes source selection, field scanning, and receipts. The current rendered bell remains the final user message.

**Tech Stack:** Python 3.11+, SQLite, PyYAML configuration, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-19-bell-retrieval-and-snapshot-paging-design.md`

## Global Constraints

- Automatic contextual retrieval is enabled by default; `auto` resolves to `prompt_only` for topical prompts and internal `field_scan_v1` for generic look-around bells.
- Bell IDs, schedules, purpose labels, authorization text, and fixed boilerplate are control-plane material and must not supply retrieval query terms.
- Receipts distinguish included context from causal influence or adoption, and distinguish zero results from unavailable/error sources.
- `response_related` is deferred to a later explicit turn; it must not retroactively retrieve into the bell turn that produced the response.
- Explicit no-change must create neither memory candidates nor curation eligibility; silence creates no turn.
- Preserve the existing resident-visible bell invitation and existing normal/orientation context ordering.

## Review Focus

1. A legacy bell row with no `retrieval_policy` must load as `auto`; cover in Task 1 migration tests.
2. Changing only an ID, schedule, or boilerplate must not change dynamic retrieval terms; cover in Task 2 receipt tests.
3. An unavailable Archive source must not look like an empty search; cover in Task 2 source-failure tests.
4. `none` and empty `resident_selected` must leave protected resident layers intact while invoking no dynamic source; cover in Task 2.
5. A no-op bell reply must not accidentally bypass normal curation safeguards for a non-bell turn; cover in Task 3 Runtime tests.

---

## File structure

| File | Responsibility |
| --- | --- |
| `VESTIGIA_Runtime/src/vestigia/bells.py` | Bell policy persistence, legacy migration, validated typed envelope, invitation rendering. |
| `VESTIGIA_Runtime/src/vestigia/bell_retrieval.py` | Pure policy resolver and deterministic `field_scan_v1` selector. |
| `VESTIGIA_Runtime/src/vestigia/context_sources.py` | Expanded source request/result provenance fields. |
| `VESTIGIA_Runtime/src/vestigia/context.py` | Context assembly uses retrieval request, applies source scope, and writes receipt/budget details. |
| `VESTIGIA_Runtime/src/vestigia/runtime.py` | Passes bell metadata to the provider and skips extraction/curation only for explicit bell no-change. |
| `VESTIGIA_Runtime/src/vestigia/adapters/discord_adapter.py` | Builds the envelope from a Bell rather than relying on invitation text. |
| `VESTIGIA_Runtime/tests/test_vestigia.py` | Existing single-suite home/Runtime regression coverage. |

### Task 1: Persisted bell retrieval policy and typed envelope

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/bells.py:18-98, 216-365, 640-690`
- Modify: `VESTIGIA_Runtime/tests/test_vestigia.py:496-715`

**Interfaces:**
- Produces `RETRIEVAL_POLICIES = {"auto", "none", "prompt_only", "response_related", "resident_selected"}`.
- Produces `Bell.retrieval_policy: str` and `BellService.retrieval_envelope(bell) -> dict[str, object]`.
- `retrieval_envelope` returns `resident_prompt`, `requested_policy`, `selected_sources`, and `control_plane`; control-plane content is separate from the rendered invitation.

- [ ] **Step 1: Write failing scheduler tests**

Add tests beside `BellSchedulerTests.test_once_bell_has_visible_registry_and_append_only_receipts`:

```python
def test_bell_policy_defaults_to_auto_and_legacy_schema_is_migrated(self) -> None:
    service = self.service()
    bell = service.create(..., prompt="Check the archive trail.")
    self.assertEqual("auto", bell.retrieval_policy)
    envelope = service.retrieval_envelope(bell)
    self.assertEqual("Check the archive trail.", envelope["resident_prompt"])
    self.assertTrue(envelope["control_plane_excluded"])
    self.assertNotIn("Bell ID:", envelope["resident_prompt"])

def test_bell_retrieval_policy_is_validated_and_revisable(self) -> None:
    service = self.service()
    bell = service.create(..., retrieval_policy="resident_selected")
    self.assertEqual("resident_selected", bell.retrieval_policy)
    self.assertEqual("none", service.revise(
        bell.id, actor="resident:Liora", retrieval_policy="none"
    ).retrieval_policy)
    with self.assertRaisesRegex(ValueError, "retrieval policy"):
        service.create(..., retrieval_policy="semantic_boilerplate")
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -k "bell_policy or retrieval_policy" -v`

Expected: FAIL because `Bell` lacks `retrieval_policy` and `BellService` lacks `retrieval_envelope`.

- [ ] **Step 3: Add storage, migration, and envelope construction**

In `bells.py`:

```python
RETRIEVAL_POLICIES = frozenset({
    "auto", "none", "prompt_only", "response_related", "resident_selected",
})

def _migrate_bell_schema(connection: Any) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(bells)")}
    if "retrieval_policy" not in columns:
        connection.execute(
            "ALTER TABLE bells ADD COLUMN retrieval_policy TEXT NOT NULL DEFAULT 'auto'"
        )
```

Call `_migrate_bell_schema` immediately after `connection.executescript(BELL_SCHEMA)`. Add the column to the create-table definition, `Bell`, `create`, `revise`, INSERT/UPDATE statements, and `_row`. Validate policy membership before writes. Implement `retrieval_envelope` with this shape:

```python
{
    "resident_prompt": bell.prompt,
    "requested_policy": bell.retrieval_policy,
    "selected_sources": (),
    "control_plane": {
        "bell_id": bell.id,
        "title": bell.title,
        "purpose": bell.purpose,
        "strength": bell.strength,
        "schedule_kind": bell.schedule_kind,
        "schedule": bell.schedule,
    },
    "control_plane_excluded": True,
}
```

Do not alter `invitation_text` except to retain its current display behavior.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -k "bell_policy or retrieval_policy" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_Runtime/src/vestigia/bells.py VESTIGIA_Runtime/tests/test_vestigia.py
git commit -m "feat(runtime): add typed bell retrieval policy"
```

### Task 2: Retrieval resolver, scoped source execution, and inspectable receipts

**Files:**
- Create: `VESTIGIA_Runtime/src/vestigia/bell_retrieval.py`
- Modify: `VESTIGIA_Runtime/src/vestigia/context_sources.py:22-115`
- Modify: `VESTIGIA_Runtime/src/vestigia/context.py:67-440`
- Modify: `VESTIGIA_Runtime/tests/test_vestigia.py:195-265, 1624-1726`

**Interfaces:**
- Produces `resolve_retrieval_request(message, db, resident_id, room_id) -> RetrievalRequest`.
- Produces `RetrievalRequest(policy_requested, policy_effective, semantic_seed, selected_sources, control_plane_excluded, deferred)`.
- Extends `ContextSourceRequest` with `retrieval_request: RetrievalRequest`.
- `ContextAssembler.assemble(...)` accepts no new public required argument; it derives the request from bell message metadata for compatibility.

- [ ] **Step 1: Write failing context and receipt tests**

Add a `BellRetrievalContextTests` class after `ContextTests`:

```python
def test_bell_prompt_only_excludes_fixed_control_plane_from_queries(self) -> None:
    message_a = NormalizedMessage(
        content="[VESTIGIA BELL ... Bell ID: A ...]\n\nReview the active bridge work.",
        interface="bell",
        metadata={"bell_retrieval": {
            "resident_prompt": "Review the active bridge work.",
            "requested_policy": "prompt_only",
            "control_plane": {"bell_id": "A", "schedule": {"time": "09:00"}},
        }},
    )
    message_b = replace(message_a, content=message_a.content.replace("A", "B"))
    first = ContextAssembler(self.config, self.db).assemble(message_a, state="ACTIVE")
    second = ContextAssembler(self.config, self.db).assemble(message_b, state="ACTIVE")
    self.assertEqual(
        self._receipt(first)["context_sources"][0]["query_terms"],
        self._receipt(second)["context_sources"][0]["query_terms"],
    )
    self.assertTrue(self._receipt(first)["retrieval"]["control_plane_excluded"])

def test_auto_generic_bell_uses_field_scan_and_none_skips_dynamic_sources(self) -> None:
    generic = self._bell_message("Notice what wants attention.", policy="auto")
    receipt = self._receipt(ContextAssembler(self.config, self.db).assemble(generic, state="ACTIVE"))
    self.assertEqual("field_scan_v1", receipt["retrieval"]["effective_policy"])
    disabled = self._bell_message("Notice what wants attention.", policy="none")
    none_receipt = self._receipt(ContextAssembler(self.config, self.db).assemble(disabled, state="ACTIVE"))
    self.assertEqual([], none_receipt["context_sources"])
    self.assertIn("identity_core", {layer["name"] for layer in none_receipt["layers"]})
```

Add an injected failing `ContextSource` test asserting `availability == "unavailable"`, `result_count == 0`, and a non-empty error reason, rather than a `zero_results` status.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -k "BellRetrievalContext or source_unavailable" -v`

Expected: FAIL because all sources currently receive `message.content`, and the receipt lacks retrieval policy/query-term fields.

- [ ] **Step 3: Implement the pure resolver and deterministic field scan**

Create `bell_retrieval.py` with frozen dataclasses and no provider calls:

```python
@dataclass(frozen=True)
class RetrievalRequest:
    policy_requested: str
    policy_effective: str
    semantic_seed: str | None
    selected_sources: tuple[str, ...]
    control_plane_excluded: bool
    deferred: bool = False

def resolve_retrieval_request(message: NormalizedMessage) -> RetrievalRequest:
    ...

def field_scan_v1(db: ContinuityDB, *, resident_id: str, room_id: str, limit: int) -> tuple[RetrievedMemory, ...]:
    ...
```

`resolve_retrieval_request` must:
- use normal message content for non-bell messages;
- use only `metadata["bell_retrieval"]["resident_prompt"]` for bell semantic seeds;
- resolve `auto` to `field_scan_v1` only for known generic purposes or prompt shapes maintained in this module;
- mark `response_related` as deferred and produce no current-turn dynamic request;
- reject unknown policy values by producing a safe `none` request plus a warning stored in receipts.

`field_scan_v1` must read existing eligible Runtime memories only; select at most `limit` records through stable ID tie-breaking and diversified memory types. It must record category reasons such as `recent`, `unresolved_tension`, `relationship_or_commitment`, or `under_visited`. It must never create or promote memory.

Extend `ContextSourceRequest` and `ContextSourceResult` to carry the request and structured availability/reason metadata. In `ContextAssembler._retrieve_context_sources`, skip dynamic sources for `none`, `response_related`, and unselected `resident_selected` scopes. For `field_scan_v1`, supply the scan result to the Runtime-memory source and request normal external source behavior only if explicitly selected; do not convert the full invitation into a query.

Add receipt keys:
```python
"retrieval": {
    "requested_policy": request.policy_requested,
    "effective_policy": request.policy_effective,
    "semantic_source": "bell.resident_prompt",
    "query_terms": normalized_terms,
    "control_plane_excluded": request.control_plane_excluded,
    "deferred": request.deferred,
}
```
and, for each source, requested/returned/included/remaining token budgets, omissions with reasons, availability, and `causal_influence: "unknown"`.

- [ ] **Step 4: Run focused and source-regression tests**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -k "Context or Retrieval or BellRetrievalContext" -v`

Expected: PASS, including existing retrieval-inspector tests.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_Runtime/src/vestigia/bell_retrieval.py VESTIGIA_Runtime/src/vestigia/context_sources.py VESTIGIA_Runtime/src/vestigia/context.py VESTIGIA_Runtime/tests/test_vestigia.py
git commit -m "feat(runtime): make bell retrieval provenance explicit"
```

### Task 3: Bell delivery metadata and explicit no-change suppression

**Files:**
- Modify: `VESTIGIA_Runtime/src/vestigia/adapters/discord_adapter.py:414-465`
- Modify: `VESTIGIA_Runtime/src/vestigia/runtime.py:190-365`
- Modify: `VESTIGIA_Runtime/tests/test_vestigia.py:496-715, 1727-1962`

**Interfaces:**
- Discord metadata contains `bell_retrieval=bell_service.retrieval_envelope(bell)`.
- Runtime result traces include a `bell_outcome` receipt when an explicit existing no-op control is applied.
- `_run_curation_if_due` is not called for an explicit bell no-change turn.

- [ ] **Step 1: Write failing delivery/no-change tests**

Add tests that construct a bell `NormalizedMessage` using the service envelope, then assert the Runtime provider receives the rendered invitation as the final user message while source receipt queries equal the resident prompt. Add a fake provider reply containing the existing no-op control and assert:

```python
result = runtime.chat(bell_message)
trace = json.loads((self.home / "traces" / f"{result.turn_id}.result.json").read_text())
self.assertEqual("no_change", trace["bell_outcome"]["state"])
self.assertEqual([], trace["proposal_ids"])
self.assertFalse(trace["bell_outcome"]["curation_eligible"])
self.assertEqual(0, self._curation_batch_count())
```

Add a non-bell no-op control case that confirms ordinary existing behavior is unchanged.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -k "bell and (no_change or delivery)" -v`

Expected: FAIL because Discord metadata has no typed envelope and Runtime always runs extraction/curation after a reply.

- [ ] **Step 3: Wire the envelope and gate only explicit bell no-change**

Set `metadata["bell_retrieval"]` in `ring_bell`; retain the current bell ID/purpose/strength audit fields. In `CoreRuntime._chat_core_unlocked`, detect the existing explicit no-op control through the authoritative resident-control result—not through natural-language matching. When the message is a bell and that control succeeded:
- set `bell_outcome = {"state": "no_change", "curation_eligible": False, "memory_candidate_created": False, "preference_inferred": False}`;
- skip participant-turn extraction and `_run_curation_if_due`;
- write the outcome in `.result.json` and a legible receipt;
- do not suppress or rewrite the resident’s visible prose.

A bell with no reply never reaches this code path; preserve that behavior.

- [ ] **Step 4: Run Runtime tests**

Run: `cd VESTIGIA_Runtime && pytest tests/test_vestigia.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_Runtime/src/vestigia/adapters/discord_adapter.py VESTIGIA_Runtime/src/vestigia/runtime.py VESTIGIA_Runtime/tests/test_vestigia.py
git commit -m "feat(runtime): preserve bell no-change outcomes"
```

### Task 4: Runtime documentation and clean-suite verification

**Files:**
- Modify: `VESTIGIA_Runtime/docs/BELLS.md`
- Modify: `VESTIGIA_Runtime/docs/ATTENTION.md` (or the existing attention design document if named differently)
- Test: `VESTIGIA_Runtime/tests/test_vestigia.py`

**Interfaces:**
- Documents policy meanings, effective `auto` resolution, receipt semantics, and no-change boundary.
- Does not advertise causal inference, automatic memory promotion, or scheduler-control text as retrieval content.

- [ ] **Step 1: Add documentation assertions to the plan’s manual review**

Document these exact statements:
```text
Control-plane bell fields are displayed and audited but excluded from retrieval terms.
Included context is not evidence of causal influence or adoption.
A no-change outcome needs explicit resident control; silence is not a response.
```

- [ ] **Step 2: Run the full Runtime suite**

Run: `cd VESTIGIA_Runtime && pytest -q`

Expected: PASS with zero failures.

- [ ] **Step 3: Commit**

```bash
git add VESTIGIA_Runtime/docs/BELLS.md VESTIGIA_Runtime/docs/ATTENTION.md
git commit -m "docs(runtime): explain bell retrieval boundaries"
```
