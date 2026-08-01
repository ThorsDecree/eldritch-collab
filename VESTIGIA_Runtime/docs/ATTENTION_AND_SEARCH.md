# Resident attention and progressive retrieval

VESTIGIA v0.6 keeps automatic continuity retrieval, archive search, working attention, and
resident adoption separate.

## Attention Tray

The Attention Tray is expiring working context. Adding an object does not make it memory,
identity, canon, agreement, or evidence that the model used it causally.

```text
[[TOOL_ACTION {
  "action":"attention.tray",
  "mode":"add",
  "reference":"doc_...",
  "label":"current evidence",
  "note":"keep close while comparing the two accounts",
  "hours":24,
  "after":"continue"
}]]
```

Active cards receive a bounded context layer. They can be listed, individually cleared, or
cleared together. Clearing changes their state without deleting the preserved operational row.

```text
[[TOOL_ACTION {"action":"attention.tray","mode":"list","after":"continue"}]]
[[TOOL_ACTION {"action":"attention.tray","mode":"remove","item_id":"tray_...","after":"continue"}]]
[[TOOL_ACTION {"action":"attention.tray","mode":"clear","after":"continue"}]]
```

## Durable search sessions

`search.session` returns a few progressive cards rather than immediately loading large source
excerpts. Every card preserves its source type, authority or status where available, why it
was shown, and the exact action that opens it.

```text
[[TOOL_ACTION {
  "action":"search.session",
  "mode":"start",
  "query":"Liora waking here",
  "scope":"everything",
  "limit":6,
  "after":"continue"
}]]
```

Scopes are `everything`, `pictures`, `scrolls`, `memories`, and `recent_conversation`.
Sessions last seven days and can be refined without losing the durable session ID:

```text
[[TOOL_ACTION {"action":"search.session","mode":"refine","session_id":"search_...","query":"only present self-description","after":"continue"}]]
[[TOOL_ACTION {"action":"search.session","mode":"inspect","session_id":"search_...","after":"continue"}]]
[[TOOL_ACTION {"action":"search.session","mode":"close","session_id":"search_...","after":"continue"}]]
```

Search remains authority-aware in presentation. A lexical match in inherited source material
does not become current adoption merely because it appears beside a resident-accepted memory.

## Retrieval Inspector

Automatic continuity retrieval records its ranked memory candidates, score components,
authority, tier, status, and whether the bounded context layer included or omitted each result.

```text
[[TOOL_ACTION {"action":"retrieval.inspect","after":"continue"}]]
```

Pass a `turn_id` to inspect an earlier preserved context receipt. The inspector reports
deterministic context assembly; it does not claim which supplied passage caused a model output.

