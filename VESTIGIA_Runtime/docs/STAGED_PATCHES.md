# Workspace Patch Staging

VESTIGIA Runtime can hold durable filesystem proposals without applying them.

The first staging surface deliberately stops before execution:

```text
observe -> propose -> inspect -> validate -> discard

not yet

observe -> propose -> apply
```

No capability named `fs.patch_apply` exists in this slice.

## Capabilities

### `fs.stage_patch`

Creates a resident-private draft for one workspace operation:

- `create`
- `edit`
- `delete`
- `move`

Paths must remain under the Runtime `workspace/` shelf and are also passed through the existing
workspace diff/path-safety machinery before a proposal is accepted.

Create/edit proposals store candidate text in Runtime-private draft state. List responses do not
return the stored candidate text. Delete/move proposals store the source base hash and operation
metadata without copying arbitrary source content into the draft.

Staging computes optimistic base/existence evidence but changes no workspace or canonical file.

### `fs.patch_list`

Lists staged/discarded proposal metadata and content hashes without exposing stored candidate text.

### `fs.patch_preview`

Recomputes the proposal against current workspace state using the existing Runtime `file.diff` and
`stat` handlers. Preview does not persist a new authority decision.

### `fs.patch_validate`

Checks whether the proposal's optimistic base preconditions still hold now:

- create target still absent;
- edit/delete source still present with the same hash;
- move source still has the same hash and destination remains absent.

Validation is observational and intentionally not durable approval. A future apply boundary must
validate again immediately before mutation.

### `fs.patch_discard`

Marks a staged proposal discarded while preserving the draft record. It does not delete or alter
the target workspace file.

## Move previews

The current proposal layer validates the source hash, source write boundary, destination absence,
and destination write boundary without reading/copying the full source file into patch state.

Therefore a move preview reports that a full destination-content diff is unavailable. A future
apply capability must re-read the source at dispatch time, verify the source hash again, verify the
destination again, then perform a contained move under the authority active at that moment.

## MCP projection

The current MCP Runtime adapter remains read-only. Runtime's live capability contracts therefore
produce this split automatically:

```text
projected through MCP:
  fs.patch_list
  fs.patch_preview
  fs.patch_validate

not projected through MCP:
  fs.stage_patch
  fs.patch_discard
```

The latter capabilities mutate Runtime-private draft state and are outside the current MCP read
effect ceiling.

This is intentional. Widening MCP from PERCEIVE to PREPARE should happen through the deployment
Keyring/authority model, not by special-casing these convenient tools.

## Receipts and canon

Every call still goes through `HousePort.dispatch()` and receives the ordinary Runtime receipt.
The staging capabilities additionally return explicit markers such as:

```text
proposal_only: true
workspace_changed: false
canonical_changed: false
apply_capability_available: false
```

A proposal may be durable without becoming canon. Validation may be true without becoming
approval. Receipt may exist without becoming autobiographical memory.
