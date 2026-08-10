# Library Window lifecycle confirmation v0.1

Status: **development / v0.8 line**

This policy removes natural-language keyword matching from Library Window lifecycle authority.

## Interactive participant boundary

On interactive participant surfaces (`cli` and `discord`), destructive lifecycle actions do not execute merely because the current message contains words such as `delete`, `remove`, `forget`, or `revoke`.

A participant-authority notebook discard or source-retrieval revocation creates a short-lived confirmation bound to the exact action and target fingerprint. The Runtime returns a prompt such as:

```text
Delete notebook sha256:<target>? Y/N
```

Only a fresh participant turn containing `Y`/`Yes` (or the equivalent explicit confirmation token) consumes that exact pending confirmation. `N`/`No` cancels it. Confirmations expire after ten minutes.

The stored confirmation record contains action/target identifiers and hashes, not notebook or source content.

## Resident/autonomous boundary

Non-interactive resident paths such as bells or trusted in-process operations may issue the structured lifecycle command directly without a human confirmation round trip. The structured capability call is the resident's decision artifact.

This distinction is deliberate:

- participant consent is never inferred from prose;
- resident private housekeeping does not require a human to ratify every autonomous action;
- remote-content quarantine remains independent and continues to forbid destructive lifecycle follow-ups in the same quarantined turn.

## Non-destructive lifecycle actions

Notebook `retain` and source detachment remain explicit structured operations. Their authority is not derived from keyword matching.

## Failure semantics

A missing, expired, wrong-target, or cancelled confirmation fails closed and performs no lifecycle mutation. A confirmation for one target cannot authorize a different target.
