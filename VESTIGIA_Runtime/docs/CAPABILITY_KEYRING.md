# Capability Keyring

Capability Keyring v0.1 is a descriptive preflight layer over VESTIGIA Runtime's existing
`CapabilityRegistry`. It does not create a second capability ontology and it does not silently
change the authority of capabilities that already exist.

The invariant is:

```text
Runtime CapabilityRegistry + existing focused authorizers
                    |
                    v
            Capability Keyring
       identity / epoch / preflight
                    |
                    v
          evidence, not approval
```

## Current surface

Runtime registers four read-only introspection capabilities:

- `policy.whoami`
- `policy.can`
- `policy.explain`
- `capability.preview`

All four are `database:read`, confirmation-free Runtime capabilities. The existing read-only MCP
projection therefore exposes them automatically through `runtime.capabilities` / `runtime.call`.
MCP does not need four parallel native tool definitions.

## Principal and authority epoch

`policy.whoami` reports the current resident principal and room plus route evidence when known.
For an MCP-projected call, the route includes the MCP deployment ID supplied by the bridge.

Runtime maintains a private `capability_authority_state` row for each resident with an integer
`authority_epoch`, currently seeded to `1`.

v0.1 has no epoch-mutation or grant-mutation capability. The durable epoch exists now so later
revocation/grant work has a stable value to bind approvals to instead of inventing one after
write-capable projection already exists.

## Effect classification

The Keyring derives a coarse effect class from the existing Runtime capability effects:

- `perceive` - read-only filesystem/database effects;
- `prepare` - pending-draft, low-authority, cache, or audit working state;
- `act` - workspace/canonical writes, controls, network/external effects;
- `unknown` - effects that have not yet been classified by the Keyring lens.

This classification is a projection. The original `CapabilitySpec.effects` remain the source
record.

Unknown effects are surfaced explicitly. They are never silently relabeled as read-only.

## `policy.can`

`policy.can` inspects the current Runtime contract for a named capability. With optional
`arguments`, it also runs the same bounded JSON-schema validator used before Runtime dispatch.

Its decision vocabulary is:

- `allow` - the current Runtime contract is callable and confirmation-free at the contract layer;
- `confirm` - the current Runtime contract declares a focused confirmation policy;
- `deny` - the capability is unknown, disabled, not callable, non-`TOOL_ACTION`, or the supplied
  candidate fails the Runtime schema.

This decision is deliberately named `runtime_contract_preflight`. v0.1 does **not** run the
target capability's focused authorizer or handler. A later real dispatch may therefore encounter
additional current-state or doorway checks.

## `policy.explain`

`policy.explain` returns the same preflight plus the public live Runtime contract used to derive
it. It is intended to make authority seams legible without copying capability semantics into a
second policy catalog.

One important migration signal is `legacy_unkeyed_authority`.

If an `act`-class Runtime capability is currently callable with `confirmation=none`, v0.1 reports
that fact without changing it. Existing behavior remains authoritative, but the capability is
marked as a Keyring migration gap for a later enforcing phase.

## `capability.preview`

`capability.preview` accepts a target capability name and candidate `arguments`.

It:

1. resolves the live Runtime contract;
2. owns the target `action` and `after` fields so callers cannot smuggle routing fields through
   the nested candidate;
3. validates the candidate with Runtime's existing schema validator;
4. derives effect/preflight evidence;
5. computes SHA-256 over the canonical candidate payload;
6. returns a bounded target summary that does not echo arbitrary candidate content.

It does **not**:

- execute the target handler;
- execute the target's focused authorizer;
- create a staged patch merely because `fs.stage_patch` was previewed;
- create an approval;
- change canonical files;
- perform an external effect.

The payload hash is evidence about exactly what was previewed. It is not an authorization token.

## Staged-patch guinea pig

The initial acceptance path deliberately previews `fs.stage_patch` because it is a useful
PREPARE-class capability with a real schema and a reversible/private consequence if actually
executed.

```text
capability.preview(fs.stage_patch, exact candidate)
             |
             v
schema + effect + target + payload hash
             |
             X
       no staged patch created
```

The current read-only MCP projection may call `capability.preview`, `policy.*`, and the existing
patch inspection tools. It still cannot call `fs.stage_patch` or `fs.patch_discard`.

## What v0.1 intentionally does not solve

General Keyring enforcement is not active yet.

Still future work:

- durable scoped grants;
- ALLOW / CONFIRM / DENY rules by principal, deployment, target, workspace, or account;
- authority-epoch mutation on grant/revocation changes;
- hash-bound approvals with TTL;
- exact approval binding to payload + destination + account + action + epoch;
- final-dispatch authority recheck at the last reversible boundary;
- migration of existing ACT capabilities behind the general gate;
- `fs.patch_apply` and other new consequential powers.

The next enforcing phase should migrate capabilities deliberately rather than turning on one
global switch and hoping every historical contract means the same thing.
