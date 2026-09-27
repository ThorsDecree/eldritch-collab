# House Mechanic Task-Scoped Source Operations — Design

Status: proposed design, user-reviewed in conversation  
Date: 2026-09-27  
Scope: VESTIGIA House Mechanic + existing stable MCP `dev.*` projection

## Purpose

Add the smallest source-editing authority needed for a VESTIGIA resident or dev agent to perform real bounded work inside a House Mechanic task worktree without gaining arbitrary host filesystem or shell access.

The immediate proving task is the VESTIGIA Runtime Archive-MCP retrieval bug where prompt-term selection currently favors the earliest surviving words and can starve later high-signal terms. The broader intent is to make House Mechanic a practical workbench for residents who want to build new repo-local tools, experiments, or personal projects as well as repair existing code.

## Design intent

The source-editing surface should preserve these invariants:

1. House Mechanic, not Git, is the host write-authority boundary.
2. Git provides isolation, provenance, exact commits, reviewability, and rollback.
3. Archive write-prefix permissions do not govern House Mechanic task worktrees.
4. A caller never supplies an arbitrary host path, worktree path, shell command, argv, cwd, or environment.
5. Reads are cheap and do not consume an edit iteration.
6. Writes happen only inside an active bounded iteration.
7. Every write is previewed first.
8. The applied patch is exactly the reviewed proposal.
9. Multi-file writes are all-or-nothing.
10. Newly created text files are first-class, because residents must be able to build new things rather than only repair existing files.
11. Supervisor self-modification remains distinct from proposing changes to supervisor source in a disposable worktree.

## Architecture

House Mechanic gains three canonical task-scoped operations:

- `task.read`
- `task.diff`
- `task.patch`

They are projected through the existing stable MCP surface. No new top-level ChatGPT/plugin descriptors are required: discovery remains `dev.capabilities`, mutations continue through `dev.call`, and observation remains available through `dev.logs` / `dev.process`.

The operations act only on the worktree already issued by `task.acquire`. The caller identifies the task and presents the normal task authority tuple; House Mechanic resolves the worktree internally from the durable task record.

## Repository and filesystem authority

House Mechanic may write only beneath operator-configured Git repositories and only through worktrees it created for active tasks.

The normal deployment model separates:

- House Mechanic source/launcher location;
- the mutable Git development checkout used as House Mechanic's configured repository root;
- Archive storage and Archive write-prefix grants.

A recommended local arrangement is a real Git clone such as:

`C:\Workspace\S9\EchoJeff\EchoProjects\GARDEN\eldritch-collab-dev`

The Archive mirror remains an Archive artifact and is not used as the tasking repository.

The configured Git checkout is not itself freely writable through MCP. It becomes writable only through House Mechanic's task/worktree authority model.

## Operation: task.read

### Purpose

Read bounded UTF-8 source text from an active leased task worktree for investigation.

### Admission

Requires:

- configured tasking;
- known `task_id`;
- current `holder_id`;
- current `authority_generation`;
- task state admitting read access;
- live task worktree;
- requested relative paths confined beneath that worktree.

`task.read` does not require an open iteration and does not consume the iteration budget.

### Input shape

Conceptually:

```json
{
  "task_id": "hm_task_...",
  "holder_id": "vestigia-chatgpt",
  "authority_generation": 1,
  "paths": [
    "VESTIGIA_Runtime/src/vestigia/mcp_context_source.py",
    "VESTIGIA_Runtime/tests/test_mcp_context_source.py"
  ]
}
```

### Result

For each path, return bounded text plus metadata sufficient for later optimistic concurrency:

- normalized repo-relative path;
- size;
- SHA-256;
- existence;
- UTF-8 classification;
- truncation state when a read ceiling is reached.

No raw host-absolute path needs to cross the MCP boundary.

## Operation: task.diff

### Purpose

Create an immutable preview proposal for one atomic multi-file mutation set.

### Admission

Requires everything `task.read` requires plus:

- active task lease;
- active iteration created by `iteration.begin`;
- matching `iteration_id`.

### Mutation types in this slice

Two mutation types are supported:

1. **modify existing text file**
   - normalized relative path;
   - expected SHA-256 of the current file;
   - exact old text;
   - exact new text.

2. **create new text file**
   - normalized relative path;
   - explicit `expected_state = "absent"`;
   - complete UTF-8 content.

Deletion, rename, binary writes, symlink writes, special-file writes, and caller-selected host paths are intentionally out of scope for this first slice.

### Validation

`task.diff` validates the entire mutation set before creating a proposal:

- task/holder/generation/iteration authority;
- path normalization;
- no absolute paths;
- no `..` escape;
- final resolved path remains inside the task worktree;
- no symlink traversal;
- no special files;
- existing files must be regular UTF-8 text files;
- create targets must not already exist;
- modify targets must match the supplied expected SHA-256;
- exact old text must occur according to the operation's replacement rules;
- duplicate or conflicting paths in the same set are refused;
- per-file and total payload limits are enforced.

Suggested initial operator ceilings:

- maximum 32 file mutations per proposal;
- maximum 1 MiB per resulting text file;
- maximum 4 MiB total patch payload.

These are configuration ceilings, not permanent protocol constants.

### Proposal record

A successful `task.diff` creates an immutable durable proposal containing at least:

- `proposal_id`;
- `proposal_digest`;
- `task_id`;
- `holder_id`;
- `authority_generation`;
- `iteration_id`;
- ordered mutation set;
- exact recorded pre-state for each path;
- rendered unified diff;
- creation timestamp;
- proposal state, initially `ready`.

The digest binds the ordered proposal contents and relevant authority/provenance fields.

The preview is evidence only. `task.diff` does not write source files.

## Operation: task.patch

### Purpose

Apply exactly one previously created immutable proposal.

### Input

Conceptually:

```json
{
  "task_id": "hm_task_...",
  "holder_id": "vestigia-chatgpt",
  "authority_generation": 1,
  "iteration_id": "hm_iteration_...",
  "proposal_id": "hm_patch_...",
  "proposal_digest": "..."
}
```

The caller does not resend mutable patch content.

### Revalidation

Before any source write, House Mechanic revalidates:

- task, holder, generation, lease, and iteration;
- proposal belongs to this exact task/generation/iteration;
- proposal digest matches;
- proposal is still unused and `ready`;
- every recorded modify pre-hash still matches;
- every create target is still absent;
- every target still resolves safely within the task worktree;
- file-type and size constraints still hold.

If any precondition fails, the whole operation fails and writes nothing.

### Atomicity

Patch sets are all-or-nothing.

House Mechanic must not leave a partially mutated worktree when one file in the set fails validation or application.

Implementation may use temporary files plus same-filesystem replacement and/or an explicit rollback journal, but the externally visible contract is atomic success or zero source changes.

On success:

- all mutations are applied;
- created parent directories may be created as needed beneath the worktree;
- the proposal becomes `consumed`;
- post-state SHA-256 is recorded for every mutated path.

A consumed proposal cannot be applied again.

## Proposal invalidation

A proposal is unusable when any of the following is true:

- authority generation changed;
- iteration is no longer the active iteration;
- task no longer admits mutation;
- lease expired before admission;
- proposal was already consumed;
- proposal digest does not match;
- any worktree precondition drifted;
- referenced path no longer resolves safely;
- expected present/absent state changed.

House Mechanic does not adapt stale proposals. The caller must re-read and create a new diff proposal.

## Iteration semantics

The intended development loop is:

```text
task.read
  -> task.read
  -> iteration.begin
  -> task.diff
  -> task.patch
  -> compile/test recipe
  -> inspect result
  -> [optional new task.diff -> task.patch within same iteration]
  -> iteration.checkpoint
```

Reads do not consume an iteration.

`task.diff` and `task.patch` require an active iteration because they represent an actual edit attempt subject to the task iteration ceiling.

`iteration.checkpoint` remains the Git-history boundary. Its existing `git add -A` behavior means newly created files become ordinary tracked Git history at checkpoint time.

## Git and provenance

Git is not treated as the security boundary by itself.

House Mechanic enforces where disk writes are permitted. Git contributes:

- disposable task worktrees;
- branch isolation;
- base-commit provenance;
- clean/dirty state;
- local checkpoint commits;
- exact candidate deployment commits;
- later PR review and merge workflow.

A task may create a new directory such as `Vesti/` and new source files beneath it, provided all paths remain inside the issued worktree and the patch passes normal limits and proposal validation.

## Supervisor self-modification boundary

A task may propose and patch files under `VESTIGIA_House_Mechanic/` inside its disposable task worktree if the configured repository contains that source.

That does **not** grant authority to modify the currently running House Mechanic process, installation, launcher, token, deployment configuration, or supervisor process state.

Source proposal authority and running-supervisor authority remain separate.

Deployment or restart of House Mechanic itself remains outside this slice.

## Receipts and evidence

This surface should preserve the project's evidence-first conventions.

### task.read evidence

Record bounded metadata such as:

- request ID;
- task ID;
- authority generation;
- requested normalized paths;
- returned hashes/sizes;
- truncation flags;
- outcome.

Raw source text need not be duplicated into the durable receipt if that would create unnecessary storage or privacy exposure.

### task.diff evidence

Record:

- proposal ID and digest;
- task/generation/iteration binding;
- ordered path list;
- mutation type per path;
- pre-state hash or absent-state assertion;
- rendered-diff hash;
- limits applied;
- outcome.

### task.patch evidence

Record:

- proposal ID and digest;
- successful revalidation;
- ordered mutated paths;
- pre-state and post-state hashes;
- atomic outcome;
- proposal state transition to consumed;
- failure category if refused.

The same MCP request ID should continue to join MCP policy/audit evidence with House Mechanic durable receipts.

## API and capability projection

The House Mechanic capability catalog should advertise the three operations with fixed method/path and closed input schemas.

Suggested operation properties:

- `task.read`
  - mutation: false
  - effect: bounded_worktree_read
  - enabled only when tasking is configured

- `task.diff`
  - mutation: true, because it creates durable proposal state even though source files remain unchanged
  - effect: bounded_worktree_patch_proposal
  - requires task authority tuple + iteration

- `task.patch`
  - mutation: true
  - effect: bounded_worktree_source_mutation
  - requires task authority tuple + iteration + proposal identity/digest

MCP should require no new stable descriptor. The existing `dev.capabilities` discovers the operations and `dev.call` dispatches them verbatim.

## Error model

Refusals should be typed and stable enough for an agent to recover without guessing. Expected categories include:

- unknown task;
- wrong holder;
- stale authority generation;
- no active iteration;
- iteration mismatch;
- lease expired;
- unsafe path;
- path escapes worktree;
- symlink/special file refused;
- binary/non-UTF-8 file refused;
- file too large;
- patch set too large;
- too many files;
- duplicate/conflicting path;
- expected hash mismatch;
- expected absent target exists;
- exact old text mismatch;
- proposal not found;
- proposal digest mismatch;
- proposal stale;
- proposal consumed;
- atomic apply failure.

Errors must not leak secrets or arbitrary host filesystem contents.

## Tests

The implementation should be test-driven.

Minimum coverage:

1. read succeeds for bounded task-worktree text;
2. read refuses path escape;
3. read refuses symlink escape;
4. diff creates a durable proposal without source mutation;
5. diff accepts a multi-file modify + create set;
6. diff refuses duplicate/conflicting paths;
7. diff refuses stale expected hashes;
8. diff refuses create when target exists;
9. patch refuses without active iteration;
10. patch refuses stale generation;
11. patch refuses altered pre-state after preview;
12. patch applies the exact proposal;
13. patch cannot be replayed;
14. multi-file patch is atomic when one mutation cannot apply;
15. successful patch records pre/post hashes;
16. checkpoint tracks newly created files;
17. MCP capability discovery exposes all three operations;
18. MCP `dev.call` passes canonical operation IDs without widening authority;
19. shared MCP/House Mechanic request ID remains visible in independent receipts.

## Rollout

The first live proving sequence after this slice lands:

1. point House Mechanic's configured development repository at the real Git clone rather than the Archive mirror;
2. restart/reload House Mechanic with the new repository binding and source-operation build;
3. verify `dev.capabilities` exposes `task.read`, `task.diff`, and `task.patch`;
4. acquire a fresh task/worktree for the VESTIGIA Runtime retrieval issue;
5. inspect `mcp_context_source.py` and its tests with `task.read`;
6. begin an iteration;
7. create a regression-test proposal with `task.diff`;
8. apply it with `task.patch`;
9. run the targeted test and observe RED;
10. propose/apply the retrieval implementation;
11. run targeted and full Runtime tests;
12. checkpoint the iteration;
13. proceed through candidate dev deployment once a Runtime mechanic-owned service binding is configured;
14. verify live retrieval behavior and joined receipts;
15. open or stage a GitHub PR.

## First proving bug: Runtime Archive-MCP query terms

The initial self-maintenance task is intentionally small and observable.

Current behavior:

- `_query_terms` scans prompt words in sentence order;
- it skips a small stopword set;
- it stops once `max_terms` is reached;
- default `max_terms` is currently 5;
- therefore early low-information words can prevent later high-signal words from ever being searched.

The first Runtime patch should remain deterministic and local rather than claiming true embedding-based semantic retrieval.

Expected direction:

- consider the full prompt before selecting bounded search terms;
- filter obvious low-information/common terms more aggressively;
- select a better bounded high-signal set rather than the first surviving N words;
- modestly increase the default Archive-MCP term ceiling from 5 to 8;
- preserve existing item/token budgets;
- add regression coverage where decisive terms appear late in the prompt.

That Runtime change is a separate bounded task performed **through** the new task source operations. It is not bundled into this authority-surface implementation.

## Non-goals

This slice does not add:

- arbitrary shell;
- caller-supplied argv/cwd/environment;
- arbitrary host filesystem writes;
- Archive authority widening;
- binary file editing;
- delete;
- rename/move;
- symlink mutation;
- supervisor self-update;
- credentials;
- public deployment;
- automatic merge;
- conflict auto-resolution;
- embedding/vector retrieval.

Those can be designed separately if later work justifies them.

## Success criteria

This design is successful when a resident can, without Jeff ferrying ordinary file-edit commands:

- inspect source in a House Mechanic-issued worktree;
- preview a bounded multi-file change;
- apply exactly the reviewed proposal atomically;
- create new repo-local text files and directories;
- test and checkpoint the work;
- retain exact task/Git/receipt provenance;
- fail closed on stale or unsafe state;
- leave the running supervisor and unrelated host filesystem outside the granted authority.

The first concrete proof is a House Mechanic-driven Runtime retrieval patch that reaches a merge-ready Git branch with its test and dev evidence intact.
