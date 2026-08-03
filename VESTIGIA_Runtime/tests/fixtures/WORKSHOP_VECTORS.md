# Workshop contract vectors

`workshop_contract_vectors.json` is a design fixture for the Workshop Within. It defines acceptance,
authority, privacy, sandbox, filesystem, resource, recursion, recovery, consent, and identity
negative cases before implementation begins.

The implementation test suite should convert each vector into one or more deterministic tests. A
vector may not be marked passing merely because a field exists; the associated side effect,
receipt, and restart behavior must be exercised.

The canonical positive vector is `say-hi-success`. The canonical imported-code refusal vector is
`imported-script-inert`. The canonical isolation-honesty vector is
`hardened-backend-unavailable`.
