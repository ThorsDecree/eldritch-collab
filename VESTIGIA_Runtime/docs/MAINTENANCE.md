# VESTIGIA Runtime maintenance surfaces

Status: **development / v0.8 line**

Maintenance operations are intentionally separate from resident/provider capabilities. A diagnostic command must not quietly acquire destructive authority merely because it discovered something repairable.

## Current contract

- `vestigia doctor HOME` inspects Runtime, database, backup, operation, dependency, and Research CAS health. Doctor may refresh derived indexes where already documented, but Research CAS inspection is non-destructive.
- `vestigia research-gc HOME` produces a non-destructive, hash-bound Research CAS garbage-collection plan.
- `vestigia research-gc HOME --apply --plan-hash <hash> --runtime-stopped` is an explicit offline operator maintenance action. It deletes only the exact still-valid orphan set from the reviewed plan and emits a content-free maintenance receipt.

Research CAS details and failure semantics are defined in [`RESEARCH_CAS_MAINTENANCE.md`](RESEARCH_CAS_MAINTENANCE.md).

## Maintenance design rule

Prefer:

```text
detect -> explain -> plan -> explicit authorization -> revalidate -> mutate -> receipt
```

over implicit repair.

A future home-wide maintenance lock may strengthen offline operations, but it must cover every home writer before any command claims cross-process exclusivity.
