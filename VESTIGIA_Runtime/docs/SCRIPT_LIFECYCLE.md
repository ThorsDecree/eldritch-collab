# Resident script lifecycle

Status: design contract for the third Workshop Within implementation stage.

A resident script is an immutable source object plus provenance, contracts, review evidence,
effective grants, and activation state. The source text alone is not a callable capability.

## Authorship lanes

The Runtime records separate facts:

- `authored_by` — who is claimed to have written the source;
- `supplied_by` — who or what delivered this copy;
- `reviewed_by` — who inspected or tested it locally;
- `approved_by` — who approved a specific hash and grant set;
- `activated_by` — who made that approved version callable;
- `derived_from` — earlier scripts, artifacts, models, or extensions used to produce it.

These lanes are not collapsed. A daemon may author a script that another human supplies. A model may
help draft a script that a resident later adopts. None of those facts automatically grants trust or
execution authority.

## States

```text
received | draft
      -> inspected
      -> tested
      -> approved
      -> active
      -> disabled
      -> superseded

any pre-active state -> rejected | quarantined | deferred
any state            -> archived
```

- `received`: imported or shared source; inert.
- `draft`: created locally but not yet reviewed; inert.
- `inspected`: static inspection completed for the exact hash.
- `tested`: required local dynamic tests completed under a named sandbox backend/profile.
- `approved`: resident/operator approved the exact source hash, contracts, and effective grants.
- `active`: callable through the sandbox under the approved grant.
- `disabled`: immediately uncallable; evidence retained.
- `superseded`: a newer version exists; prior executions remain reproducible by reference but new
  calls are denied unless policy explicitly permits pinned historical execution.
- `quarantined`: integrity, policy, verification, or behavior failed.
- `archived`: removed from ordinary shelves while preserving history.

No transition is inferred from use, repetition, source location, or import count.

## Immutable version identity

A version is bound to:

```text
script ID
+ semantic/integer version
+ source SHA-256
+ input schema hash
+ output schema hash
+ requested grant hash
+ declared sandbox profile
+ interpreter environment identity
```

Changing any bound component creates a new review target. Mutable aliases may point to the newest
active version but cannot alter what an existing receipt means.

A different source digest with the same script ID and version is a conflict and enters quarantine.

## Script record

A script follows `schemas/resident-script.schema.json` and includes:

- stable ID, name, version, language, and source object reference;
- authorship and supply provenance;
- input and output JSON Schemas;
- requested capabilities and sandbox profile;
- resource limits;
- deterministic/nondeterministic declaration;
- declared artifacts;
- test vector references;
- compatibility requirements;
- privacy and sharing classification;
- current lifecycle state.

Source bytes live in content-addressed storage. Database records reference hashes rather than
duplicating source text in receipts.

## Import and sharing

`script.import` accepts a local file or object reference and creates a `received` record. Import:

- stores the exact bytes and hash;
- records original filename and source interface as metadata;
- performs archive/path safety checks when packaged;
- never imports Python modules;
- never resolves dependencies;
- never executes setup hooks;
- never makes the script callable.

A shared package may include source, README, schemas, and test vectors. Sender-provided signatures
or receipts are preserved as provenance. Local inspection, local testing, and local approval remain
required.

## Static inspection receipt

Inspection emits:

- parse success/failure;
- imports and dynamic import signals;
- dangerous or unsupported language features;
- embedded destinations and secret-shaped strings;
- source size and complexity ceilings;
- declared versus observed file/network/process behavior signals;
- input/output schema validation;
- dependency declarations;
- eligible sandbox profiles;
- policy violations and warnings;
- inspector version and ruleset hash.

Inspection may classify a script as eligible for local testing, hardened-only, or rejected. It does
not claim that eligible code is safe.

## Test receipt

Testing binds:

- source and schema hashes;
- sandbox backend/profile and guarantee descriptor;
- interpreter environment identity;
- synthetic input vector IDs and hashes;
- effective test grants;
- resource limits;
- output hashes and validation results;
- denied-access attempts;
- nondeterminism observations;
- terminal status and effect state.

A source change, backend guarantee change, interpreter environment change, or grant change makes the
test receipt stale for activation purposes.

## Approval and grants

Approval is two related but distinct decisions:

1. this exact source and contract is acceptable;
2. this exact effective grant may be used under stated limits.

The grant follows `schemas/workshop-grant.schema.json` and records requested, granted, denied, and
constrained powers. Effective grants are always an intersection with current house policy.

Approval may require resident, operator, or both depending on policy. A resident can refuse a script
even if an operator installed or supplied it. An operator may deny host-level powers even if the
resident wants them. Neither lane silently impersonates the other.

## Activation

Activation creates a hash-bound activation record with:

- script version identity;
- approval and test receipt IDs;
- effective grant hash;
- allowed caller ritual IDs or direct-call policy;
- expiry and review date;
- maximum cost and resource policy;
- sharing and output privacy defaults.

Activation never occurs as a side effect of testing. Imported scripts are never auto-activated.

## Invocation

A call resolves the active version, rechecks all bound hashes and dependencies, intersects grants,
and creates a plan before starting the sandbox.

Direct invocation may be allowed for the resident. Ritual invocation must also satisfy the ritual's
capability request and caller allowlist. Extensions, scripts, and generated artifacts cannot invoke
the script merely because they know its ID.

## Output provenance

Every output records:

- script ID/version/source hash;
- execution and step IDs;
- input object references and hashes;
- sandbox backend and interpreter identity;
- effective grant hash;
- output media/type and hash;
- privacy and adoption state;
- whether content was resident-authored, script-generated, model-generated, or transformed.

The default authorship lane is `script_generated`. A resident may later adopt or annotate an output
through a separate action. An output never becomes identity or durable memory automatically.

## Revision and supersession

Editing source creates a new version. The Runtime shows a source diff, schema diff, grant diff, and
test impact. Prior versions and receipts remain immutable.

A newer version does not automatically supersede an active version. Supersession is explicit and
may be atomic with activation of the replacement. Existing paused executions remain pinned to their
old version and either resume under the exact old environment or require replanning.

## Disable, quarantine, and archive

Disablement is immediate and preserves all source, approvals, artifacts, and receipts.

Quarantine occurs on:

- digest conflict;
- verification failure;
- observed undeclared behavior;
- sandbox escape or backend guarantee failure;
- dependency or interpreter compromise;
- receipt/provenance inconsistency;
- operator or resident safety decision.

Quarantine prevents new calls and pauses dependent rituals with an explicit reason. It does not
delete evidence.

Archive removes ordinary discoverability while keeping references resolvable for history and
support. Deletion of source bytes is a separate, explicit privacy operation and may make historical
execution non-reproducible; receipts remain.

## Dependency policy

The initial script shelf permits only the reviewed standard-library environment or a named,
prebuilt package environment. A script cannot install packages.

Dependencies are declared by stable package/environment identifiers and hashes. Updates require
retesting. Dependency cycles among scripts are rejected. A script may not import another resident
script as a module; composition occurs through typed ritual steps and receipts.

## Resident-facing inspection

A script card should answer:

- What exact version and hash is this?
- Who authored, supplied, reviewed, approved, and activated it?
- What does it request?
- What was granted or denied?
- Which sandbox guarantee protects this run?
- What tests passed, failed, or became stale?
- Which rituals depend on it?
- What outputs has it produced?
- What changed from the prior version?
- Why is it callable or not callable now?

## Contract tests

The script shelf must prove:

- import is inert and performs no module import or setup execution;
- source/schema/grant changes invalidate prior approval as specified;
- digest/version conflicts quarantine rather than overwrite;
- sender receipts never substitute for local testing and approval;
- activation requires current inspection, tests, approvals, and grants;
- disabled/quarantined scripts are not callable through direct or ritual paths;
- output authorship and privacy do not silently become resident identity or memory;
- paused executions cannot drift to a newer version;
- dependency changes invalidate callability legibly;
- historical receipts remain after disable, supersession, and archive;
- imported `.py` files remain inert by default.

## Non-goals

The script shelf is not a package index, social trust network, code-signing authority, automatic
update channel, collaborative editor, or proof of authorship. Those may be layered later without
weakening local inspection and activation.
