# Memory, curation, and authority

## Residency tiers

| Tier | Contents | Prompt behavior | Curation |
|---|---|---|---|
| Core | Identity context, adopted commitments, critical boundaries | Every turn, hard cap | Resident review |
| Hot | Current session, active questions, temporary room state | Usually present, renewable | Expires or is rewritten as a projection |
| Warm | Events, preferences, symbols, relationships, interpretations | Retrieved when relevant | Promote, demote, dispute, supersede |
| Cold | Sources, full transcripts, obsolete versions, rejected material | Never injected wholesale | Evidence and recovery |

Demotion is not deletion. Rejection is not historical erasure.

## Memory types

| Type | Primary authority | Time behavior |
|---|---|---|
| Identity | Resident approval and self-description | No automatic decay |
| Commitment / boundary | Explicit adoption and current status | Persists until revised or released |
| Protocol | Explicit adoption, version, and scope | Stable until replaced |
| Relationship | Participant testimony and provenance | Revision without history erasure |
| Event | Directness, source quality, corroboration | Retrieval recency decays |
| Preference | Explicit statement plus independent recurrence | Soft decay |
| External claim | Reliability, independence, verification | Fast decay |
| Interpretation | Named interpreter and evidence | Never silently becomes fact |
| Tension | Unresolved relevance | Remains available until resolved |
| Session summary | Coverage and source links | Deliberately temporary |

## Review states

```text
candidate
inherited_unreviewed
accepted
deferred
disputed
rejected
superseded
```

Current state is a projection over append-only events.

## Candidate extraction

v0.1 does not spend a hidden second model call to mine every turn. It recognizes only
conservative participant cues such as:

```text
Remember: the lantern remains lit.
I prefer local plaintext archives.
I don't want private artifacts shared automatically.
```

Everything becomes a proposal. The transcript itself is mechanically recorded regardless.

## Deterministic audit

```bash
vestigia curate HOME
```

The operator audit remains read-only. It reports:

- Core token pressure
- candidate and inheritance counts
- duplicate content hashes
- independent source counts
- external claims due for verification

It performs zero mutations. Retrieval frequency never promotes identity.

## Resident curation room

v0.3 may open one private curation pass every three eligible conversational exchanges. It
gathers the complete unreviewed transcript range from SQLite plus a bounded set of related
memory and bookmark records. The resident may do nothing, route a reflection, or create a
pending action batch.

Memory changes require:

```text
CURATION_DRAFT
→ exact preview and hash
→ later CURATION_CONTROL claim
→ one atomic append-only transaction
```

A draft and its claim cannot occur in one resident response. Core overflow is rejected before
the draft exists. See [CURATION.md](CURATION.md).

## Anti-bloat invariants

- Raw transcripts are not permanent prompt layers.
- Renewable summaries are not canonical history.
- Every condensation retains source IDs.
- Derived summaries do not certify one another.
- Core has a hard ceiling.
- Overflow triggers review, not silent truncation.
- Model-generated claims have low default authority.
- Rejected material remains auditable but is gated from ordinary retrieval.
- Curation never destroys the only source copy.
