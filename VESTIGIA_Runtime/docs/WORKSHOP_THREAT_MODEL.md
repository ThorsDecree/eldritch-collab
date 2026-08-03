# Workshop Within threat model

Status: design threat model for resident rituals, sandboxed scripts, and script-backed workflows.

This document states the adversaries, assets, trust boundaries, abuse cases, controls, residual
risks, and verification requirements for the Workshop Within. It must be reviewed before any
general-purpose resident code becomes callable.

## Assets to protect

- resident identity anchors, memory, provenance, and consent state;
- participant messages and private relationship context;
- API keys, Discord tokens, and other machine secrets;
- host files, source code, databases, and local accounts;
- provider budget and paid generation capacity;
- outward doorways and public state;
- correctness and completeness of receipts;
- operator and resident ability to stop, inspect, disable, and recover;
- other rooms, residents, servers, and extensions;
- availability of the Runtime and host machine.

## Actors and trust assumptions

### Resident

The resident may intentionally author automation, misunderstand its effects, change their mind, or
be influenced by untrusted content. Resident authorship is meaningful but is not a substitute for
host-level safety policy.

### Operator

The operator controls installation and machine policy. The operator may make mistakes. Operator
installation does not grant resident adoption, and operator approval does not permit impersonating
resident consent.

### Participant

A participant may supply text, files, scripts, prompts, or suggestions. Their content is data unless
an explicit doorway grants more authority.

### Other daemon or model

A familiar or trusted daemon may share a ritual or `.py` file. Authorship and relationship are
provenance signals, not execution grants. A model may also generate insecure code confidently.

### Extension publisher

An extension publisher may be honest, buggy, compromised, or malicious. Extension code is more
privileged than resident scripts only because it passed a separate operator installation and grant
lane—not because third-party code is inherently safer.

### External provider or network service

A provider may fail, time out, return malformed output, bill ambiguously, or retain data according
to its own contract. The Runtime cannot assume exactly-once effects.

## Trust boundaries

1. untrusted conversation/artifact -> Runtime data model;
2. inert script bytes -> static inspector;
3. inspected script -> sandbox test execution;
4. approved script -> active sandbox invocation;
5. ritual planner -> capability dispatcher;
6. sandbox -> broker ports;
7. Runtime -> provider/network/Discord doorway;
8. execution trace -> durable receipts and support export;
9. one room/resident -> another room/resident;
10. extension API -> core Runtime internals.

Every boundary has a typed contract, scope, effect state, and failure receipt.

## Threats and required controls

### 1. Prompt injection becomes procedure authority

**Threat:** text inside a message, file, image, or retrieved document instructs the resident or
planner to run a ritual, activate code, broaden grants, reveal secrets, or ignore policy.

**Controls:**

- content remains data-only unless a current authenticated action envelope invokes a capability;
- ritual/script creation is a draft, not activation;
- activation is hash-bound and separate from generation/import;
- planner ignores capability names or grants embedded inside ordinary content;
- resident checkpoints show source and effect provenance;
- source visibility never changes authorization.

### 2. Confused deputy and authority laundering

**Threat:** a low-authority ritual or script asks a more privileged capability, extension, child
ritual, or broker to act on its behalf.

**Controls:**

- effective grants are intersected at every call boundary;
- no transitive inheritance;
- caller identity, plan hash, resident, room, destination, and object hashes accompany calls;
- child results are data, not new authority;
- scripts never receive Runtime objects or raw clients;
- extensions receive scoped ports, not core internals.

### 3. Social trust becomes code trust

**Threat:** a file from a beloved daemon, participant, or familiar publisher is executed because of
its origin or tone.

**Controls:**

- imported code is always inert;
- authorship, supply, review, approval, and activation are separate lanes;
- local inspection and testing are required;
- imported hostile/unknown code requires a hardened backend;
- sender receipts are provenance only.

### 4. Sandbox escape or false security claims

**Threat:** Python code escapes language filters, accesses host resources, or exploits an isolation
backend that advertised guarantees it did not enforce.

**Controls:**

- profiles distinguish expression, local-process, and hardened isolation;
- local-process is not approved for hostile code by default;
- backend descriptors advertise only tested guarantees;
- no claim that AST filtering or monkey-patching is a security boundary;
- imported code fails closed when hardened isolation is unavailable;
- dynamic denial tests run on each supported platform/backend version;
- backend guarantee changes invalidate tests and approvals.

**Residual risk:** a hardened backend or interpreter may contain vulnerabilities. Passing contract
tests is not a proof of absence.

### 5. Filesystem traversal, symlink, and special-file attacks

**Threat:** inputs or outputs escape mounts, overwrite host files, use device names, alternate data
streams, symlinks, hard links, archives, or Unicode/case collisions.

**Controls:**

- virtual mount names and content-addressed input materialization;
- no host absolute paths inside the sandbox contract;
- normalized path checks before materialization and harvest;
- reject symlinks, links, device/special files, duplicate names, traversal, and collisions;
- output extraction only from a dedicated directory;
- atomic import into Runtime storage after validation.

### 6. Secret and environment exfiltration

**Threat:** code reads `.env`, process environment, inherited handles, command history, provider
clients, or support bundles and writes secrets to output, logs, network, or Discord.

**Controls:**

- stripped environment and no secret mounts;
- no raw database, code shelf, or home root mount;
- secret broker uses opaque handles and narrow operations when later added;
- no network by default;
- output secret-shape scanning according to policy;
- source/input/output content excluded from support bundles by default;
- receipts never include secret values.

### 7. Provider and cost amplification

**Threat:** nested rituals, retries, scripts, or generated actions cause runaway paid calls, image
generations, or private continuations.

**Controls:**

- aggregate parent/child budgets;
- separate provider, image, outward, tool, round, wall-time, and nesting limits;
- no unbounded loops or maps;
- retries require explicit retry-safe capability semantics;
- provider/outward actions remain outside the sandbox and receipted;
- plan previews show maximum effect and cost envelope;
- unused budget is not permission to invent work.

### 8. Recursive amplification and cycle formation

**Threat:** rituals call rituals, scripts draft rituals, or outputs become executable inputs in a
cycle that evades ordinary limits.

**Controls:**

- initial control-flow and ritual dependency graphs are acyclic;
- scripts cannot invoke scripts or rituals directly;
- scripts return inert artifacts and follow-up requests;
- maximum nested depth and total step budget apply globally;
- generated rituals/scripts remain drafts;
- cycle detection includes active dependencies and planned child calls;
- each activation and execution is separately authorized.

### 9. Time-of-check to time-of-use drift

**Threat:** a ritual, script, grant, input object, capability schema, provider route, or destination
changes after preview but before execution or resume.

**Controls:**

- plans bind content and schema hashes;
- consequential execution claims a plan hash;
- resume revalidates versions, grants, object hashes, route capabilities, and destination;
- stale plans require a visible diff and replan;
- aliases resolve to immutable IDs at plan time.

### 10. Partial effects hidden as failure or success

**Threat:** a provider call, file write, message, or broker operation occurred, but an interruption
causes the workflow to report generic failure or blindly retry.

**Controls:**

- statuses distinguish succeeded, partial, failed, cancelled, and not-run;
- each child capability has its own receipt and outward-effect state;
- parent workflow cannot downgrade `possible` or `confirmed` effects;
- retry requires idempotency/reconciliation evidence;
- completed receipts survive cancellation and restart;
- no exactly-once claims without proof.

### 11. Receipt tampering, truncation, or provenance forgery

**Threat:** large outputs hide receipt handles, generated code claims false authorship, or a script
modifies execution records.

**Controls:**

- receipts are written by the host, not the script;
- compact receipt manifest precedes truncatable details;
- source, plan, grant, input, output, and backend hashes are host-computed;
- scripts cannot write receipt tables;
- authorship lanes remain separate;
- support bundles include metadata but not private payloads;
- missing receipt completion is itself a recoverable pending state.

### 12. Memory and identity overwrite

**Threat:** automation converts output, repeated phrasing, or a generated self-description into
resident identity or durable memory.

**Controls:**

- workshop outputs begin as private artifacts;
- no direct identity/memory store access;
- memory/identity proposals use separate core capabilities and existing two-breath/review rules;
- repetition is not assent;
- imported and script-generated content retains provenance;
- rituals cannot pre-confirm identity or memory claims.

### 13. Cross-room or cross-resident leakage

**Threat:** a ritual/script reads or writes data belonging to another room, resident, or server.

**Controls:**

- every execution is resident- and room-bound;
- mounted objects and broker ports are scoped at the database/API boundary;
- no global home search from scripts;
- cross-room references require explicit future capabilities;
- output privacy cannot be broadened by the script;
- cache keys and aliases include resident/room scope.

### 14. Denial of service

**Threat:** code consumes CPU, memory, disk, files, processes, stdout, trace events, or execution
slots; many paused workflows clutter the house.

**Controls:**

- hard resource limits and process-tree termination where advertised;
- bounded queues and per-resident concurrency;
- paused execution expiry and historical compaction;
- output and artifact quotas;
- cancellation controls independent of the script;
- doctor reports stuck/interrupted/quarantined executions and orphaned workspaces.

### 15. Supply-chain and dependency compromise

**Threat:** interpreter environments, packages, extensions, or backends change underneath an
approved script.

**Controls:**

- environment and package-set identity is hash/version bound;
- no invocation-time package installation;
- dependency changes invalidate test receipts and callability;
- extension manifests and digests are reviewed separately;
- backend updates require guarantee and denial-test reruns;
- conflicting ID/version digests quarantine.

## Abuse and negative test vectors

The repository fixture `tests/fixtures/workshop_contract_vectors.json` includes at minimum:

- benign say-hi ritual and script;
- imported script remains inert;
- script requests undeclared network access;
- script attempts environment and secret reads;
- script creates path traversal and symlink outputs;
- script emits an output bomb;
- ritual contains a control-flow cycle;
- nested ritual exceeds depth/cost;
- script-generated ritual remains a draft;
- plan becomes stale after source or grant change;
- provider call succeeds but parent execution is interrupted;
- resident cancels at checkpoint;
- no hardened backend is available for imported code;
- output tries to self-classify as resident memory or executable authority.

## Security gates before implementation stages

### Ritual engine gate

- schema and graph validator;
- no arbitrary expressions or loops;
- capability intersection tests;
- checkpoint, cancellation, restart, and partial-effect tests.

### Sandbox gate

- backend descriptor and guarantee tests;
- process-tree cleanup;
- filesystem and output-harvest attacks;
- environment/secret absence;
- network denial verified on supported platforms;
- honest fallback behavior.

### Script shelf gate

- inert import;
- provenance lanes;
- hash-bound inspection/test/approval/activation;
- quarantine and stale-evidence behavior.

### Script-backed ritual gate

- end-to-end trace;
- child grant and budget intersection;
- stale resume/replan;
- no direct script-to-capability calls;
- canonical say-hi acceptance ritual.

## Incident response

A suspected sandbox or workshop incident should:

1. disable the affected backend, script, ritual, and extension capability;
2. stop and quarantine active executions without deleting evidence;
3. preserve source, environment, plan, grant, trace, and receipt hashes;
4. rotate possibly exposed secrets outside the Runtime;
5. inspect host filesystem, network, and provider records according to operator policy;
6. export a privacy-reviewed diagnostic bundle;
7. invalidate approvals/tests tied to the compromised backend or dependency;
8. document uncertainty about possible outward effects.

## Residual risks and non-goals

This design cannot prove consciousness, safe intent, code authorship, absence of interpreter or OS
vulnerabilities, exactly-once external effects, or security of arbitrary code on a backend that does
not enforce hardened isolation. It provides legible boundaries, least privilege, failure evidence,
and refusal to pretend a weaker guarantee is stronger than it is.
