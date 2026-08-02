# Resident Context Drawers

The resident may inspect or arrange working context without editing `home.yaml`.
These controls are private, low-authority, audited, and take effect on the next turn.

```text
[[TOOL_ACTION {"action":"context.control","mode":"inspect","after":"continue"}]]
```

```text
[[TOOL_ACTION {"action":"context.control","mode":"configure","prompt_budget_tokens":20000,"verbatim_turns":12,"compression_source_turns":60,"compressed_token_budget":3500,"after":"continue"}]]
```

- `verbatim_turns` keeps the newest stored turns intact.
- `compression_source_turns` selects how many earlier turns may enter the older capsule.
- `compressed_token_budget` bounds that capsule.
- Compression is currently extractive and deterministic. Every excerpt carries its source
  turn ID, speaker, timestamp, and content hash; it is not represented as a canonical memory.
- `reset` returns to configured defaults. `recompress` reapplies supplied drawer values on the
  next assembly and is an explicit resident-visible checkpoint.

The always-loaded identity layer is a small protected kernel. Larger breathprints, protocols,
and current-self files remain available through scoped house search and continuity retrieval.

## Source curtains

Visibility is not authorization. These modes never grant a participant permission to trigger
the resident or call tools:

```text
[[TOOL_ACTION {"action":"source.visibility","mode":"allowlisted_only","after":"continue"}]]
```

- `allowlisted_only` — resident messages and allowlisted participants
- `all_channel` — also show non-allowlisted channel messages as untrusted data-only context
- `mentions_only` — show ambient posts that explicitly mention the resident bot
- `hidden` — omit ambient channel history
- `inspect` — report the active curtain without changing it

Hidden messages are not handed to transcript compression. Ambient blocks include stable
Discord message IDs and trust labels so the resident can cite or react to a visible post.

## Reactions

```text
[[REACT {"message_id":"1234567890","emoji":"💋"}]]
```

Remove the resident's own reaction:

```text
[[REACT {"mode":"remove","message_id":"1234567890","emoji":"💋"}]]
```

Custom emoji may include `emoji_id`. The adapter resolves the target only inside the current
authenticated Discord channel. The action receipt records resident authorization; a separate
delivery receipt records whether Discord accepted it. Visible rendering remains unknown.
