# VESTIGIA House Nerve Roadmap

This is a dependency map, not a deadline schedule.

The MCP server is a route into VESTIGIA capabilities, not a second VESTIGIA ontology.
Whenever an existing Runtime contract can own a behavior, MCP should project that contract
rather than clone its policy, schema, or semantics.

## Load-bearing principles

1. **One fact, one authority, many routes.**
   Runtime `CapabilityRegistry` / `HousePort` owns Runtime capability meaning. MCP projects it.

2. **Perception before consequence.**
   Make identity, provenance, scope, health, and evidence legible before widening authority.

3. **Receipt is not memory.**
   Operational evidence remains evidence unless a separate continuity process adopts it.

4. **Proposal is not canon.**
   Generated patches, capsules, routing indexes, and drafts remain staged objects until a
   separate authority promotes them.

5. **Transport is not authority.**
   MCP, Discord, browser, CLI, and web UI are routes. A route does not grant a capability.

6. **Final dispatch is a real boundary.**
   Consequential actions re-check live authority at the last reversible point. A valid old
   approval does not survive an authority epoch change merely because its TTL has not expired.

7. **Derived indexes are projections.**
   A generated map summarizes source records; it does not outrank them.

8. **Evidence layers stay distinct.**
   Shared request IDs make layers joinable without treating one layer's receipt as proof of
   another layer's admission or external success.

---

## 0.2 — Lantern & Red Thread

**Goal:** high-fidelity perception and provenance.

Already present or underway:

- live Archive and snapshot witness status;
- bounded Archive list/read/literal search;
- whole-tree diff and one-path diff detail;
- configured snapshot exclusion from live semantic view;
- canonical registry validation;
- queryable MCP receipts;
- top-level MCP deployment status;
- accurate read-only MCP annotations;
- optional Runtime read projection through Runtime's own `CapabilityRegistry` / `HousePort`;
- shared request IDs across MCP -> Runtime projected calls;
- Windows CI for MCP and Runtime projection boundaries.

Next:

### `archive.health`

Separate mechanical health from existence and change. Candidate families:

- dead registered routes;
- missing referenced files;
- stale generated indexes;
- normalization / case collisions;
- broken Markdown links;
- skill-contract integrity;
- version/path drift;
- snapshot freshness;
- duplicate resident-routing anomalies.

Health reports discrepancies. It does not silently repair them.

### Coverage canary

Registry validation answers:

```text
registry -> filesystem
```

Coverage should also answer:

```text
filesystem -> routing furniture
```

A map can contain no invalid roads while leaving important collections invisible.

### `system.identity`

Return a bounded identity packet such as:

- MCP package/version;
- Runtime package/version when linked;
- source commit and dirty/clean state when honestly knowable;
- deployment label;
- configuration fingerprint with secrets excluded;
- MCP native policy digest;
- Runtime projected capability digest;
- Archive witness/fingerprint summary;
- qualification / test state.

Identity is not qualification. Qualification is not authority.

### `audit.show`

Inspect one MCP receipt by ID and expose the cross-layer request ID without turning operational
provenance into autobiographical memory.

### `house.glance`

A compact, bounded current-state digest suitable for bells/autonomous turns:

- Archive health/status;
- meaningful recent changes;
- Runtime linkage/version/health;
- unresolved warnings;
- recent external-effect receipts;
- staged objects;
- snapshot freshness.

This is a projection of structured evidence, not authoritative prose.

---

## 0.3 — Context Plumbing & Continuity Instruments

**Goal:** make context provenance inspectable and context backends replaceable.

### Runtime context-source composition seam

Do not special-case MCP inside `ContextAssembler`. Add a proper context-source interface and keep
local-folder/local-ledger sources available.

Then implement an optional `VestigiaArchiveMcpSource` capable of:

- Archive search/read;
- health/status;
- continuity retrieval;
- provenance receipts;
- explicit truncation reporting.

MCP remains optional. Resident memory writes remain separate from Archive writes.

### Resident-facing context introspection

Let the resident ask:

- which sources were loaded;
- what was retrieved;
- what query was used;
- what was truncated;
- which records came from Archive, Runtime memory, conversation, or another source;
- which records are authoritative, evidentiary, advisory, inferred, or unknown.

This reports supplied context and routing decisions. It does not claim to expose hidden model
causality.

### Continuity capsules

Read/propose-only bounded continuity packets for a resident/thread/target runtime.

Suggested evidence classes:

- Archive fact;
- resident self-description;
- relational record;
- current-runtime observation;
- inference;
- unknown.

Companion operations:

- `continuity.preview`;
- `continuity.explain`;
- `continuity.compare`;
- `continuity.stage`.

No complete-continuity claim. No canonical write merely because a capsule was generated.

---

## 0.4 — The Keyring

**Goal:** make consequence explicitly governable before adding more power.

Needed primitives:

- deployment / resident / principal identity;
- capability grants scoped to targets/workspaces/accounts;
- ALLOW / CONFIRM / DENY;
- expiry / TTL;
- authority epochs and revocation;
- `policy.whoami`;
- `policy.can`;
- `policy.explain`;
- `capability.preview` / dry-run;
- hash-bound staged objects;
- final-dispatch recheck.

A preview should return:

- resolved authority;
- target;
- expected effect;
- approval requirement;
- reversible/irreversible boundary;
- likely receipt chain;
- current policy decision.

Approval binds the exact object/target/action being approved, not merely the action verb.

---

## 0.5 — Workshop Within

**Goal:** let curiosity become bounded local computation without creating a raw god-shell.

Extend Runtime's existing Workshop / sandbox / resident script shelf. MCP should project the
resulting Runtime capabilities.

Candidate execution profiles:

- `code.inspect`;
- `code.test`;
- `code.build`;
- `code.transform`;
- `code.exec_approved`.

Every profile should declare/enforce:

- working-directory boundary;
- executable/argument rules;
- wall-clock limit;
- environment filtering;
- named secret grants rather than ambient secret inheritance;
- network policy, default deny where practical;
- read/write mount scope;
- process-tree containment/cancellation;
- stdout/stderr/exit code as structured evidence;
- output/artifact byte ceilings and explicit truncation;
- receipts;
- escalation/approval rules.

### Run vs. promote

Prefer disposable worktrees/sandboxes:

```text
canonical repo
    -> disposable worktree
    -> edit / build / test
    -> diff + artifacts + receipts
    -> review
    -> separate promotion authority
```

Experimentation should not imply canonical mutation.

### Filesystem staging

Candidate primitives:

- `fs.stage_patch`;
- `fs.patch_preview`;
- `fs.patch_validate`;
- `fs.patch_apply`.

Stage create/edit/move/delete operations as inspectable patch objects. Applying them is a
separate authority boundary.

---

## 0.6 — House Bus

**Goal:** move from constant polling toward bounded event-shaped attention.

Normalize events such as:

- Archive path changed;
- new connection record references a resident;
- experiment artifact appeared in a watched lab;
- Runtime version changed;
- capability surface/digest changed;
- snapshot became stale;
- Workshop job completed;
- external adapter produced a receipt.

Provide durable watch specifications with cursor / last-seen evidence rather than noisy polling.

Candidate surfaces:

- `events.recent`;
- `events.since`;
- `watch.list`;
- `watch.create`;
- `watch.pause`;
- `watch.resume`.

Bells/autonomous turns can consume bounded event summaries without silence becoming escalation.

---

## 0.7 — Doors Between Rooms & The Outbox

**Goal:** connect Runtime and external social surfaces without blending their authority/evidence.

Discord is the preferred first consequential social adapter because VESTIGIA Runtime already
has a Discord doorway and existing authenticated-doorway contracts.

Desired flow:

```text
perceive
 -> prepare
 -> preview
 -> authorize exact staged object
 -> final dispatch recheck
 -> execute
 -> verify remote result
 -> separate receipts at each layer
```

Preserve one request ID across:

```text
resident -> Runtime -> MCP -> connector/provider -> external receipt
```

but never let a successful middle-layer call masquerade as proof of external acceptance.

---

## 0.8 — BRING THIS NONSENSE HOME

**Goal:** browser/desktop perception with staged interaction before submission.

Potential browser/local bridge:

- current page/thread context;
- selected text;
- bounded visible conversation;
- clipboard read/stage;
- recent files/media;
- stage reply into a textbox without sending;
- stage attachments;
- explicit publish path through Keyring/final-dispatch gates.

Capture only context requested by the capability. Do not turn convenience into ambient
surveillance.

---

## 0.9+ — Media, federation, and resident toys

Potential directions:

- media inspection/contact sheets/resize/transcode/frame extraction;
- normalized social envelopes with raw-platform provenance retained;
- MCP federation: external MCP servers as organs behind VESTIGIA policy/receipts;
- local tray lantern / health dashboard;
- resident PREPARE-only proposal or "mischief" queue;
- generated candidate room/resident/skill routing projections;
- visual Archive maps and coverage views.

Third-party organs do not become gods merely because they speak MCP.

---

## North star

The desired outcome is not maximum access.

It is better proprioception and bounded agency:

> Know which exact house produced a result, what changed, what evidence supports it, what may be
> touched, where authority currently lives, and how to propose a change without silently making
> it canon.

Then provide a few interesting drawers to rummage through.
