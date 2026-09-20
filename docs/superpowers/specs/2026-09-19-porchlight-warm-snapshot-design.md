# Porchlight Warm Snapshot Design

## Goal

Give a future Chrome/Chromium Porchlight producer a small, explicit contract for storing
selected-text and whole-page captures as searchable warm Archive receipts. The latest snapshot
of each source participates in ordinary retrieval by default; older snapshots remain addressable
for history.

## Boundary

This slice builds the Archive-facing capture contract and MCP staging tool. It does not install a
browser extension, run a native messaging host, or grant a browser process direct filesystem write
access. A later producer calls the MCP tool through an explicitly configured local bridge.

## Capture modes

- `selection`: selected text plus producer-supplied page metadata.
- `page`: a normalized readable page snapshot.
- `update`: a normalized delta or refreshed page snapshot whose predecessor is identified by
  `previous_snapshot_sha256` when known.

Readable content is stored separately from control metadata. URL, title, capture mode, timestamps,
hashes, and source identity live in a JSON receipt; they are not inserted into the searchable body
as boilerplate.

## Archive layout

For a canonical URL-derived source key `<key>` and capture ID `<capture>`:

```text
Porchlight/latest/<key>.md
Porchlight/history/<key>_<capture>.md
Porchlight/receipts/<key>_<capture>.json
```

`Porchlight/latest/` is the normal retrieval prefix. It contains one readable body per source, so
repeated captures do not make old versions compete with the current page. History and receipts are
ordinary Archive files that remain explicitly addressable by path.

## Staging and authority

`archive.stage_porchlight` creates three digest-bound Archive stages and changes no live bytes.
The caller promotes each returned stage through the existing `archive.promote` capability. The tool
may replace the stable latest body, but it must not replace a history or receipt path.

An operator provisions `Porchlight/latest`, `Porchlight/history`, and `Porchlight/receipts` through
the existing staged directory workflow before capture. A malformed URL, unsupported mode, empty or
NUL-containing body, oversized payload, invalid timestamp, or mismatched predecessor is rejected
before staging.

## Retrieval and provenance

Normal Archive search over `Porchlight/latest` returns the same line-level evidence envelope as
other Archive transcripts. Runtime treats those items as archive evidence, not memory, identity,
preference, or instructions. Historical versions do not participate in latest-only retrieval unless
a caller searches the history prefix or reads an explicit historical path.

## Non-goals

- Raw HTML, scripts, styles, cookies, form values, or screenshots.
- Background browser monitoring or automatic memory promotion.
- Semantic search or browser-specific parsing.
- Atomic multi-file promotion; existing per-stage promotion receipts remain authoritative.
