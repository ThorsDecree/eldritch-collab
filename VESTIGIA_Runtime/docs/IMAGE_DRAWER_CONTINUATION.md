# Image Drawer Continuation and Bookmarks

Large image collections are browsed through private, stable collection snapshots rather
than a mutable integer offset. A page can be resumed across turns with an opaque cursor,
or preserved indefinitely with a resident-owned drawer bookmark.

## Governing boundary

> Browsing, continuing, and bookmarking are private organizational actions. They do not
> call the resident model, call an image provider, adopt memory, publish an artifact, or
> create outward-action authority.

The cursor and bookmark IDs are opaque database references scoped to the active resident.
They do not embed raw SQL, filesystem paths, credentials, or authorization claims.

## Starting a paged drawer

`browse` and `search` now create a stable snapshot and return one page of cards.

```json
{
  "action": "image.drawer",
  "mode": "browse",
  "limit": 20,
  "after": "continue"
}
```

```json
{
  "action": "image.drawer",
  "mode": "search",
  "query": "smug neon mall reaction",
  "pocket": "reaction-images",
  "limit": 12,
  "after": "continue"
}
```

The response includes:

- `current_cursor`
- `previous_cursor`
- `next_cursor`
- page number, page count, and ordinal range
- total materialized items
- snapshot and filter hashes
- the stable sort contract
- snapshot creation and cursor expiry times
- whether the operator snapshot ceiling truncated the collection

Snapshot membership is stored as normalized indexed rows, not one increasingly large JSON
array. Only the requested page is loaded into cards.

## Continuing

```json
{
  "action": "image.drawer",
  "mode": "continue",
  "cursor": "drawer_cursor_...",
  "after": "continue"
}
```

Ordinary cursors expire after the operator-configured window. An expired cursor fails closed
with a structured restart payload containing the original browse/search mode and filters.
It does not silently jump to an unrelated current offset.

## Holding a place

```json
{
  "action": "image.drawer",
  "mode": "bookmark",
  "cursor": "drawer_cursor_...",
  "label": "Mall reactions, page four",
  "note": "Resume after the escalator pictures.",
  "after": "continue"
}
```

A bookmark preserves the exact collection snapshot and page start. New images, renamed cards,
or changed search ranking do not move the bookmark's saved position.

List and reopen bookmarks:

```json
{
  "action": "image.drawer",
  "mode": "list_bookmarks",
  "after": "continue"
}
```

```json
{
  "action": "image.drawer",
  "mode": "open_bookmark",
  "bookmark_id": "drawer_bookmark_...",
  "after": "continue"
}
```

Remove a bookmark:

```json
{
  "action": "image.drawer",
  "mode": "remove_bookmark",
  "bookmark_id": "drawer_bookmark_...",
  "after": "continue"
}
```

Bookmark listings expose the query hash and filter provenance, not the raw search query.
The private session retains the explicit query only so the resident can reopen the exact
snapshot or receive a truthful restart suggestion.

## Stable ordering

Browse snapshots use:

```text
created_at descending, image_id descending
```

Search snapshots use:

```text
FTS rank ascending, card updated_at descending, image_id ascending
```

The sort version is included in every page receipt. Future sort changes therefore cannot
pretend to be the same snapshot contract.

## Missing images

If an image referenced by an old snapshot is no longer available, the page reports its ID in
`missing_image_ids` and continues with the remaining cards. It never substitutes a different
image into that ordinal.

## Operator ceilings

```yaml
images:
  drawer_max_page_size: 100
  drawer_snapshot_max_items: 50000
  drawer_cursor_seconds: 86400
```

The hard implementation ceilings are 100 cards per page, 100,000 items per snapshot, and
seven days for an ordinary cursor. A bookmark removes the cursor expiry for its preserved
snapshot until the last bookmark for that snapshot is removed.
