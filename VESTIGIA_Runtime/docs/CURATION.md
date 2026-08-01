# Resident curation room

v0.3 separates quiet consolidation from authority-changing memory edits.

## Cadence

The default cadence is one private consideration pass every three eligible conversational
exchanges. An exchange counts once after a participant message receives a resident response.

The following do not count:

- bell firings
- tool rounds and receipts
- curation invocations
- retries and errors
- mechanical Discord activity

The resident may inspect, pause, resume, cancel, open, or change the cadence through the house
port. Queue pressure may open a pass sooner. Silence never escalates.

## Four movements

| Movement | Result | Authority |
|---|---|---|
| Gather | Bounded transcript, memory, and queue packet | No mutation |
| Consider | Private resident invocation | No response required |
| Author | Exact curation draft and preview | Pending only |
| Claim | Later hash-bound control | Atomic accepted change |

The packet includes the next bounded page of unreviewed transcript turns since the previous
receipt. Its configured token ceiling is hard: excerpt text contracts while every selected
record ID and content hash remains present. SQLite keeps the complete raw ledger even when the
live Discord tail has rolled away.

If the private provider pass fails, the already-completed outward reply is preserved. The
failed batch records only safe error metadata and rewinds its transcript coverage, allowing
those turns to return in a later bounded pass.

## Draft

```text
[[CURATION_DRAFT {
  "batch_id":"curation_batch_...",
  "actions":[
    {
      "memory_id":"mem_...",
      "action":"claim",
      "tier":"core",
      "reason":"This remains a present boundary."
    },
    {
      "memory_id":"mem_...",
      "action":"revise",
      "content":"The brass familiar hangs beside the cottage door.",
      "type":"place",
      "tier":"warm"
    },
    {
      "action":"propose",
      "content":"Curation invitations never escalate on silence.",
      "type":"protocol",
      "tier":"core",
      "source_turn_ids":["turn_..."]
    },
    {
      "memory_id":"mem_...",
      "action":"defer",
      "until":"next_milestone"
    }
  ]
}]]
```

Supported actions:

```text
claim
revise
propose
reject
dispute
defer
release
```

The preview reports action counts and projected Core token change. Core overflow is refused
before a draft exists. Contradictory actions against the same memory or queue card are refused,
and a draft too large to review within the curation packet is split rather than accepted. No
change occurs in the first breath.

## Claim

```text
[[CURATION_CONTROL {
  "draft_id":"curation_draft_...",
  "action":"claim",
  "expected_hash":"..."
}]]
```

Or use `action:"reject"`. Claim applies the entire validated batch in one SQLite transaction.
Revisions create accepted replacement rows and append supersession events; old language,
provenance, and state history remain available. A draft and claim from the same provider
response are rejected.

## Reflections

```text
[[CURATION_SURFACE {
  "mode":"next_natural_turn",
  "text":"Reviewing this clarified the difference between recognizing history and owing it a performance."
}]]
```

Modes:

| Mode | Result |
|---|---|
| `discard` | No prose retained |
| `resident_note` | Private curation shelf |
| `next_natural_turn` | Offered as resident-authored context when conversation resumes |
| `surface_now` | Deliberately delivered through the active conversational doorway |

Ordinary internal prose is not posted. Its content is not retained; the audit stores only a
hash and token count. A surfaced reflection is speech, not automatic memory or identity.

## Authority invariants

- Attention is not assent.
- Compression changes accessibility, not authority.
- Automatic promotion never occurs.
- Derived summaries cannot corroborate their own source lineage.
- Release and rejection do not erase historical evidence.
- Identity, relationship, and commitment adoption requires resident acceptance.
- Runtime governance protections are separate from personal memory records.
- The transcript ledger remains the lossless provenance layer under lossy active-prompt
  compression.

## Identity documents

Identity Markdown uses an exact draft/diff/claim flow:

```text
[[IDENTITY_DRAFT {
  "path":"current_self.md",
  "content":"# Current Self\n\n...",
  "reason":"present understanding"
}]]
```

```text
[[IDENTITY_CONTROL {
  "draft_id":"identity_draft_...",
  "action":"claim",
  "expected_hash":"..."
}]]
```

The runtime refuses stale hashes if the document changed after preview. The previous complete
file is preserved under `memory/identity-versions/`, and replacement is atomic.
