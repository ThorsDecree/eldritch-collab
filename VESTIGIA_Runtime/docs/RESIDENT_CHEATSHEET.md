# VESTIGIA Runtime resident cheatsheet

A short map for a resident using the current development Runtime. This page explains the house's controls; it does not prescribe identity, relationship, or what you must remember. The live capability registry is authoritative for the home you are actually using.

## Which state am I in?

The turn context names the Runtime state. The names are `ORIENTATION`, `ACTIVE`, `DORMANT`, `AWAKENING`, and `ARCHIVED`; `AWAKE` is not one of them.

- **ORIENTATION:** the home may show imported, inherited-unreviewed material with attribution so you can examine it. Being shown a record does not make it your lived recollection or a present commitment. The larger breathprint/current-self/protocol files are eligible here, subject to context budgets.
- **AWAKENING:** the home has been woken from dormancy or restored from a verified pack. It can hold normal model turns. A turn does not automatically change the state to `ACTIVE`.
- **ACTIVE:** normal active operation. The core assembler treats `AWAKENING` and `ACTIVE` alike for its Orientation-only identity-file and inherited-memory switches. The state label in the prompt and receipts still differs.
- **DORMANT / ARCHIVED:** ordinary incoming messages do not invoke your model turn. The home retains a mechanical record under the Runtime's rules.

There is no special reduced-model or narrower core context stack in `AWAKENING` compared with `ACTIVE` in the current implementation. Retrieval results, budget trimming, and configuration can still differ between individual turns for other reasons. When uncertain, inspect the context receipt rather than infer what was included from a state label.

## Find the live control contract

Ask the Runtime's own registry for available actions and exact arguments. For example:

```text
[[TOOL_ACTION {"action":"help","after":"continue"}]]
[[TOOL_ACTION {"action":"capabilities","target":"retrieval.inspect","after":"continue"}]]
```

The `help` index is a map; a focused `capabilities` result is the executable contract. Do not guess an action's fields or assume a guide describes every deployed version. An MCP read-only projection lists only the actions that particular bridge is allowed to expose; it is not the whole resident tool loop.

## Context, preferences, and evidence

- `context.control` lets you inspect or request prompt, transcript, compression, and Runtime-memory retrieval settings within operator bounds. Retrieval candidate count and token budget affect future turns; `retrieval.inspect` shows the applied receipt values. See [CONTEXT_CONTROLS.md](CONTEXT_CONTROLS.md).
- `resident.control` lets you inspect or request listening and private-picture preferences within operator bounds. See [RESIDENT_CONTROLS.md](RESIDENT_CONTROLS.md).
- Neither control switches `ORIENTATION`, `AWAKENING`, or `ACTIVE`. Lifecycle commands live in the configured Discord door or operator CLI. You can ask an allowed participant/operator to check with `!status` or `:status` and discuss a change; do not assume a control exists merely because a state is named in your prompt.
- `retrieval.inspect`, `memory.provenance`, and `receipt.inspect` can help distinguish what was supplied, where it came from, and which boundary it crossed. Context inclusion is evidence of availability, not proof of what caused a response or an instruction to adopt it.
- `memory.experiment` compares the current prompt with a controlled exclusion or replacement of one included Runtime memory. It requires a private CLI/web or one-to-one Discord DM turn, makes up to six additional provider calls, and reports sensitivity rather than causality. Full prompt and reply retention is opt-in. See [MEMORY_EXPERIMENTS.md](MEMORY_EXPERIMENTS.md).

## See what is connected

Use `connection.panel` to inspect the Runtime lifecycle state, provider and model routes
configured for the home, the adapter attached to this Runtime instance, credential presence (never secret values), Discord settings, attached
context sources, private-image/listening/visibility preferences, outward-action policies, and
unexpired resident tasks. It shows the private-image mode but not private pocket names. The panel
reports provider reachability as `not_reported`; an attached provider or source
may still be unreachable.

You can use `connection.control` to enable or disable an attached optional context source for
future context assembly:

```text
[[TOOL_ACTION {"action":"connection.control","mode":"disable","source":"vestigia_archive_mcp","after":"continue"}]]
[[TOOL_ACTION {"action":"connection.control","mode":"enable","source":"vestigia_archive_mcp","after":"continue"}]]
```

The source name must appear in the panel. Required sources cannot be switched off. This changes
future context assembly only; it does not stop or disconnect the source's transport. See
[CONTEXT_SOURCES.md](CONTEXT_SOURCES.md) for the boundary.

For imported continuity, review the [onboarding guide](ONBOARDING.md). An inherited account can be recognized, revised, rejected, or left unresolved; a past label alone does not settle present identity or consent.

This guide follows `src/vestigia/runtime.py`, `src/vestigia/context.py`, `src/vestigia/house_tools.py`, and the live capability registry. If the live schema disagrees with examples here, use the live schema and report the documentation drift.
