# Research CAS maintenance v0.1

Status: **development / stacked on Library Window v0.1**

The Research content-addressed store (CAS) deliberately writes inert raw/readable bytes before the SQLite source-custody transaction. That ordering prevents a committed source capsule from referring to bytes that were never durably written, but a process crash can leave an unreferenced CAS blob behind.

This maintenance surface reconciles those two layers without weakening source custody.

## Governing rules

1. **Database-referenced source custody wins.** A referenced blob that is missing, malformed, or hash-mismatched is an integrity error, never garbage.
2. **Doctor diagnoses; Doctor does not delete.** `vestigia doctor` may report Research CAS drift but never removes Research bytes.
3. **Only verified orphans are collectible.** Automatic GC candidates must have a valid `<sha256>.raw` or `<sha256>.txt` name, content matching that filename hash, no database reference, and sufficient age.
4. **Young unreferenced files are protected.** Library Window writes CAS bytes before its source-row transaction, so a fresh unreferenced blob may merely be between the filesystem and database phases.
5. **Malformed or corrupt unreferenced files are not auto-garbage.** They remain visible for operator investigation.
6. **GC is an offline maintenance action.** v0.1 requires the operator to assert that the VESTIGIA Runtime is stopped. This is an explicit operational contract, not a mechanically enforced cross-process lock.
7. **Apply is hash-bound.** GC uses a reviewed `plan -> apply` sequence. Any path, hash, size, mtime, reference, or candidate-set change invalidates the plan.
8. **Deletion is receipted without source content.** Maintenance receipts contain hashes, paths, sizes, outcome, and reclaimed bytes, never source text or raw bytes.

## Doctor surface

`vestigia doctor HOME` gains a `research_cas` section with:

- source-reference count;
- unique referenced CAS paths;
- verified referenced bytes;
- missing database-referenced blobs;
- referenced hash/size mismatches;
- invalid or escaping stored paths;
- aged valid unreferenced GC candidates;
- young grace-protected unreferenced blobs;
- corrupt unreferenced CAS files;
- malformed or unexpected entries;
- candidate byte total.

Doctor severity is intended to follow this order:

- **error**: missing referenced data, referenced hash mismatch, invalid referenced path, or CAS bytes present while the custody table is unavailable;
- **warning**: aged orphan candidates, corrupt unreferenced data, or unexpected entries;
- **ok**: referenced custody is intact and no maintenance anomaly requires attention.

Young grace-protected blobs are informational because they may represent a currently incomplete source write. Shared content-addressed blobs are verified and counted once even when multiple source capsules reference them.

## Plan

Default planning command:

```powershell
vestigia research-gc C:\path\to\home
```

The default grace interval is 24 hours. It may be adjusted deliberately:

```powershell
vestigia research-gc C:\path\to\home --min-age-hours 72
```

Planning does not delete anything. It returns a deterministic `plan_hash` bound to:

- the home fingerprint;
- configured grace interval;
- candidate relative paths;
- candidate content hashes;
- candidate byte sizes;
- candidate mtimes.

Referenced integrity errors are surfaced in the plan and block apply.

## Apply

After reviewing the plan, stop all VESTIGIA processes that may access that home. Then apply the exact plan:

```powershell
vestigia research-gc C:\path\to\home `
  --apply `
  --plan-hash "sha256:<reviewed-plan-hash>" `
  --runtime-stopped
```

`--runtime-stopped` is an operator assertion. v0.1 does not claim to discover or terminate every process that may hold the home open.

Immediately before deletion, apply:

1. rebuilds the plan and requires the same plan hash;
2. takes a SQLite `BEGIN IMMEDIATE` write lock;
3. re-reads all source references;
4. rejects any candidate that became referenced;
5. rechecks path containment and rejects symlinks;
6. rechecks size and mtime;
7. rechecks grace age;
8. re-hashes candidate bytes;
9. requires filename hash == byte hash;
10. deletes only the validated candidate set.

A maintenance receipt is written under:

```text
traces/maintenance/
```

The receipt records no remote source content.

## Failure semantics

### Process crash before source custody commit

Possible result:

```text
valid CAS bytes
+ no library_sources reference
= orphan candidate after grace interval
```

No resident-visible source capsule exists.

### Missing or corrupt referenced blob

Possible result:

```text
library_sources reference
+ missing/corrupt bytes
= Doctor ERROR
```

GC refuses to treat it as garbage.

### Plan changes before apply

Apply fails closed and requires a fresh plan.

### Partial filesystem deletion failure

Already deleted candidates remain deleted, deletion stops, and a `partial_failure` maintenance receipt records exactly what was removed. The operator must run Doctor and generate a fresh plan before any subsequent apply.

## Concurrency non-guarantee

`BEGIN IMMEDIATE` prevents concurrent database writers during the final reference check and deletion phase. It does **not** prevent an unrelated process from writing CAS bytes before attempting its own source-row transaction.

For that reason destructive v0.1 GC is explicitly an **offline maintenance procedure**. A future cross-process home maintenance lock may replace the operator assertion once it can be enforced consistently across CLI, Discord, Commander, research intake, restore, and other home writers.

## Future generalization

This first implementation is scoped to `research/sources/`. The classification and plan/apply contract can later become a generic House CAS maintenance primitive if other VESTIGIA subsystems adopt content-addressed storage. That generalization should preserve subsystem ownership and must not turn `doctor` into an implicit destructive repair command.
