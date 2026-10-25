# Counterfactual Memory Experiments

`memory.experiment` compares the provider's reply to the current turn with a reply made after excluding or replacing one Runtime memory that was fully present in that turn's retrieved continuity layer. It freezes the first provider prompt, changes only that memory block, and does not run retrieval again.

The experiment is available only in a local CLI or web conversation, or a one-to-one Discord DM. It is not available from a channel, group DM, bell, curation run, or an unknown interface. Each repetition makes two additional provider calls on the current model route; the maximum is three pairs (six calls). Returned action markers are rendered inert and never executed by the experiment runner.

## Run a comparison

Inspect the current turn's retrieval receipt first and choose a memory ID that is fully included in `retrieved_continuity`:

```text
[[TOOL_ACTION {"action":"retrieval.inspect","after":"continue"}]]
```

Exclude that memory:

```text
[[TOOL_ACTION {"action":"memory.experiment","mode":"run","memory_id":"memory_...","intervention":"exclude","repetitions":2,"after":"continue"}]]
```

Replace it with another eligible memory in the same resident and room:

```text
[[TOOL_ACTION {"action":"memory.experiment","mode":"run","memory_id":"memory_...","intervention":"replace","replacement_memory_id":"memory_...","repetitions":2,"after":"continue"}]]
```

The question is the user message from the current turn. Choose the turn you want to study before calling the experiment; the action does not accept a new question that would change the prompt.

The target must be complete, present in the baseline `retrieved_continuity` layer, and absent from other context layers. A replacement must be accepted, unexpired, unsealed, `hot` or `warm`, belong to the same resident and room, and not already appear in the prompt. A replacement that would exceed the existing memory-layer or total-context budget is rejected. Exclusion leaves the space empty; the Runtime does not fill it with another candidate.

## Review or remove capsules

```text
[[TOOL_ACTION {"action":"memory.experiment","mode":"list","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.experiment","mode":"inspect","experiment_id":"experiment_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.experiment","mode":"delete","experiment_id":"experiment_...","after":"continue"}]]
```

By default, the local capsule stores message and layer hashes, included memory IDs, provider/model details, response hashes, usage, arm order, comparison scores, and status. It does not store the exact prompt or full replies. Set `retain_context` to `true` for a run to retain the exact baseline and variant messages and complete replies in that capsule:

```text
[[TOOL_ACTION {"action":"memory.experiment","mode":"run","memory_id":"memory_...","intervention":"exclude","retain_context":true,"after":"continue"}]]
```

`inspect` returns the stored capsule; `delete` removes it. HousePort audit receipts keep the experiment ID, hashes, and status without prompt or reply bodies. The operator can disable the capability with `memory.experiments.enabled: false` in `home.yaml`.

## How to read the result

The service alternates which arm runs first across repetitions, uses the same provider route for both arms, and records per-arm provider, model, response ID, and usage. A pair includes a string-similarity ratio and response hashes. The ratio is a mechanical text comparison, not a quality score. Provider failures are recorded as partial or failed; the service does not retry.

Treat the result as observed output sensitivity under one frozen prompt and provider setup. A single memory intervention, even repeated, does not establish that memory caused an answer. It does not tune, adopt, revise, or delete memory records.
