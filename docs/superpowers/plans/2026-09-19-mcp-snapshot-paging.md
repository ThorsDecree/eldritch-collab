# Snapshot-Bound Archive Paging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read Archive text and arbitrary files beyond 1 MB through bounded, snapshot-aware, signed pages that make continuity and budget state legible.

**Architecture:** Add a small durable browse-session store and HMAC cursor codec under MCP state storage. ArchiveSource streams and hashes source content while returning only the requested UTF-8-safe text or base64 byte page. The public server exposes upgraded `archive.read_text` and a new read-only `archive.read_bytes`; a changed artifact returns an explicit condition instead of mixed-revision data.

**Tech Stack:** Python 3.11+, standard-library hashlib/hmac/base64/zipfile/codecs, MCP SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-bell-retrieval-and-snapshot-paging-design.md`

## Global Constraints

- A page is not a complete read: return requested, returned, truncated, and remaining byte budgets separately.
- Bind a browse continuation to source, normalized path, operation, policy-scope digest, snapshot digest, page size, and expiry.
- Use an HMAC key persisted in MCP state storage; a public checksum alone is not authorization or integrity protection.
- On content mismatch return `file_changed_during_browse` with no data page; do not silently continue against a changed file.
- `archive.read_text` accepts configured text-like suffixes and never splits UTF-8; raw byte offsets and content SHA remain citation evidence.
- `archive.read_bytes` is read-only transport for allowed regular files, including SQLite files; it is not a SQL execution surface.
- Existing literal search remains explicit about unscanned/oversize files; do not claim it becomes exhaustive in this sprint.

## Review Focus

1. A file altered to the same byte length must still be detected through SHA-256; cover in Task 2 changed-file tests.
2. A four-byte UTF-8 character at a page boundary must not be split or lost; cover in Task 2 text-boundary tests.
3. A forged cursor with a valid-looking JSON body must fail HMAC verification; cover in Task 1.
4. Base64 output must honor the outbound page ceiling after encoding expansion; cover in Task 2 byte-page tests.
5. An expired or policy-scope-mismatched cursor must not create a fresh browse session or leak a page; cover in Task 3.

---

## File structure

| File | Responsibility |
| --- | --- |
| `VESTIGIA_MCP_Server/src/vestigia_mcp/browse.py` | HMAC cursor codec, durable session records, expiry and snapshot comparison. |
| `VESTIGIA_MCP_Server/src/vestigia_mcp/config.py` | Browse TTL, maximum raw page size, and state-backed cursor-secret configuration. |
| `VESTIGIA_MCP_Server/src/vestigia_mcp/adapters/archive.py` | Streaming hash/range primitives; UTF-8 line-aware and generic byte pages. |
| `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py` | `archive.read_text` contract update and new `archive.read_bytes` tool. |
| `VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py` | Read-only `archive.read_bytes` capability. |
| `VESTIGIA_MCP_Server/tests/test_browse.py` | Cursor/session integrity and expiry tests. |
| `VESTIGIA_MCP_Server/tests/test_archive_adapter.py` | Directory/ZIP large-text, UTF-8, byte-range, and changed-artifact tests. |
| `VESTIGIA_MCP_Server/README.md` | User-facing page/cursor and incomplete-search contract. |

### Task 1: Durable HMAC browse sessions

**Files:**
- Create: `VESTIGIA_MCP_Server/src/vestigia_mcp/browse.py`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/config.py:10-113`
- Create: `VESTIGIA_MCP_Server/tests/test_browse.py`

**Interfaces:**
- Produces `BrowseSessionStore(state_dir: Path, *, ttl_seconds: int, secret: bytes | None = None)`.
- Produces `BrowseCursorCodec(secret: bytes)` with `encode(kind, claims) -> str` and `decode(token, expected_kind) -> dict[str, object]`.
- Produces `BrowseSessionStore.create(...)`, `load(browse_id)`, and `validate_continuation(...)`.
- Settings add `archive_browse_ttl_seconds: int = 900` and `archive_page_max_bytes: int = 64_000`.

- [ ] **Step 1: Write failing codec/session tests**

Create `tests/test_browse.py`:

```python
def test_cursor_is_signed_expiring_and_operation_bound(tmp_path: Path) -> None:
    store = BrowseSessionStore(tmp_path, ttl_seconds=60, secret=b"x" * 32)
    session = store.create(
        kind="archive.read_text",
        source="live",
        path="logs/long.md",
        policy_scope="archive.read_text:test-policy",
        page_bytes=1024,
        snapshot_sha256="a" * 64,
        size=4096,
        now=_at("2026-09-19T12:00:00+00:00"),
    )
    claims = store.decode(session.cursor, "archive.read_text", now=_at("2026-09-19T12:00:30+00:00"))
    assert claims["browse_id"] == session.id
    with pytest.raises(BrowseCursorError, match="signature"):
        store.decode(session.cursor[:-1] + "x", "archive.read_text", now=_at("2026-09-19T12:00:30+00:00"))
    with pytest.raises(BrowseCursorError, match="expired"):
        store.decode(session.cursor, "archive.read_text", now=_at("2026-09-19T12:01:01+00:00"))
```

Add a scope/page-size mismatch case and a test that `load` survives constructing a second store with the same `state_dir` and secret.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `cd VESTIGIA_MCP_Server && pytest tests/test_browse.py -v`

Expected: FAIL because `vestigia_mcp.browse` does not exist.

- [ ] **Step 3: Implement state-backed cursors**

Implement a JSON session record beneath `state_dir / "browse_sessions"`, written atomically with restrictive file permissions where supported. Load or create a random 32-byte secret in `state_dir / "cursor_secret.bin"` if no explicit test/deployment secret is supplied. Sign canonical JSON claims with `hmac.new(secret, payload, hashlib.sha256)`.

The cursor claims must include:
```python
{
    "v": 2,
    "kind": "archive.read_text",
    "browse_id": "...",
    "source": "live",
    "path": "logs/long.md",
    "offset": 1024,
    "page_bytes": 1024,
    "policy_scope": "archive.read_text:<digest>",
    "expires_at": "...",
}
```

The durable record additionally stores `snapshot_sha256`, `size`, `source_revision`, and `created_at`. `decode` validates syntax, HMAC, operation kind, claim types, and expiry before file access. Use distinct errors for malformed, invalid signature, wrong operation, expired, and scope/page-size mismatch.

Add settings parsing:
```python
archive_browse_ttl_seconds=_positive_int_env(
    "VESTIGIA_MCP_ARCHIVE_BROWSE_TTL_SECONDS", 900
),
archive_page_max_bytes=_positive_int_env(
    "VESTIGIA_MCP_ARCHIVE_PAGE_MAX_BYTES", 64_000
),
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `cd VESTIGIA_MCP_Server && pytest tests/test_browse.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/browse.py VESTIGIA_MCP_Server/src/vestigia_mcp/config.py VESTIGIA_MCP_Server/tests/test_browse.py
git commit -m "feat(mcp): add durable signed browse sessions"
```

### Task 2: Stream large text and arbitrary byte pages

**Files:**
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/adapters/archive.py:68-75, 309-444`
- Modify: `VESTIGIA_MCP_Server/tests/test_archive_adapter.py:18-294`
- Test: `VESTIGIA_MCP_Server/tests/test_browse.py`

**Interfaces:**
- Produces `ArchiveSource.read_text_page(relative, *, page_bytes, cursor, browse_store, policy_scope) -> dict[str, object]`.
- Produces `ArchiveSource.read_bytes_page(relative, *, page_bytes, cursor, browse_store, policy_scope) -> dict[str, object]`.
- Text result includes `content`, `byte_start`, `byte_end`, `line_start`, `line_end`, `content_sha256`, `snapshot_status`, `next_cursor`, and `budget`.
- Byte result includes base64 `data` and the same raw byte/snapshot/budget fields.

- [ ] **Step 1: Write failing archive paging tests**

Append these focused tests to `test_archive_adapter.py`:

```python
def test_large_utf8_text_pages_ignore_total_admission_ceiling_and_preserve_lines(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "huge.md"
    path.parent.mkdir()
    path.write_text(("α\n" * 600_000) + "tail\n", encoding="utf-8")
    source = ArchiveSource(tmp_path)
    first = source.read_text_page(
        "logs/huge.md", page_bytes=257, browse_store=_store(tmp_path),
        policy_scope="archive.read_text:test",
    )
    assert first["budget"]["returned_bytes"] <= 257
    assert first["line_start"] == 1
    assert first["snapshot_status"] == "same_snapshot"
    second = source.read_text_page(
        "logs/huge.md", page_bytes=257, cursor=first["next_cursor"],
        browse_store=_store(tmp_path), policy_scope="archive.read_text:test",
    )
    assert second["byte_start"] == first["byte_end"]
    assert second["content"].encode("utf-8")
```

Add a mutation between pages that replaces bytes without changing length and asserts:
```python
assert changed["snapshot_status"] == "file_changed_during_browse"
assert "data" not in changed and "content" not in changed
```

Add a SQLite-like binary fixture with `b"SQLite format 3\\x00" + bytes(range(256))`, assert a decoded `data` slice matches the requested offset, and assert base64 output does not exceed its configured encoded ceiling.

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run: `cd VESTIGIA_MCP_Server && pytest tests/test_archive_adapter.py -k "large_utf8 or read_bytes or changed" -v`

Expected: FAIL because `read_text_page` rejects over 1 MB before paging and no byte-page method exists.

- [ ] **Step 3: Replace full-file admission reads with streaming page primitives**

Keep `_read_text_bytes` and `read_text` for existing bounded callers/search behavior. Add independent streaming helpers for pages:

```python
def _stream_member(self, relative: str) -> tuple[BinaryIO, int, str]:
    ...

def _snapshot_and_slice(
    self, relative: str, *, offset: int, page_bytes: int, text: bool
) -> SnapshotSlice:
    ...
```

For a directory source, stream from a regular resolved file; for a ZIP source, stream from `ZipFile.open(info)`. Hash the complete logical file stream with SHA-256 during each browse validation, while buffering only the requested raw range. For text, use an incremental strict UTF-8 decoder and move the endpoint backward until the page is decodable; count newline boundaries while scanning to produce 1-based `line_start` and `line_end`. For bytes, enforce `page_bytes <= archive_page_max_bytes` after accounting for base64 expansion.

On the first page, create a browse session from the resulting full-stream digest/size/revision and return a signed continuation if content remains. On a continuation, decode and load the session, compare current digest/size against the stored snapshot, and return:
```python
{
    "snapshot_status": "file_changed_during_browse",
    "path": normalized,
    "content_sha256": stored_digest,
    "current_content_sha256": current_digest,
    "next_cursor": None,
}
```
when mismatched. Do not call `encode` with a new snapshot in that case.

Use a single `budget` object:
```python
{
    "requested_bytes": page_bytes,
    "returned_bytes": len(raw_page),
    "truncated": end < total_size,
    "remaining_bytes": total_size - end,
}
```

- [ ] **Step 4: Run archive paging tests**

Run: `cd VESTIGIA_MCP_Server && pytest tests/test_archive_adapter.py tests/test_browse.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/adapters/archive.py VESTIGIA_MCP_Server/tests/test_archive_adapter.py VESTIGIA_MCP_Server/tests/test_browse.py
git commit -m "feat(mcp): page large archive text and bytes"
```

### Task 3: MCP tools, capability policy, and honest search documentation

**Files:**
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/server.py:125-355`
- Modify: `VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py:18-80`
- Modify: `VESTIGIA_MCP_Server/README.md`
- Modify: `VESTIGIA_MCP_Server/tests/test_archive_adapter.py`

**Interfaces:**
- `archive.read_text(source, path, cursor=None, page_bytes=64_000)` returns the streaming text page and permits large total file sizes.
- `archive.read_bytes(source, path, cursor=None, page_bytes=48_000)` returns a base64 page.
- `archive.read_bytes` is a `PERCEIVE` capability with `ALLOW` default.

- [ ] **Step 1: Write failing server-contract tests**

Add direct server-operation tests using the project’s MCP test helper or an equivalent registered-tool invocation. Assert that:
```python
result = archive_read_text("live", "logs/huge.md", page_bytes=512)
assert result["budget"]["requested_bytes"] == 512
assert result["snapshot_status"] == "same_snapshot"

binary = archive_read_bytes("live", "db/runtime.sqlite", page_bytes=512)
assert base64.b64decode(binary["data"]) == fixture[:512]
```
Also assert `DEFAULT_CAPABILITIES` contains exactly one `archive.read_bytes` capability with `EffectClass.PERCEIVE`.

- [ ] **Step 2: Run the focused server tests to verify they fail**

Run: `cd VESTIGIA_MCP_Server && pytest -k "read_bytes or server_contract" -v`

Expected: FAIL because no `archive.read_bytes` tool/capability is registered and server still supplies `archive_text_max_bytes` as a whole-file ceiling.

- [ ] **Step 3: Register tools and document incomplete-search semantics**

Instantiate one `BrowseSessionStore` in `create_server` using `settings.state_dir` and `settings.archive_browse_ttl_seconds`. Derive a stable policy-scope string from the capability name plus deployment/policy digest; pass it into ArchiveSource page methods after `guarded` authorizes the operation.

Register:
```python
@server.tool(name="archive.read_bytes", ..., annotations=READ_ONLY_ANNOTATIONS)
def archive_read_bytes(source: str, path: str, cursor: str | None = None,
                       page_bytes: int = 48_000) -> dict[str, object]:
    ...
```

Update `archive.read_text` help to say its cursor is snapshot-bound and expiry-limited, and a page is bounded evidence rather than a whole-file assertion. Add the same language to README, including:
- `same_snapshot` versus `file_changed_during_browse`;
- requested/returned/truncated/remaining page budgets;
- binary paging is transport, not database querying;
- `archive.search_text` still reports files it skipped or limited.

- [ ] **Step 4: Run the full MCP test suite**

Run: `cd VESTIGIA_MCP_Server && pytest -q`

Expected: PASS with zero failures.

- [ ] **Step 5: Commit**

```bash
git add VESTIGIA_MCP_Server/src/vestigia_mcp/server.py VESTIGIA_MCP_Server/src/vestigia_mcp/policy.py VESTIGIA_MCP_Server/README.md VESTIGIA_MCP_Server/tests/test_archive_adapter.py
git commit -m "feat(mcp): expose snapshot-bound archive pages"
```

### Task 4: Cross-contract regression pass

**Files:**
- Test: `VESTIGIA_MCP_Server/tests/test_browse.py`
- Test: `VESTIGIA_MCP_Server/tests/test_archive_adapter.py`
- Modify: `VESTIGIA_MCP_Server/README.md` only if test-discovered behavior requires documentation correction.

**Interfaces:**
- Legacy list/search pagination remains compatible.
- New browse cursors are unambiguously versioned and independent of legacy checksum-only cursor decoding.

- [ ] **Step 1: Add legacy compatibility assertions**

Assert the existing `list_paths` and `search_text` tests continue to pass unchanged. Add one explicit test that a legacy `archive.read_text` checksum-style cursor is rejected as an unsupported new browse cursor rather than interpreted as a valid session.

- [ ] **Step 2: Run all tests**

Run: `cd VESTIGIA_MCP_Server && pytest -q`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add VESTIGIA_MCP_Server/tests/test_browse.py VESTIGIA_MCP_Server/tests/test_archive_adapter.py VESTIGIA_MCP_Server/README.md
git commit -m "test(mcp): lock snapshot paging compatibility"
```
