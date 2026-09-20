# Porchlight Warm Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a digest-bound MCP staging contract that stores Porchlight readable snapshots as searchable latest receipts plus addressable history.

**Architecture:** A pure MCP contract derives a stable source key from a canonical URL and renders one readable body, one immutable history body, and one JSON receipt. The MCP server stages all three through `ArchiveMutationStore`; existing promotion remains the authority. Runtime reads `Porchlight/latest` as ordinary Archive evidence and gains no memory authority.

**Tech Stack:** Python 3.11+, dataclasses, `urllib.parse`, standard-library JSON/hash helpers, pytest, and existing MCP staged-write APIs.

**Spec:** `docs/superpowers/specs/2026-09-19-porchlight-warm-snapshot-design.md`

## Global Constraints

- Readable body is searchable; control metadata remains in JSON receipts.
- `Porchlight/latest/` is normal retrieval; history and receipts require explicit paths.
- `archive.stage_porchlight` creates stages only and never changes live Archive bytes.
- Existing `archive.promote` individually promotes every returned stage.
- Operators provision `Porchlight/latest`, `Porchlight/history`, and `Porchlight/receipts` first through the directory-stage workflow.
- This slice contains no browser extension/native host, raw HTML, or background monitoring.

## Review Focus

- Canonical URL fragments and query ordering must resolve to one source key (Task 1).
- URL/title/mode must be absent from the searchable body (Task 1).
- Empty/NUL/oversize content, invalid timestamps, and bad predecessor hashes must fail before staging (Task 1).
- Repeated capture may replace latest but cannot replace live history or receipt paths (Task 2).
- Policy must remain PREPARE-only and audit must record body hash/size rather than body content (Tasks 2–3).

---

### Task 1: Porchlight snapshot contract

**Files:**

- Create: `VESTIGIA_MCP_Server/src/vestigia_mcp/porchlight.py`
- Create: `VESTIGIA_MCP_Server/tests/test_porchlight.py`

**Interfaces:**

- Produces `SnapshotArtifact`, `build_snapshot`, `canonical_source_url`, `source_key_for_url`, and `is_latest_path`.
- `build_snapshot(url, title, content, mode, captured_at=None, previous_snapshot_sha256=None)` returns latest/history/receipt paths, body, receipt text/mapping, source key, and capture ID.

- [ ] **Step 1: Write failing tests**

```python
def test_snapshot_separates_control_metadata_from_searchable_body() -> None:
    artifact = build_snapshot(
        url="https://www.reddit.com/r/test/comments/abc/?b=2&a=1#reply",
        title="A thread", content="The reply that matters.", mode="page",
        captured_at="2026-09-19T12:00:00+00:00",
    )
    assert "The reply that matters." in artifact.body
    assert "reddit.com" not in artifact.body
    assert "A thread" not in artifact.body
    assert artifact.receipt["source_url"] == "https://www.reddit.com/r/test/comments/abc?a=1&b=2"
```

Add literal tests for stable latest/history/receipt paths, invalid mode/timestamp/empty/NUL/oversize content, non-web URLs, and malformed predecessor hashes.

- [ ] **Step 2: Run RED**

Run: `cd VESTIGIA_MCP_Server && pytest -q tests/test_porchlight.py`

Expected: import failure because `vestigia_mcp.porchlight` does not exist.

- [ ] **Step 3: Implement the minimal contract**

Require HTTP(S) URLs without credentials; lowercase hostnames, drop fragments, sort query pairs, omit default ports, and hash the canonical URL to a 24-character source key. Permit `selection`, `page`, and `update`; require timezone-aware ISO-8601 timestamps when present; enforce nonempty NUL-free body text under 1,000,000 UTF-8 bytes. Render the body without metadata and render a sorted receipt JSON.

```python
latest_path = f"Porchlight/latest/{source_key}.md"
history_path = f"Porchlight/history/{source_key}_{capture_id}.md"
receipt_path = f"Porchlight/receipts/{source_key}_{capture_id}.json"
```

- [ ] **Step 4: Run GREEN**

Run: `cd VESTIGIA_MCP_Server && pytest -q tests/test_porchlight.py`

Expected: every focused test passes.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/porchlight.py VESTIGIA_MCP_Server/tests/test_porchlight.py
git commit -m "feat(mcp): add Porchlight snapshot contract"
```

### Task 2: MCP Porchlight staging capability

**Files:**

- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_server_catalog.py`
- Modify: `VESTIGIA_MCP_Server/tests/test_porchlight.py`

**Interfaces:**

- Consumes `SnapshotArtifact` and `build_snapshot`.
- Produces `archive.stage_porchlight(url, title, content, mode, captured_at?, previous_snapshot_sha256?)` returning capture/source IDs, three public stage records, and `canonical_changed: false`.

- [ ] **Step 1: Write failing catalog/integration tests**

```python
def test_policy_catalog_exposes_porchlight_staging() -> None:
    assert "archive.stage_porchlight" in {cap.name for cap in DEFAULT_CAPABILITIES}

def test_porchlight_stage_creates_latest_history_and_receipt_stages(tmp_path: Path) -> None:
    # Provision live/Porchlight/{latest,history,receipts}; invoke the tool; assert the
    # three stages are returned as staged and canonical bytes remain unchanged.
```

- [ ] **Step 2: Run RED**

Run: `cd VESTIGIA_MCP_Server && pytest -q tests/test_porchlight.py tests/test_server_catalog.py`

Expected: missing policy/tool failures.

- [ ] **Step 3: Implement the PREPARE-only tool**

Register `archive.stage_porchlight` with `LOCAL_WRITE_ANNOTATIONS` and a PREPARE capability. Build the contract under `guarded`, translate `ValueError` to `ArchiveError`, reject already-live history/receipt paths, stage latest with the predecessor hash, and stage history/receipt with `expected_base_sha256="absent"`. Return the paths, all three stage records, `canonical_changed: false`, and individual `archive.promote` instructions. Audit URL/title/mode/timing/predecessor plus body SHA-256 and byte count, never the body.

- [ ] **Step 4: Run GREEN**

Run: `cd VESTIGIA_MCP_Server && pytest -q tests/test_porchlight.py tests/test_server_catalog.py`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/server.py VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py VESTIGIA_MCP_Server/tests/test_server_catalog.py VESTIGIA_MCP_Server/tests/test_porchlight.py
git commit -m "feat(mcp): stage Porchlight warm snapshots"
```

### Task 3: Documentation and verification

**Files:**

- Modify: `VESTIGIA_MCP_Server/README.md`
- Modify: `VESTIGIA_MCP_Server/docs/ROADMAP.md`
- Create: `VESTIGIA_MCP_Server/docs/PORCHLIGHT.md`
- Modify: `VESTIGIA_MCP_Server/tests/test_policy.py`

**Interfaces:**

- Documents input, latest-only retrieval, staged promotion, and the future browser-producer boundary.

- [ ] **Step 1: Add policy regression**

```python
def test_porchlight_staging_is_prepare_only_and_not_canonical_write() -> None:
    capability = PolicyEngine().require_allowed("archive.stage_porchlight")
    assert capability.effect is EffectClass.PREPARE
    assert capability.default is Decision.ALLOW
```

- [ ] **Step 2: Run focused policy test**

Run: `cd VESTIGIA_MCP_Server && pytest -q tests/test_policy.py::test_porchlight_staging_is_prepare_only_and_not_canonical_write`

Expected: pass after Task 2, pinning the policy contract.

- [ ] **Step 3: Write operator docs**

Create `docs/PORCHLIGHT.md` explaining input fields/modes, body/receipt separation, paths, directory provisioning, staged-only behavior, individual promotion, latest-only retrieval, partial-stage inspection/discard, and the absent Chrome extension/native host. Add a concise README entry and roadmap note under browser/desktop perception.

- [ ] **Step 4: Verify full MCP suite and whitespace**

Run: `cd VESTIGIA_MCP_Server && pytest -q -ra && git diff --check`

Expected: full suite passes and diff check emits no output.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/README.md VESTIGIA_MCP_Server/docs/PORCHLIGHT.md VESTIGIA_MCP_Server/docs/ROADMAP.md VESTIGIA_MCP_Server/tests/test_policy.py
git commit -m "docs(mcp): document Porchlight warm storage"
```

## Self-Review

- Task 1 owns identity and control/body separation; Task 2 owns staged authority; Task 3 owns retrieval and operator boundaries.
- Every Review Focus item maps to a named test-owning task.
- Task 2 consumes exactly the Task 1 artifact/function interface.
- The plan contains no placeholder implementation or unbounded future work.
