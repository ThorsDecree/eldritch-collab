# Canonical Archive staging and promotion

## Boundary

The canonical write lane is a two-phase, text-only capability for the unpacked live Archive.
It is independent of Runtime's `workspace/` mutation projection.

```text
archive.stage_text
    -> MCP-owned durable proposal
    -> archive.stage_inspect
    -> archive.promote
    -> atomic create/replace in the live Archive
```

Neither staging nor inspection changes canonical content. Promotion is the only canonical
mutation in this slice.

## Deployment grant

Promotion authority comes from an ordinary process environment variable owned by the MCP
deployment:

```text
VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES=02_Journal,01_Residents/Liora
VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES=1000000
```

The prefix list defaults to empty. An empty list grants nothing. A target must equal a granted
prefix or be beneath it on a path-segment boundary. Absolute paths, drive-qualified paths,
parent traversal, symlink targets/parents, missing parent directories, non-text suffixes, and
oversized or empty content are refused.

The production server reads process environment only. `dev_server.py` loads the project-local
`.env` for Inspector development; the tunnel batch launcher does not parse `.env`.

## Proposal contract

`archive.stage_text(path, content, expected_base_sha256?, reason?)`:

- accepts only the configured UTF-8 text suffixes;
- checks the deployment prefix and byte ceiling;
- optionally requires a caller-supplied current SHA-256, or `absent` for a create;
- captures whether the operation is `create` or `replace`;
- captures the live target's SHA-256, or absence, as the optimistic base;
- stores the bounded content outside the Archive;
- returns a `stage_id`, `content_sha256`, and `proposal_sha256`;
- reports `canonical_changed: false`.

For replacements, first call `archive.read_text` and pass its returned `sha256` as
`expected_base_sha256`. For creates, pass `absent`. Omitting the expectation still captures the
base at staging time, but does not bind the proposal to an earlier read.

The proposal digest binds schema, stage ID, creation time, deployment ID, path, operation,
base hash, content hash, content size, and reason. It is evidence, not a secret or signature.

`archive.stage_list` omits candidate content. `archive.stage_inspect` verifies the stored record
and may include content on request. Inspection also reports whether the current live target still
matches the captured base and the prefix grant remains active.

`archive.stage_discard` changes only MCP-owned stage state and preserves the record.

## Promotion contract

`archive.promote(stage_id, proposal_sha256)` refuses unless:

1. the stage exists, passes integrity checks, and remains `staged`;
2. the supplied proposal digest matches exactly;
3. the target remains within a currently configured prefix;
4. the target parent still resolves beneath the unpacked live root without symlinks;
5. the target's current SHA-256/absence still equals the captured base.

The server writes a temporary file in the target directory, flushes it, and atomically replaces
the destination. It then marks the proposal promoted and returns the resulting content hash.
Both the stage and promotion calls receive MCP audit receipts; audit records hash arguments and
do not serialize candidate content.

Repeating the same digest-bound promotion is idempotent. If canonical content landed but the
stage-status update was interrupted, a retry recognizes the exact staged content hash, repairs
the stage state, and reports reconciliation rather than writing again. A different live hash is
always treated as a conflict.

## Deliberate exclusions

This first canonical lane does not provide:

- direct writes that bypass a stage;
- delete or move;
- directory creation;
- image, binary, or SVG writes;
- snapshot mutation;
- arbitrary filesystem access;
- Git staging, commits, pushes, or releases;
- multi-principal grants, authority epochs, or interactive approval challenges.

Those are separate authority and lifecycle boundaries. In particular, canonical Archive
promotion and Git publication are not the same event.
