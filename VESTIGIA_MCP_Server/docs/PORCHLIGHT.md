# Porchlight warm snapshots

Porchlight is the producer-facing contract for turning bounded browser context into searchable, warm Archive receipts. The current slice is browser-neutral: a future Chrome/Chromium extension or native host can produce the payload, while MCP handles validation and reversible Archive staging.

## Capture contract

`archive.stage_porchlight` accepts:

- `url` — an `http` or `https` source URL.
- `title` — a bounded human-readable page title.
- `content` — readable text only, never raw HTML.
- `mode` — one of `selection`, `page`, or `update`.
- `captured_at` — an optional timezone-aware ISO 8601 timestamp.
- `previous_snapshot_sha256` — an optional hash for optimistic concurrency on the latest file.

The URL is canonicalized before deriving a stable 24-character source key. The readable body is hashed and stored separately from the receipt. Bell IDs, scheduler text, extension metadata, and other control-plane fields do not belong in `content` and therefore do not become search terms through this contract.

`selection` is for a user-selected excerpt, `page` is for one bounded readable page snapshot, and `update` is for a later delta or refreshed snapshot. The server enforces a one-megabyte UTF-8 body limit and rejects NUL bytes, empty content, non-web URLs, malformed timestamps, and invalid previous hashes.

## Staged layout

The tool produces three independent staged text artifacts:

```text
Porchlight/latest/<source-key>.md
Porchlight/history/<source-key>_<capture-id>.md
Porchlight/receipts/<source-key>_<capture-id>.json
```

The latest body is the normal searchable warm source. History and receipts are explicit provenance shelves and should not be included in ordinary semantic retrieval by prefix policy. The receipt records the canonical URL, capture mode and timestamp, content hash and byte count, and the previous snapshot hash when supplied. It contains no control-plane prompt or raw HTML.

Before capture, an operator must provision these three parent directories through the existing staged directory workflow. `archive.stage_porchlight` creates no live directories and changes no canonical bytes. It returns three stage IDs; inspect and promote them individually with `archive.promote` after normal review and prefix-grant checks. A staging error after an earlier artifact has been accepted can leave that earlier artifact as a durable, inspectable stage, so callers should inspect or discard partial stages explicitly.

## Retrieval and browser boundary

This contract supports the intended Porchlight flow:

1. Capture selected text or a readable page snapshot.
2. Stage the body, history copy, and receipt.
3. Review provenance and hashes.
4. Promote the desired artifacts into the Archive.
5. Search `Porchlight/latest` like any other warm transcript source.

The current MCP server does not implement a Chrome extension, native messaging host, DOM reader, background page monitoring, or automatic memory promotion. Those belong to a later browser/local bridge and must preserve the same explicit capture mode, source identity, size bound, and Archive staging boundary.
