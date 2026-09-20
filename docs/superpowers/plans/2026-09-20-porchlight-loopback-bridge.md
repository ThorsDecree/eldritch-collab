# Porchlight Loopback Bridge MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MV3 Chrome extension and authenticated loopback Python bridge that directly shares readable Porchlight captures and optional visible screenshots into `Modules/Porchlight`.

**Architecture:** Add an atomic Archive bundle primitive, a shared direct-share service, a standard-library HTTP bridge on `127.0.0.1`, and a vanilla MV3 extension. Preserve the existing staged MCP import path.

**Tech Stack:** Python 3.11+, existing VESTIGIA MCP adapters, `http.server`, Chrome MV3, vanilla JavaScript, pytest, and Node’s built-in test runner. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-09-20-porchlight-loopback-bridge-design.md`

## Global constraints

- Explicit resident action only; no polling, background monitoring, automatic memory promotion, public HTTP, LAN access, or Native Messaging.
- Readable text only; no raw HTML/cookies/headers/scripts/styles/credentials.
- Screenshot is visible viewport PNG, opt-in and separate from semantic text.
- New direct artifacts use `Modules/Porchlight/{latest,history,receipts,images}`; old root-level artifacts stay readable.
- Direct share still enforces write prefixes, normalization, hashes, byte limits, conflict checks, audit metadata, and all-or-none bundle behavior.

## Review focus

- `0.0.0.0` fails closed.
- A stale update cannot overwrite newer latest content.
- Screenshot/write failure leaves no partial bundle.
- Bad token/origin never reaches Archive mutation code.
- Oversized or empty page content produces a client-visible error and no share.

### Task 1: Direct Archive bundle primitive

**Files:** modify `VESTIGIA_MCP_Server/src/vestigia_mcp/archive_mutation.py` and `policy.py`; test `tests/test_archive_mutation.py` and `tests/test_policy.py`.

- [ ] Add failing tests for text/history/receipt/PNG bundle success, hashes, idempotent same-content latest, invalid image, mismatched latest base, rollback, and the direct policy capability.
- [ ] Run `cd VESTIGIA_MCP_Server && ../.venv/bin/pytest -q tests/test_archive_mutation.py -k bundle && ../.venv/bin/pytest -q tests/test_policy.py -k porchlight`; observe expected missing API failures.
- [ ] Implement bounded `BundleEntry` and `ArchiveMutationStore.share_bundle(entries, expected_base_sha256_by_path, reason, audit_metadata)`: normalize and validate every entry before writing, enforce write prefixes/limits, validate PNG signatures, use temp files plus flush/fsync/replace under the mutation lock, restore replaced targets on failure, return per-entry hashes and atomic/changed metadata, and make equal bytes idempotent.
- [ ] Re-run focused tests and the existing archive/policy tests.
- [ ] Commit `feat(mcp): add atomic Porchlight share bundles`.

### Task 2: Shared direct-share service

**Files:** create `VESTIGIA_MCP_Server/src/vestigia_mcp/porchlight_share.py`; modify `porchlight.py`, `server.py`, `config.py`; test `tests/test_porchlight.py` and `tests/test_server_catalog.py`.

- [ ] Add failing tests for `PorchlightShareRequest`, `PorchlightShareService.share`, `Modules/Porchlight` paths, explicit-consent marker, no stage/promotion fields, selection/page/update validation, unchanged updates, stale conflicts, screenshot metadata/image path, and catalog annotations for `archive.share_porchlight`.
- [ ] Run focused tests and observe expected missing service/API failures.
- [ ] Implement shared validation and direct service over `share_bundle`; use current latest body/hash for update baselines; return paths/hashes/IDs/status without raw content; retain staged `archive.stage_porchlight` compatibility.
- [ ] Add bounded screenshot/bridge settings and policy/catalog registration without changing staged tool annotations.
- [ ] Run focused tests and commit `feat(mcp): add direct Porchlight sharing service`.

### Task 3: Authenticated loopback bridge

**Files:** create `VESTIGIA_MCP_Server/src/vestigia_mcp/porchlight_bridge.py`; modify `config.py`, `cli.py`; test `tests/test_porchlight_bridge.py` and `tests/test_config.py`.

- [ ] Add failing HTTP tests for health, pair verification, valid share, missing/bad token, wrong origin, malformed/oversized JSON, non-loopback rejection, service isolation on rejects, and token rotation.
- [ ] Run the bridge tests and observe missing-module/settings failures.
- [ ] Implement `PairingTokenStore`, bounded `ThreadingHTTPServer`, exact JSON/auth/origin handling, correlation IDs, 400/401/403/409/503 mapping, no raw content in logs/errors, and loopback-only host enforcement.
- [ ] Add `run-porchlight-bridge` plus pairing-token CLI behavior while preserving MCP stdio startup.
- [ ] Run bridge/config tests and commit `feat(mcp): add authenticated Porchlight loopback bridge`.

### Task 4: MV3 extension capture surface

**Files:** create `VESTIGIA_MCP_Server/porchlight_extension/{manifest.json,service_worker.js,popup.html,popup.js,options.html,options.js,README.md}`; test `tests/extension/test_capture_contract.mjs`.

- [ ] Add failing Node contract tests for selection/page/update payloads, empty selection, readable-body capture, screenshot omission by default, and shared/unchanged/conflict/error response states.
- [ ] Run Node tests and observe missing-helper failures.
- [ ] Implement minimal MV3 permissions (`activeTab`, `scripting`, `storage`, `tabs`, configured loopback host), click-triggered extraction, context-menu selection, popup selection/page/update actions, size checks, opt-in `captureVisibleTab`, pairing storage, origin/auth headers, and status UI.
- [ ] Run Node tests and commit `feat: add Porchlight Chrome capture extension`.

### Task 5: Launcher, docs, and integration fixture

**Files:** modify `VESTIGIA_MCP_Server/pyproject.toml`, README/docs, and Windows example; create `tests/test_porchlight_integration.py`.

- [ ] Add failing temporary-Archive HTTP integration test for text/image shares, paths, hashes, direct marker, unchanged update, and failed-bundle cleanup.
- [ ] Wire package entrypoints and Windows loopback/token/write-prefix examples; document extension loading, pairing, direct consent, screenshot privacy, conflicts, and legacy migration.
- [ ] Run integration test and commit `docs: wire Porchlight bridge launch and integration`.

### Task 6: Full verification and PR handoff

- [ ] Run MCP pytest suite, Node extension tests, `git diff --check`, and Runtime tests if interfaces changed.
- [ ] Inspect final diff for credentials, screenshots, Archive data, `.venv`, raw fixtures, and unintended scope.
- [ ] Push the feature branch through the authorized GitHub connector and open one PR against `main` with test results and manual Chrome smoke-test instructions.
