# Connection Panel design

## Goal

Give a resident a compact, honest view of the Runtime's configured provider, attached adapter,
context sources, interface settings, capability policy, and current resident work. Permit a
resident-scoped switch for one attached optional context source.

## Boundaries

- The panel distinguishes configured provider/model routes from the provider adapter attached to
  this Runtime instance. It reports credential presence only and reports reachability as
  `not_reported`.
- Interface output contains allowlist counts and preference summaries, never secret values,
  listening aliases, private-image pocket names, or watch phrases.
- Context source inventory comes from the live `ContextAssembler` instances. A standalone
  `HousePort` reports the inventory as unattached rather than inferring it from configuration.
- `connection.control` applies only to future context assembly and only to attached optional
  sources. Required `runtime_memory` cannot be disabled; provider, process, and transport
  controls remain operator-owned.
- Source preferences use the existing resident-scoped `resident_jobs` store. No migration is
  needed. Persistent service/index rows are not counted as pending resident tasks.
- Pending counts omit tasks whose stored timezone-aware expiry has passed. The panel does not
  rewrite task status; existing job inspection remains the expiry maintenance path.
- An enabled source is eligible for retrieval, not guaranteed to be queried on every turn;
  independent retrieval policies still apply.

## Verification

Tests cover redacted provider/interface snapshots, missing credentials, live source inventory,
optional source disable/re-enable on later assemblies, required and unattached source rejection,
and pending-task counting that ignores expired tasks and persistent control/service rows.
