# VESTIGIA MCP — Proprioception surfaces

Proprioception is evidence about the deployment itself: what house answered, what is mechanically
crooked, what Runtime is linked, what receipts exist, and which parts of the map remain advisory.
It is not autobiographical memory and does not confer authority.

## `archive.health`

`archive.health` is a read-only diagnostic surface. It currently checks:

- canonical `00_Bootloader/house_index.json` target existence;
- duplicate registry targets as aliases/ambiguities, not automatically defects;
- top-level routing coverage candidates (`filesystem -> registry` canary);
- case-fold path collisions;
- bounded local Markdown links, including links that would escape the Archive root;
- a low-trust source/container timestamp.

A coverage candidate is not proof that a collection needs a route. Generated routing furniture is
descriptive and never outranks source records.

The endpoint explicitly lists deferred check families. In v0.1 these include semantic stale-index
analysis, skill-contract integrity, cross-version path drift, duplicate resident-routing semantics,
and semantic snapshot freshness. Container/directory modification time is not presented as proof
that a snapshot is semantically current.

`house.glance` runs the quick health path with Markdown-link scanning disabled so an autonomous
orientation call does not unexpectedly turn into a whole-house text crawl. Call `archive.health`
for the deeper flashlight pass.

## `system.identity`

`system.identity` returns a bounded identity packet:

- MCP package version and deployment label;
- non-secret effective-config fingerprint;
- executable MCP policy digest;
- bounded Archive witness digests over stats plus `manifest.md` and the canonical registry;
- Runtime linkage/status when configured;
- source commit/state only when explicitly embedded by operator/build metadata;
- an explicit qualification status and its evidence limits.

It deliberately does **not** invoke `git` or a shell to manufacture a dirty/clean claim. If the
source commit/state was not embedded, that fact remains unknown.

The Archive witness digest is not a whole-Archive content hash. Its scope is returned explicitly.
Call the dedicated diff/fingerprint surfaces when stronger content evidence is required.

Identity is not qualification. Qualification is not authority.

## `audit.show`

`audit.show(event_id)` inspects one MCP audit receipt by durable event ID. New receipts include:

- capability;
- effect class;
- live policy decision;
- deciding authority label;
- argument SHA-256 rather than raw arguments;
- outcome;
- optional cross-layer `request_id`;
- deployment and timestamp.

Older JSONL records remain readable even when they predate a newly added optional field.

A receipt is operational provenance, not memory. Nothing in the audit surface promotes a receipt
into resident continuity.

## `house.glance`

`house.glance` is intentionally compact and descriptive. It is suitable as a first call for a
bell/autonomous orientation pass. It returns:

- live/snapshot availability, stats, and low-trust source clocks;
- quick Archive-health summary;
- Runtime linkage/version/projection status;
- recent MCP receipts and recent errors;
- unresolved warnings;
- explicit placeholders for meaningful-diff, staged-patch, and watch/subscription surfaces that
  are not yet implemented.

It does **not** run a whole-tree `archive.diff`, because hashing an 8 GB live house on every bell
would be terrible proprioception ergonomics. The result tells the caller which focused tool to use
when stronger change evidence is needed.

## Host tool-catalog cache membrane

MCP hosts may cache or snapshot discovered tool descriptors independently of the server process.
A restarted VESTIGIA MCP server can therefore have a newer executable policy surface than a
particular host/thread currently displays.

`vestigia.status` includes a small cache-membrane canary:

- live executable policy capability count/list;
- the names of newly deployed proprioception tools;
- a note that host-visible descriptors may lag the live server.

This gives two distinct evidence layers:

```text
host-visible tool catalog
!= necessarily ==
live VESTIGIA MCP executable policy surface
```

The mismatch is diagnosable evidence. It is not silently treated as server failure or as proof the
host has refreshed.
