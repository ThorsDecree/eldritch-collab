# Bell Retrieval and Snapshot Paging Design

**Status:** Approved conversational design; pending written-spec review  
**Date:** 2026-09-19  
**Scope:** One coordinated sprint with two independently deployable subprojects:
1. VESTIGIA Runtime bell retrieval and provenance.
2. VESTIGIA MCP Server large-artifact paging and browse continuity.

## Intent and success criteria

A bell is an invitation whose actual prompt may request contextual retrieval. Scheduler identifiers, schedules, purpose labels, authorization text, and fixed boilerplate are control-plane material: they must remain available for display and audit without becoming a semantic magnet.

Automatic contextual retrieval remains enabled unless a resident disables or narrows it. Topic-bearing bell prompts retrieve from the prompt's meaningful text; generic “look around” bells use a bounded field scan. Each injected result has a deterministic receipt that reports retrieval mechanics without claiming that inclusion caused a response.

Archive browsing must serve text and binary artifacts larger than the current 1 MB admission ceiling. Every continuation must be bounded, expiry-limited, scoped, snapshot-aware, and explicit when a source changes during browse. A successful page must not be described as a complete read.

## Non-goals

- No claim that a retrieved context item caused, influenced, or was adopted by a resident response.
- No automatic curation, preference inference, or identity update from a bell that chooses nothing.
- No unrestricted SQL console. Binary paging supports database transport; structured SQLite inspection is a future separately-authorized surface.
- No invented per-user authorization. The current MCP deployment exposes server policy grants, not caller principals.
- No changes to canonical Archive content or Runtime resident content as part of this feature.

## A. Runtime bell retrieval

### A.1 Typed bell envelope

Runtime represents each bell turn with a typed retrieval envelope rather than deriving retrieval from the rendered invitation string.

```python
@dataclass(frozen=True)
class BellRetrievalEnvelope:
    bell_id: str
    resident_prompt: str
    control_plane: Mapping[str, object]
    requested_policy: Literal[
        "auto", "none", "prompt_only", "response_related", "resident_selected"
    ]
    selected_sources: tuple[str, ...] = ()
```

`control_plane` includes identity/schedule/purpose/authorization/boilerplate fields. The rendered invitation may display it, but ContextAssembler never uses it as a retrieval query or query-term source unless a future explicit opt-in is introduced.

`resident_prompt` is the freeform invitation text. It remains the resident-visible final user message, preserving the bell’s attention salience.

### A.2 Effective retrieval policies

The resident or bell configuration may request one of these values:

| Requested policy | Effective behavior |
| --- | --- |
| `auto` | Resolve deterministically: a topic-bearing resident prompt uses `prompt_only`; a generic look-around prompt uses `field_scan_v1`. |
| `none` | Do not invoke automatic Runtime-memory or Archive retrieval for this turn. Resident/context layers that are always resident remain available. |
| `prompt_only` | Query retrieval sources only with normalized terms derived from `resident_prompt`. |
| `response_related` | Record a deferred retrieval request. It can execute only in a subsequent explicitly-opened turn, never retroactively in the current bell turn. |
| `resident_selected` | Restrict automatic retrieval to explicitly selected source IDs/scopes. Missing selections produce no retrieval, with a receipt reason. |

`field_scan_v1` is an internal effective strategy, not a user-facing policy value. It is a bounded, deterministic, diverse selection across recent deltas, unresolved loops, active or recurring threads, fresh curation/receipts, relational changes, and under-visited shelves. It does not use fixed bell boilerplate as a search seed.

The resolver records both requested and effective policy. It identifies generic prompts through stable, reviewed bell configuration/category data and prompt-shape rules, never through control-plane boilerplate token matching.

### A.3 Context assembly contract

ContextAssembler receives a typed `RetrievalRequest` separate from `message.content`:

```python
@dataclass(frozen=True)
class RetrievalRequest:
    source: Literal["ordinary_message", "bell"]
    policy_requested: str
    policy_effective: str
    semantic_seed: str | None
    selected_sources: tuple[str, ...]
    control_plane_excluded: bool
```

Only `semantic_seed` and `selected_sources` reach dynamic retrieval adapters. The current bell invitation remains the final current-turn message after resident and orientation context, so it is attention-salient; dynamic retrieval is supplementary context, not a replacement for the bell.

An `ORIENTATION` state may add its existing orientation context before the bell. Bells do not force orientation. Receipts record the state and layer ordering so this behavior is inspectable.

### A.4 Provenance and budget receipts

Each dynamic retrieval lane reports:

```json
{
  "lane": "runtime_memory",
  "requested_policy": "auto",
  "effective_policy": "prompt_only",
  "semantic_source": "bell.resident_prompt",
  "query_terms": ["..."],
  "control_plane_excluded": true,
  "source_scope": ["runtime_memory"],
  "requested_budget_tokens": 1200,
  "returned_budget_tokens": 760,
  "included_budget_tokens": 650,
  "remaining_budget_tokens": 550,
  "omitted": [{"id": "…", "reason": "budget"}],
  "availability": "available",
  "result_count": 2
}
```

`availability` distinguishes `available`, `zero_results`, `unavailable`, and `error`. A transport timeout is not a zero-result outcome. “Included” means only that material was supplied to model context; it never asserts causal influence or adoption.

### A.5 No-change outcome

No response to a bell creates no new turn and therefore no curation candidate. An explicit no-change outcome uses the existing no-op control path and adds a bell outcome receipt:

```json
{
  "bell_outcome": "no_change",
  "curation_eligible": false,
  "memory_candidate_created": false,
  "preference_inferred": false
}
```

Natural-language text must not be heuristically reclassified as a no-change declaration. Only the explicit control/no-op mechanism grants that outcome.

### A.6 Runtime regression tests

- Equivalent prompts with changed IDs, schedules, purpose labels, and boilerplate produce the same semantic seed and dynamic retrieval request.
- Changing a resident prompt changes `prompt_only` retrieval terms.
- Generic look-around resolves `auto` to `field_scan_v1` and returns a bounded, deterministic receipt.
- `none` invokes no dynamic memory/archive adapter.
- `resident_selected` excludes unselected sources.
- Source timeout, disabled source, and zero-result search are separately represented.
- Explicit no-change suppresses curation and memory candidates.

## B. MCP snapshot-bound paging

### B.1 Browse session and cursor

The server creates a durable browse session at the first page. A signed cursor contains only stable, non-secret continuation claims:

```json
{
  "v": 2,
  "browse_id": "…",
  "source": "live",
  "path": "relative/path",
  "mode": "text",
  "offset": 65536,
  "page_bytes": 65536,
  "policy_scope": "archive.read_text:<policy-digest>",
  "expires_at": "2026-09-19T…Z",
  "signature": "…"
}
```

The durable session record stores source identity, normalized path/query, authorization-policy digest, snapshot descriptor, page size, start time, expiry, and the initial content SHA-256. Cursor integrity uses an HMAC key persisted in MCP state storage; a checksum alone is insufficient.

“Authorization scope” in this version means the effective server policy grant/digest. It is not represented as a human principal. A future principal-aware deployment may extend the session record without weakening these bindings.

### B.2 Snapshot verification

On initial browse, the server captures a content SHA-256, size, and source revision descriptor. On each continuation, it verifies the current target against the bound descriptor. If it differs, the tool returns a typed `file_changed_during_browse` condition and does not return mixed-revision bytes.

A valid continuation returns `snapshot_status: "same_snapshot"`. Expired, malformed, policy-mismatched, source-mismatched, and page-size-mismatched cursors fail explicitly and cannot silently restart at a new revision.

### B.3 Read surfaces

`archive.read_text` becomes a streaming UTF-8-safe reader for eligible text-like files regardless of total file size. It supports a bounded byte page and returns exact byte and line spans:

```json
{
  "text": "…",
  "byte_start": 0,
  "byte_end": 65536,
  "line_start": 1,
  "line_end": 842,
  "content_sha256": "…",
  "next_cursor": "…",
  "budget": {
    "requested_bytes": 65536,
    "returned_bytes": 65490,
    "truncated": true,
    "remaining_bytes": 1930042
  }
}
```

`archive.read_bytes` is a new bounded read-only surface for any allowed regular file, including database files. It returns base64 data plus exact byte spans, content hash, browse status, and the same budget shape. Its output cap accounts for base64 expansion.

Text decoding must not split a UTF-8 sequence. The returned byte offset always describes raw file bytes; line numbers are best-effort semantic aids and never replace offsets or hash evidence.

### B.4 Search and long artifacts

This sprint guarantees browse/read continuity for large files. Existing literal `archive.search_text` behavior remains explicit about files it did not scan. It must report skipped/limited files and scan budget, never imply exhaustive search. Streaming search across all large files is a later focused feature.

### B.5 MCP regression tests

- A UTF-8 text file over 1 MB is served in successive pages without admission rejection.
- Text page boundaries preserve decoded text and monotonically increasing exact byte/line spans.
- Byte paging returns a correct base64 slice from a non-text/SQLite fixture.
- A continuation whose source/path/scope/page size is altered is rejected.
- Expired and forged/signature-invalid cursors are rejected.
- Changing a browsed file between pages returns `file_changed_during_browse` and no mixed page.
- Budget fields distinguish request, return, truncation, and remaining content.
- Search reports oversize/scan-limit omissions rather than “no matches.”

## Delivery shape

The Runtime and MCP Server changes share vocabulary—provenance, omission, boundedness, and explicit unavailable-versus-empty outcomes—but keep their data models and test suites independent.

Implementation will use two plans:
1. Runtime bell retrieval/provenance/no-change plan.
2. MCP snapshot paging/large-artifact plan.

Both plans must use test-first implementation and preserve existing public behavior unless a new explicit field/tool contract replaces it.
