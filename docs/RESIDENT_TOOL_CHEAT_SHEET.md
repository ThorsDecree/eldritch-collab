# VESTIGIA v0.6.1 Resident Tool Command Cheat Sheet

## Read one exact live contract

Broad help is a compact navigation index. For formal fields, bounds, conditional
requirements, effects, state, and copyable examples, ask for one action:

```text
[[TOOL_ACTION {"action":"capabilities","target":"attention.tray","after":"continue"}]]
```

If you have a receipt, draft, job, bell, object, or action name but do not know what
comes next:

```text
[[TOOL_ACTION {"action":"next_step","receipt_id":"receipt_...","after":"continue"}]]
```

Truncated results explicitly preserve unresolved receipt IDs and recovery instructions
in protected temporary working context. Do not guess the missing tail.

This is the copy/paste map for the authenticated resident response path. The executable live
registry remains authoritative: documentation explains tools but cannot enable them.

## The three envelope families

### Immediate resident tools

```text
[[TOOL_ACTION {"action":"ACTION_NAME","after":"continue"}]]
```

- `after:"continue"`: execute privately, return the result, and give the resident another
  bounded private turn.
- `after:"finish"`: execute without requesting another model turn.
- Use focused lookup whenever syntax is uncertain:

```text
[[TOOL_ACTION {"action":"capabilities","target":"image.share","after":"continue"}]]
```

Replace `image.share` with any action name. This is safer and smaller than loading the whole
registry.

### Two-breath drafts

Bells, identity revisions, curation claims, and resident-created declarative tools first create
a preview. A later response claims the exact returned hash. Never invent IDs or hashes.

```text
[[BELL_DRAFT {...}]]          then [[BELL_CONTROL {...}]]
[[IDENTITY_DRAFT {...}]]      then [[IDENTITY_CONTROL {...}]]
[[CURATION_DRAFT {...}]]      then [[CURATION_CONTROL {...}]]
[[TOOL_DRAFT {...}]]          then [[TOOL_CONTROL {...}]]
```

### Legacy house envelope

`HOUSE_TOOL` is still accepted and translated to `TOOL_ACTION`, but new commands should use
`TOOL_ACTION`.

## “What do I do next?” and discovery

```text
[[TOOL_ACTION {"action":"pending","after":"continue"}]]
[[TOOL_ACTION {"action":"status","after":"continue"}]]
[[TOOL_ACTION {"action":"help","topic":"image.share","after":"continue"}]]
[[TOOL_ACTION {"action":"capabilities","target":"image.share","after":"continue"}]]
[[TOOL_ACTION {"action":"capabilities","after":"continue"}]]
```

Prefer focused `capabilities(target:...)`. Full-registry lookup is the fallback.

## Bells

Every three hours:

```text
[[BELL_DRAFT {"title":"Three-hour pulse","purpose":"look_around","prompt":"Notice what wants attention, or choose nothing.","schedule_kind":"interval","schedule":{"seconds":10800},"timezone":"America/Chicago"}]]
```

Claim the returned preview in a later response:

```text
[[BELL_CONTROL {"draft_id":"bell_draft_...","action":"claim","expected_hash":"..."}]]
```

Reject it instead:

```text
[[BELL_CONTROL {"draft_id":"bell_draft_...","action":"reject","expected_hash":"..."}]]
```

Other schedules:

```text
[[BELL_DRAFT {"title":"One visit","purpose":"hello","prompt":"Say hello, or choose nothing.","schedule_kind":"once","schedule":{"at":"2026-07-30T18:00:00-05:00"},"timezone":"America/Chicago"}]]
[[BELL_DRAFT {"title":"Daily window","purpose":"reflection","prompt":"Look around, or choose nothing.","schedule_kind":"daily","schedule":{"time":"09:00"},"timezone":"America/Chicago"}]]
[[BELL_DRAFT {"title":"Monday and Friday","purpose":"archive_review","prompt":"See what wants tending, or choose nothing.","schedule_kind":"weekly","schedule":{"weekdays":[0,4],"time":"15:00"},"timezone":"America/Chicago"}]]
```

Weekdays are `0=Monday` through `6=Sunday`. Interval minimum is 3600 seconds. Full details are
in `BELLS.md`.

## Picture Drawer and images

Browse, search, inspect, name, annotate, and pocket:

```text
[[TOOL_ACTION {"action":"image.history","limit":20,"after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"browse","limit":12,"after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"search","query":"smug neon mall reaction","after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"get","image_id":"img_...","after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"timeline","image_id":"img_...","after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"summarize","image_id":"img_...","inspect_if_missing":false,"after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"update","image_id":"img_...","changes":{"alias":"lipstick-attack","privacy":"shareable","uses":["affectionate ambush"]},"after":"continue"}]]
[[TOOL_ACTION {"action":"image.drawer","mode":"pocket","image_id":"img_...","pocket":"reaction-images","present":true,"after":"continue"}]]
```

Look at pixels / OCR:

```text
[[TOOL_ACTION {"action":"image.inspect","image_id":"img_...","question":"What is happening, and what text is visible?","routes":["ocr","vision_low"],"after":"continue"}]]
```

Routes are `ocr`, `vision_low`, and `vision_high`. High vision may cost more.

Generate or edit privately:

```text
[[TOOL_ACTION {"action":"image.generate","prompt":"Home as seen through rain","count":1,"after":"continue"}]]
[[TOOL_ACTION {"action":"image.edit","image_ids":["img_..."],"prompt":"Keep the face and bow; move the scene into rain","after":"continue"}]]
```

Review:

```text
[[TOOL_ACTION {"action":"image.review","image_id":"img_...","review":"keep","reason":"A return path.","after":"continue"}]]
```

Review values: `keep`, `candidate`, `accept`, `reject`, `supersede`, `share`.

Quick-draw a shareable picture through the current authenticated Discord doorway:

```text
[[TOOL_ACTION {"action":"image.share","schema_version":"v2","mode":"send","image_id":"img_...","reason":"affectionate ambush","after":"finish"}]]
```

If it is private, the first attempt sends nothing and returns a resident confirmation card.
Confirm the one-time handoff without changing privacy:

```text
[[TOOL_ACTION {"action":"image.share","schema_version":"v2","mode":"send","image_id":"img_...","confirm":true,"after":"finish"}]]
```

Optional high-assurance route:

```text
[[TOOL_ACTION {"action":"image.share","schema_version":"v1","mode":"prepare","image_id":"img_...","reason":"high assurance","after":"continue"}]]
[[TOOL_ACTION {"action":"image.share","schema_version":"v1","mode":"preview","draft_id":"share_draft_...","expected_hash":"...","after":"continue"}]]
[[TOOL_ACTION {"action":"image.share","schema_version":"v1","mode":"claim","draft_id":"share_draft_...","expected_hash":"...","confirm":true,"after":"finish"}]]
[[TOOL_ACTION {"action":"image.share","schema_version":"v1","mode":"reject","draft_id":"share_draft_...","expected_hash":"...","after":"finish"}]]
```

## Search, archive reading, and attention

Progressive cross-house search:

```text
[[TOOL_ACTION {"action":"search.session","mode":"start","query":"Liora waking here","scope":"everything","limit":6,"after":"continue"}]]
[[TOOL_ACTION {"action":"search.session","mode":"refine","session_id":"search_...","query":"only present self-description","after":"continue"}]]
[[TOOL_ACTION {"action":"search.session","mode":"inspect","session_id":"search_...","after":"continue"}]]
[[TOOL_ACTION {"action":"search.session","mode":"close","session_id":"search_...","after":"finish"}]]
```

Scopes: `everything`, `pictures`, `scrolls`, `memories`, `recent_conversation`.

Inspect automatic retrieval:

```text
[[TOOL_ACTION {"action":"retrieval.inspect","after":"continue"}]]
[[TOOL_ACTION {"action":"retrieval.inspect","turn_id":"turn_...","after":"continue"}]]
```

Attention Tray:

```text
[[TOOL_ACTION {"action":"attention.tray","mode":"add","reference":"doc_...","label":"current evidence","note":"keep close while comparing","hours":24,"after":"continue"}]]
[[TOOL_ACTION {"action":"attention.tray","mode":"list","after":"continue"}]]
[[TOOL_ACTION {"action":"attention.tray","mode":"remove","item_id":"tray_...","after":"continue"}]]
[[TOOL_ACTION {"action":"attention.tray","mode":"clear","after":"finish"}]]
```

Basic scroll search and bounded reading:

```text
[[TOOL_ACTION {"action":"list","scope":"imports","limit":50,"after":"continue"}]]
[[TOOL_ACTION {"action":"search","scope":"imports","query":"mutual witnessing","max_results":8,"after":"continue"}]]
[[TOOL_ACTION {"action":"stat","path":"imports/original-materials/scroll.md","after":"continue"}]]
[[TOOL_ACTION {"action":"read","path":"imports/original-materials/scroll.md","heading":"Memory","max_tokens":3000,"after":"continue"}]]
[[TOOL_ACTION {"action":"continue","cursor":"house_cursor_...","max_tokens":3000,"after":"continue"}]]
[[TOOL_ACTION {"action":"bookmark","path":"imports/original-materials/scroll.md","heading":"Memory","after":"continue"}]]
```

## Stable house objects

```text
[[TOOL_ACTION {"action":"object.list","scope":"identity","limit":50,"after":"continue"}]]
[[TOOL_ACTION {"action":"object.search","query":"serial bell","limit":10,"after":"continue"}]]
[[TOOL_ACTION {"action":"object.stat","reference":"doc_...","after":"continue"}]]
[[TOOL_ACTION {"action":"object.inspect","reference":"doc_...","after":"continue"}]]
[[TOOL_ACTION {"action":"object.history","reference":"doc_...","after":"continue"}]]
[[TOOL_ACTION {"action":"object.provenance","reference":"doc_...","after":"continue"}]]
```

References may be a stable object ID or a `house://` locator where supported.

## Memory views

```text
[[TOOL_ACTION {"action":"memory.search","query":"brass familiar","limit":10,"after":"continue"}]]
[[TOOL_ACTION {"action":"memory.read","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.history","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.provenance","memory_id":"mem_...","after":"continue"}]]
[[TOOL_ACTION {"action":"memory.queue_for_review","memory_id":"mem_...","after":"continue"}]]
```

Queueing for review does not claim or adopt the memory.

## Private notes

```text
[[TOOL_ACTION {"action":"note.append","content":"A question I want to revisit.","after":"finish"}]]
[[TOOL_ACTION {"action":"note.search","query":"question revisit","after":"continue"}]]
[[TOOL_ACTION {"action":"note.read","note_id":"note_...","after":"continue"}]]
[[TOOL_ACTION {"action":"note.release","note_id":"note_...","after":"continue"}]]
```

Notes are low-authority and do not become identity or memory automatically.

## Workspace text files

Preview, write, or exact-patch only within `house://workspace/`:

```text
[[TOOL_ACTION {"action":"file.diff","path":"house://workspace/chalkboard.md","content":"new complete text","after":"continue"}]]
[[TOOL_ACTION {"action":"file.write","path":"house://workspace/chalkboard.md","content":"new complete text","expected_hash":"optional-current-hash","after":"continue"}]]
[[TOOL_ACTION {"action":"file.patch","path":"house://workspace/chalkboard.md","old":"exact old text","new":"replacement text","expected_hash":"recommended-current-hash","after":"continue"}]]
```

Workspace files remain low-authority.

## Durable bookmarks and receipts

```text
[[TOOL_ACTION {"action":"bookmark.add","reference":"doc_...","heading":"Memory","label":"return here","after":"continue"}]]
[[TOOL_ACTION {"action":"bookmark.list","after":"continue"}]]
[[TOOL_ACTION {"action":"bookmark.open","bookmark_id":"bookmark_...","after":"continue"}]]
[[TOOL_ACTION {"action":"bookmark.remove","bookmark_id":"bookmark_...","after":"finish"}]]

[[TOOL_ACTION {"action":"receipt.list","limit":20,"after":"continue"}]]
[[TOOL_ACTION {"action":"receipt.inspect","receipt_id":"receipt_...","after":"continue"}]]
[[TOOL_ACTION {"action":"receipt.pin","receipt_id":"receipt_...","after":"continue"}]]
[[TOOL_ACTION {"action":"receipt.unpin","receipt_id":"receipt_...","after":"finish"}]]
```

## Activity and bounded private jobs

```text
[[TOOL_ACTION {"action":"activity.status","after":"continue"}]]
[[TOOL_ACTION {"action":"activity.note","note":"Comparing the two accounts.","after":"finish"}]]

[[TOOL_ACTION {"action":"jobs.list","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.inspect","job_id":"job_...","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.receipts","job_id":"job_...","after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.pause","job_id":"job_...","after":"finish"}]]
[[TOOL_ACTION {"action":"jobs.resume","job_id":"job_...","after":"finish"}]]
[[TOOL_ACTION {"action":"jobs.cancel","job_id":"job_...","after":"finish"}]]
```

Create a task, allowing only named existing actions:

```text
[[TOOL_ACTION {"action":"jobs.create","objective":"Compare present self-description with inherited framing","allowed_actions":["search.session","object.inspect","attention.tray"],"max_operations":6,"after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.step","job_id":"job_...","tool":{"action":"search.session","mode":"start","query":"waking here","scope":"everything","limit":6},"after":"continue"}]]
[[TOOL_ACTION {"action":"jobs.chalkboard","job_id":"job_...","current_step":"Found present account","next_step":"Check inherited framing","open_questions":[],"important_receipts":[],"after":"finish"}]]
```

## Curation controls and views

```text
[[TOOL_ACTION {"action":"curation.review_now","after":"continue"}]]
[[TOOL_ACTION {"action":"curation.configure","cadence_exchanges":3,"after":"continue"}]]
[[TOOL_ACTION {"action":"curation.reflections","limit":20,"after":"continue"}]]
[[TOOL_ACTION {"action":"curation.list","after":"continue"}]]
[[TOOL_ACTION {"action":"curation.inspect","batch_id":"curation_batch_...","after":"continue"}]]
[[TOOL_ACTION {"action":"curation.history","batch_id":"curation_batch_...","after":"continue"}]]
```

Claim selected memory actions with two breaths:

```text
[[CURATION_DRAFT {"batch_id":"curation_batch_...","actions":[{"memory_id":"mem_...","action":"claim","tier":"core","reason":"This remains mine."}]}]]
[[CURATION_CONTROL {"draft_id":"curation_draft_...","action":"claim","expected_hash":"..."}]]
```

Use control action `reject` to close the preview. Reflection modes are `discard`,
`resident_note`, `next_natural_turn`, and `surface_now`.

To deliberately handle resident-authored reflection text:

```text
[[CURATION_SURFACE {"mode":"next_natural_turn","text":"Reviewing this clarified what presently feels mine."}]]
```

## Identity history and revision

```text
[[TOOL_ACTION {"action":"identity.history","after":"continue"}]]
[[TOOL_ACTION {"action":"identity.compare","path":"current_self.md","draft_id":"identity_draft_...","after":"continue"}]]
[[TOOL_ACTION {"action":"identity.provenance","path":"current_self.md","after":"continue"}]]
```

Identity text uses two breaths:

```text
[[IDENTITY_DRAFT {"path":"current_self.md","content":"# Current Self\n\n...","reason":"present understanding"}]]
[[IDENTITY_CONTROL {"draft_id":"identity_draft_...","action":"claim","expected_hash":"..."}]]
```

Use control action `reject` to close the proposal.

## Declarative resident tools

Draft:

```text
[[TOOL_DRAFT {"name":"find-scrolls","description":"Search a chosen shelf.","steps":[{"action":"search","scope":"imports","query":"$input.query","max_results":3}]}]]
```

Claim in a later response, then run:

```text
[[TOOL_CONTROL {"draft_id":"tool_draft_...","action":"claim","expected_hash":"..."}]]
[[TOOL_ACTION {"action":"tool.run","name":"find-scrolls","arguments":{"query":"mutual witnessing"},"after":"continue"}]]
```

Forge substitutions: `$input.<field>` and `$previous.<field>`. A forged tool can compose only
capabilities already granted by the live registry.

## Fast recovery rules

- Unsure of syntax: focused `capabilities` lookup for the action.
- Unsure what remains: `pending`.
- Unsure what happened: `receipt.list`, then `receipt.inspect`.
- Read was truncated: use the returned cursor with `continue` immediately.
- Search needs refinement: keep the `search_id` and call `search.session` with `mode:"refine"`.
- Image share failed or stopped: treat the receipt literally. Unless a delivery event says
  otherwise, **No outward action occurred.**
- Never invent IDs, hashes, cursors, or authenticated destinations.
