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

6. **Preview is not approval.**
   A schema-valid candidate and its hash are evidence about what was previewed. They do not mint
   authority to execute it.

7. **Final dispatch is a real boundary.**
   Consequential actions re-check live authority at the last reversible point. A valid old
   approval does not survive an authority epoch change merely because its TTL has not expired.

8. **Derived indexes are projections.**
   A generated map summarizes source records; it does not outrank them.

9. **Evidence layers stay distinct.**
   Shared request IDs make layers joinable without treating one layer's receipt as proof of
   another layer's admission or external success.

---

## 0.2 — Lantern & Red Thread

**Goal:** high-fidelity perception and provenance.

Implemented:

- live Archive and snapshot witness status;
- bounded Archive list/read/literal search;
- whole-tree diff and one-path diff detail;
- configured snapshot exclusion from live semantic view;
- canonical registry validation;
- mechanical `archive.health` diagnostics and coverage canaries;
- queryable recent MCP receipts and `audit.show`;
- `system.identity` and top-level MCP deployment status;
- compact `house.glance` orientation;
- accurate read-only MCP annotations;
- optional Runtime read projection through Runtime's own `CapabilityRegistry` / `HousePort`;
- shared request IDs across MCP -> Runtime projected calls;
- Windows CI for MCP and Runtime projection boundaries.

Still worth deepening:

- stale generated-index diagnostics;
- skill-contract integrity checks;
- stronger snapshot-freshness evidence;
- more complete orphan/unindexed routing diagnostics;
- teach `house.glance` to report newly merged Runtime staged-patch state instead of its older
  hard-coded unsupported marker;
- bounded recent-change views that do not require whole-tree rehashing.

Health reports discrepancies. It does not silently repair them.

---

## 0.3 — Context Plumbing & Continuity Instruments

**Goal:** make context provenance inspectable and context backends replaceable.

Implemented:

- a proper Runtime `ContextSource` composition seam rather than an MCP special case inside
  `ContextAssembler`;
- Runtime memory represented as the required compatibility ContextSource;
- optional `VestigiaArchiveMcpSource` over a local stdio MCP child;
- allowlisted child environment that does not inherit Runtime/provider/Discord/tunnel secrets;
- bounded Archive resident-anchor reads and literal search;
- Runtime-owned external-source item/token ceilings;
- source-neutral context receipt v0.2 with availability, query, provenance/authority class,
  truncation, warnings, included/omitted evidence, and explicit no-adoption/no-memory-write flags;
- resident-facing source introspection through the existing `retrieval.inspect` capability.

Important invariant:

```text
source evidence -> attributed prompt layer -> context receipt

not

source evidence -> automatic Runtime memory / identity / canon
```

Remaining major instrument:

### Continuity capsules

Read/propose-only bounded continuity packets for a resident/thread/target runtime.

Suggested evidence classes:

- Archive fact;
- resident self-description;
- relational record;
- current-runtime observation;
- inference;
- unknown.

Potential operations:

- `continuity.preview`;
- `continuity.explain`;
- `continuity.compare`;
- `continuity.stage`.

No complete-continuity claim. No canonical write merely because a capsule was generated.

---

## 0.4 — The Keyring

**Goal:** make consequence explicitly governable before adding more power.

### v0.1 — descriptive preflight

Initial Runtime-owned slice implemented on the Keyring feature branch:

- resident principal identity with route/deployment evidence when available;
- durable per-resident `authority_epoch`, seeded to `1`;
- `policy.whoami`;
- `policy.can`;
- `policy.explain`;
- `capability.preview`;
- coarse `perceive` / `prepare` / `act` / `unknown` classification derived from the existing
  `CapabilitySpec.effects` records;
- exact candidate validation through Runtime's existing JSON-schema validator;
- canonical SHA-256 over previewed candidate payloads without echoing arbitrary candidate content;
- bounded target/scope summaries;
- explicit `legacy_unkeyed_authority` findings for ACT-class capabilities that current Runtime
  contracts still admit with `confirmation=none`;
- read-only projection of all four Keyring senses through the existing generic MCP Runtime bridge.

v0.1 is intentionally **descriptive**. It does not execute a target capability's focused
authorizer or handler, create an approval, mutate the epoch, or change existing Runtime dispatch
semantics.

### Next enforcing slice

Needed primitives:

- durable grants scoped by principal/deployment and target/workspace/account;
- ALLOW / CONFIRM / DENY policy rules over those grants;
- grant expiry / TTL;
- authority-epoch increment on grant/revocation changes;
- hash-bound approvals that include payload + action + destination/account + authority epoch;
- an approval/introspection surface that never exposes secret material;
- final-dispatch recheck at the last reversible boundary;
- explicit migration of existing ACT capabilities behind the general Keyring gate.

A future approval must bind the exact object/target/action being approved, not merely the action
verb. Preview remains evidence, not approval.

The staged-patch system is the preferred first enforcement guinea pig because PREPARE state is
reversible and Runtime-private. Do not introduce `fs.patch_apply` until the enforcing Keyring
boundary is real and tested.

---

## 0.5 — Workshop Within

**Goal:** let curiosity become bounded local computation without creating a raw god-shell.

Already present:

- Runtime Workshop/script shelf;
- static inspection and lifecycle evidence;
- bounded sandbox/process-runner substrate;
- Resident Workbench;
- staged workspace patch objects:
  - `fs.stage_patch`;
  - `fs.patch_list`;
  - `fs.patch_preview`;
  - `fs.patch_validate`;
  - `fs.patch_discard`;
- no `fs.patch_apply` authority yet.

The current read-only MCP Runtime projection exposes patch inspection but not staging/discarding.

Next execution-profile work should extend Runtime's existing Workshop rather than add raw MCP
shell access.

Candidate profiles:

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

---

## 0.6 — House Bus

**Goal:** move from constant polling toward bounded event-shaped attention.

Normalize events such as:

- Archive path changed;
- new connection record references a resident;
- experiment artifact appeared in a watched lab;
- Runtime version changed;
- capability surface/digest changed;
- authority epoch changed;
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
