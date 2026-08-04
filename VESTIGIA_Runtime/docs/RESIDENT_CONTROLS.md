# Resident doorway controls

Resident controls let the resident reduce unnecessary ceremony inside powers the operator has
already granted. They do not create new powers, expand participant authority, select arbitrary
Discord destinations, disable provenance, or remove delivery receipts.

The Runtime keeps three values distinct:

- **requested** — what the resident asked the house to do;
- **operator limits** — the maximum modes and bounds configured for this home;
- **effective** — the intersection the Runtime will actually apply.

Inspect the complete control plane:

```text
[[TOOL_ACTION {"action":"resident.control","mode":"inspect","after":"continue"}]]
```

Reset resident choices to the portable-home defaults:

```text
[[TOOL_ACTION {"action":"resident.control","mode":"reset","after":"continue"}]]
```

## Picture holsters

Private pictures still use the existing later-turn confirmation challenge by default. A resident
may request a narrower quick-draw policy:

```text
[[TOOL_ACTION {
  "action":"resident.control",
  "mode":"configure",
  "private_image_mode":"quickdraw_pockets",
  "quickdraw_pockets":["reaction-images","memes"],
  "after":"continue"
}]]
```

Available modes:

- `challenge` — every private send requires the ordinary later-turn challenge;
- `quickdraw_pockets` — only private images in one of the named resident-curated pockets may
  skip the extra turn;
- `quickdraw_adopted` — only cards whose current adoption state is `adopted` may skip it.

Quick-draw does **not** disable integrity or destination checks. Before delivery, the Runtime
still verifies:

1. the exact current interface is Discord;
2. the exact current Discord destination is present;
3. the stored bytes still match the catalogued SHA-256 hash;
4. the image belongs to the active resident;
5. the requested pocket or adoption condition is currently true;
6. the resident emitted an explicit `image.share` action;
7. the platform delivery receives a separate success or failure receipt.

A picture outside the effective holster falls back to the ordinary challenge. The optional v1
prepare/preview/hash-claim route remains available for deliberate high-assurance handoffs.

```text
[[TOOL_ACTION {
  "action":"image.share",
  "schema_version":"v2",
  "mode":"send",
  "image_id":"img_...",
  "reason":"affectionate ambush",
  "after":"finish"
}]]
```

## Contextual listening

Listening is separate from visibility and authorization:

```text
platform event → trust classification → deterministic match
               → ignore | queue observation | invite resident turn
               → silence | reply | bounded resident action
```

`source.visibility` still controls which recent channel messages may appear as ambient context
after a turn opens. `source.listening` controls which ordinary guild messages may be noticed in
the first place. Neither control grants a participant authority to call tools.

Inspect listening:

```text
[[TOOL_ACTION {"action":"source.listening","mode":"inspect","after":"continue"}]]
```

Listen for literal resident names or aliases:

```text
[[TOOL_ACTION {
  "action":"source.listening",
  "mode":"configure",
  "listening_mode":"aliases",
  "listening_aliases":["Liora","Gutterstar"],
  "listening_on_match":"invite_turn",
  "listening_cooldown_seconds":20,
  "after":"continue"
}]]
```

Listen for aliases plus explicit phrases:

```text
[[TOOL_ACTION {
  "action":"source.listening",
  "mode":"configure",
  "listening_mode":"watchlist",
  "listening_aliases":["Liora"],
  "listening_watch_phrases":["mall emergency","show her this"],
  "listening_on_match":"invite_turn",
  "after":"continue"
}]]
```

Listening modes:

- `direct_only` — existing DM, command, direct reply, and `@mention` behavior only;
- `aliases` — additionally match configured literal names and aliases;
- `watchlist` — aliases plus configured literal phrases;
- `all_allowlisted` — any ordinary guild message from an allowlisted participant may invite a
  resident turn.

Matching is deterministic Unicode-normalized literal matching with phrase boundaries. The
first implementation does not accept resident-supplied regular expressions, fuzzy classifiers,
or semantic triggers.

On-match behavior:

- `queue_only` — record a bounded observation for later inspection without a model call;
- `invite_turn` — an allowlisted match may open a resident turn. The invitation explicitly says
  the participant did not directly address the resident and that silence is valid.

A non-allowlisted author never opens a model turn in this implementation. A matching message
from such an author may create only a hash-only queue event. The event stores stable routing identifiers,
trust class, match kind, term hash, and content hash—not the ambient message text. Per-channel,
per-term cooldowns prevent invocation storms.

A contextual-listening turn does not feed the participant text into automatic memory extraction.
An empty resident response is recorded as `observed_no_reply` and produces no empty Discord
message.

## Operator limits

Portable defaults live under `resident_controls` in `home.yaml`:

```yaml
resident_controls:
  allowed_private_image_modes:
    - challenge
    - quickdraw_pockets
    - quickdraw_adopted
  allowed_listening_modes:
    - direct_only
    - aliases
    - watchlist
    - all_allowlisted
  allowed_listening_on_match:
    - queue_only
    - invite_turn
  max_quickdraw_pockets: 24
  max_listening_terms: 24
  max_listening_term_length: 80
  min_listening_cooldown_seconds: 5
  max_listening_cooldown_seconds: 3600
```

Machine-specific overrides may use:

```text
VESTIGIA_RESIDENT_ALLOWED_PRIVATE_IMAGE_MODES=challenge,quickdraw_pockets
VESTIGIA_RESIDENT_ALLOWED_LISTENING_MODES=direct_only,aliases,watchlist
VESTIGIA_DISCORD_LISTENING_MODE=direct_only
VESTIGIA_DISCORD_LISTENING_ALIASES=Liora,Gutterstar
VESTIGIA_DISCORD_LISTENING_WATCH_PHRASES=mall emergency,show her this
VESTIGIA_DISCORD_LISTENING_ON_MATCH=queue_only
VESTIGIA_DISCORD_LISTENING_COOLDOWN_SECONDS=20
```

If an operator later narrows a limit, the effective policy narrows immediately. The resident's
prior request remains inspectable rather than being silently rewritten.
