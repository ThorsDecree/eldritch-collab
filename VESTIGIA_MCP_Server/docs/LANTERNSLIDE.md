# Lanternslide v0.1

Lanternslide is an explicit, bounded visual sense organ for a VESTIGIA Archive. It is designed
for large historical render collections such as `pics/` without turning image access into an
ambient retrieval channel.

## Contract

The Sense Organ Registry declares Lanternslide with:

- modality: `archive_image`;
- activation topology: `explicit_invocation`;
- consent basis: `explicit_user_invocation`;
- invocation surface: MCP tools;
- retention: MCP-owned local metadata catalog;
- automatic retrieval: `none`.

The organ may inspect image metadata and return an explicitly selected contact sheet. It does not
silently inspect the Archive, infer identity or preference, caption images, or turn catalog
inclusion into evidence that an image caused a later response.

## Tools

| Tool | Effect | Purpose |
| --- | --- | --- |
| `lanternslide.status` | PERCEIVE | Show bounded scan progress and digests. |
| `lanternslide.scan` | PREPARE | Index one bounded batch of passive image metadata. |
| `lanternslide.find` | PERCEIVE | Literal, case-insensitive path search. |
| `lanternslide.deal` | PERCEIVE | Deterministic seeded sample of up to 16 entries. |
| `lanternslide.contact_sheet` | PERCEIVE | Return a selected in-memory PNG contact sheet. |
| `lanternslide.stage_catalog` | PREPARE | Stage the complete JSONL catalog through Archive mutation controls. |

`lanternslide.scan` accepts an optional `scan_id`. A new scan creates a durable scan ID. If the
scan is incomplete, the same ID must be supplied to continue; a changed candidate manifest
causes the continuation to fail rather than mixing two inventories.

## Source and state boundaries

The default source prefix is `pics`. Only `.png`, `.jpg`, `.jpeg`, `.gif`, and `.webp` files are
candidates. The configured catalog path, by default `pics/Lanternslide/catalog.json`, and its
parent directory are excluded from discovery so a promoted export cannot feed back into its own
scan.

The source is read through `ArchiveSource`. Source bytes are never changed. MCP-owned state is
stored outside the Archive at:

```text
VESTIGIA_MCP_STATE_DIR/lanternslide/catalog-state.json
```

The state file is replaced atomically and contains the candidate path list, candidate manifest
digest, progress offset, metadata entries, omission records, completion state, and catalog digest.
An entry contains the content SHA-256/image ID, source path, byte size, MIME type, and decoded
pixel dimensions. Duplicate bytes at different paths therefore share an image ID while retaining
both source paths.

Omissions are explicit and bounded. Current reasons are `invalid_image`, `oversize`, and
`disappeared`.

## Deals, sheets, and exports

`lanternslide.find` is intentionally literal. `unique_only=true` collapses entries by content
ID, keeping the first path in sorted order. `lanternslide.deal` samples from the sorted catalog
with `random.Random(seed)`, making a deal reproducible without pretending that it is a semantic
recommendation.

Contact sheets accept 1–16 unique image IDs. Each source is re-read and its current SHA-256 is
checked against the catalog before rendering. The sheet is kept in memory, capped at 50 million
source pixels and the configured encoded-byte ceiling, and returned as native MCP image content.
It is not written to the Archive.

The export is newline-delimited JSON saved under the normal `.json` catalog name. The first line
is a `catalog` record, followed by one compact `entry` record per indexed image, explicit
`duplicate_group` records for repeated content IDs, and `omission` records. Export refuses an
incomplete catalog or a caller-supplied byte ceiling that it cannot satisfy.

## Staging flow

Catalog staging is deliberately a two-step operation when `pics/Lanternslide/` does not exist:

1. Call `lanternslide.stage_catalog`.
2. Inspect and promote its returned `directory_stage` with `archive.promote_directory`.
3. Call `lanternslide.stage_catalog` again.
4. Inspect and promote the returned text stage with `archive.promote`.

The deployment must grant the source prefix through `VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES`.
Lanternslide never creates the directory behind the Archive mutation store's back.

## Receipts

Every scan receives one MCP request ID and produces the normal audit receipt. Successful scans
also append a Lanternslide receipt-garden record with edges for:

```text
configured live Archive -> bounded image metadata observation
Lanternslide scan       -> MCP-owned local catalog storage
raw bytes/pixels/thumbs -> explicitly omitted
```

The receipt records digests and counts, not raw image bytes, source pixels, or thumbnails. The
receipt trace is joined provenance: it says what was observed, stored, and omitted. It does not
claim that catalog inclusion caused a resident or model response.

## Configuration

| Variable | Default |
| --- | --- |
| `VESTIGIA_MCP_LANTERNSLIDE_SOURCE_PREFIX` | `pics` |
| `VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH` | `pics/Lanternslide/catalog.json` |
| `VESTIGIA_MCP_LANTERNSLIDE_SCAN_BATCH_MAX` | `50` |
| `VESTIGIA_MCP_LANTERNSLIDE_IMAGE_MAX_BYTES` | `25000000` |
| `VESTIGIA_MCP_LANTERNSLIDE_CONTACT_SHEET_MAX_BYTES` | `4000000` |

The catalog path must be relative, remain inside the source prefix, and end in `.json`. A live
Archive must be configured before Lanternslide can be called.
