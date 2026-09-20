# Bell Observatory

Bell Observatory is a Runtime-owned diagnostic surface for scheduled attention. It explains the
decision inputs and receipts around a bell without claiming to replay model cognition.

## Read-only actions

- `bell.runs.list` — list bounded runs, optionally filtered by bell or response state.
- `bell.run.inspect` — inspect one run's policy, semantic source, query terms, source results,
  omissions, orientation references, budgets, and response outcome.
- `bell.run.replay` — return stored decision inputs for a safe, non-outward replay harness.
- `bell.policy.preview` — resolve a requested bell policy before a run.

These actions are exposed to MCP only when Runtime's existing projection classifies them as
callable, confirmation-free, non-outward reads. MCP does not duplicate the bell ontology or open
the Runtime database.

## What a run records

A run links a bell ID, turn ID, context-receipt path, requested/effective policy, semantic source,
query terms, selected sources, control-plane exclusion, orientation references, source-level
inclusion/omission facts, budget accounting, and response state. Raw prompts and response bodies
are not copied into the observatory record; hashes and cited receipt paths remain available.

Bell boilerplate is control-plane metadata. `control_plane_excluded` must remain true for bell
retrieval, and the semantic query comes from the resident prompt or the explicitly selected
source policy. A `no_change` response is a first-class outcome with
`curation_eligible: false` and an explicit suppression reason.

## Replay boundary

Replay reconstructs stored decision inputs and returns `model_causality: not_replayed` plus
`outward_dispatch: false`. It can answer what the Runtime selected, omitted, and budgeted; it
cannot answer which token internally caused a model response.
