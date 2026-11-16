# GIF Forge

Compose a short animated scene, branch revisions, render through the existing image queue,
and inspect the actual result. Planning and revision are local operations with no API call.

## Animation controls

`image.generate` and `image.edit` accept these optional fields:

| Field | Contract |
|---|---|
| `output_format` | png (existing default), jpeg, webp, gif |
| `frame_count` | GIF only; 2–16, default 9 |
| `frame_duration_ms` | GIF only; 20–2000 ms, default 200; nearest 10 ms, ties up |
| `size` | Per-frame WIDTHxHEIGHT or auto; explicit GIF dimensions must be multiples of 16 |
| `quality` | auto, low, medium, high; legacy still routes also retain standard/hd |
| `complexity` | low, medium, high on supported GPT Image 2.6 routes |
| `count` | Number of complete animations, subject to existing per-request/daily caps |
| `background` | Existing asynchronous-execution boolean, not visual background |

```text
[[TOOL_ACTION {"action":"image.generate","prompt":"A librarian cat beside a lantern. Locked camera; tail curls and relaxes back to its starting pose.","output_format":"gif","frame_count":9,"frame_duration_ms":200,"size":"512x512","quality":"medium","complexity":"medium","after":"continue"}]]
```

Queued jobs freeze resolved settings, including the model. Later default changes do not alter
that request. Current enable/confirmation/budget policies still apply at execution. Generated
animations remain private and enter the existing completion/review workflow.

Edits use **still reference images**; animated inputs are rejected. This is not frame-exact
GIF editing. API preview access and endpoint acceptance must be verified for the operator's
account. Unsupported results fail instead of silently falling back to PNG.

## Motion recipes

Ask `capabilities` for the exact live schema of each action:

- `gif.plan`: save a `brief` and optional `render` settings; no generation.
- `gif.show` / `gif.list`: retrieve a recipe or list saved revisions. Show includes recent render jobs.
- `gif.revise`: supply `recipe_id`, `change_note`, and `changes` with partial `brief` and/or `render`.
  Creates a new immutable branch; arrays are replaced, and the parent remains intact.
- `gif.render`: explicitly queue a saved `recipe_id`, optionally with `count`. Uses existing
  image enable/edit settings, quotas, operator confirmation rules, and sharing boundaries.
- `gif.inspect`: supply an output `image_id` and optional `maximum` (2–16, default 12).
  Returns measured metadata and a private contact-sheet image ID with sampling provenance.

```json
{
  "action": "gif.plan",
  "brief": {
    "subject": "Charcoal librarian cat and golden lantern",
    "scene": "Midnight teal library window",
    "motion": "Tail curls, pauses, then relaxes",
    "invariants": ["Cat face and books stay fixed", "Lantern stays on the desk"],
    "camera": "Locked medium shot",
    "loop": "Return to the starting pose without a jump",
    "beats": [
      {"description": "Tail curls upward", "frames": 3},
      {"description": "Gentle hold", "frames": 2},
      {"description": "Tail relaxes back to start", "frames": 4}
    ]
  },
  "render": {"frame_count": 9, "frame_duration_ms": 200, "size": "512x512", "quality": "medium"},
  "after": "continue"
}
```

Briefs require `subject` and `motion`; optional fields are `scene`, `camera`, `invariants`,
`loop`, `beats`, and shelf `reference_ids`. Beat allocations must sum to the frame count.
Beats and loop intentions guide the prompt; they do not guarantee exact timing or seamlessness.
Model routing remains operator-owned. Recipe text is intentionally saved as a resident draft,
independently of the generated-artifact prompt-retention setting.

Use existing `image.review`, `image.drawer`, and `image.share` for keeping, rejecting, annotating,
and sharing. Rendering or inspection never grants publication permission.

## Measured output and previews

New assets store byte-detected format/MIME, dimensions, byte size, actual frame count,
per-frame delays, total duration when known, and loop extension (0 means indefinite; absent is
null). Requested settings are separate because the encoder may coalesce frames or alter timing.
A static GIF or PNG returned for an animation request is an error. All outputs are validated
before any artifact is saved. Original bytes are never re-encoded.

Contact sheets are private derived shelf assets with parent ID and coverage. `image.inspect`
uses a labelled sheet for animated OCR/vision, returns sampled indices/timestamps, and includes
that recipe in cache identity. It observes sampled still frames, not motion between them.
Older rows retain their paths and MIME and have unknown new metadata until inspected; inspection
returns measured properties without rewriting old originals. Picture Drawer displays stored
timing and serves new GIF assets as image/gif.

## Operator configuration

Existing still models and defaults remain unchanged. New defaults:

```yaml
images:
  gif_model: gpt-image-2.6-flare  # also accepts gpt-image-2.6-sunburst
  max_gif_request_pixels: 16777216
  gif_auto_frame_pixels: 1048576
  max_input_bytes: 20000000
  max_decoded_frames: 256
  max_decoded_pixels: 67108864
```

Environment overrides: `VESTIGIA_GIF_MODEL`, `VESTIGIA_GIF_MAX_REQUEST_PIXELS`.
Request workload is width × height × frames × count. Auto size uses the configured per-frame
estimate; this is not a cost quote or a guarantee of actual dimensions. Returned bytes also face
encoded-byte and decoded-frame/pixel caps. Existing output-count quotas still apply.

The adapter contains preview fields in the official SDK's `extra_body` extension boundary,
retaining openai>=2.0.0. Returned usage is request-level evidence attached to output provenance;
do not sum the same request usage once per output.

Image SDK automatic retries are disabled. Jobs left running beyond `job_stale_seconds` become
failed with `RenderOutcomeUnknown` and appear in normal completion handling. They are not
automatically requeued: the provider may already have charged. Review before explicitly
rendering again. This change also applies to still-image jobs.

## Offline and live verification

No API key is required for fake generation:

```powershell
python -m vestigia.cli image generate PATH_TO_TEST_HOME "Lantern flickers" --fake --output-format gif --frame-count 3 --frame-duration-ms 200 --size 512x512
```

The fake provider emits distinct color frames for testing, not simulated artwork. Unit and
integration tests use synthetic files and simulated responses. Jeff performs live verification:

1. Confirm GIF preview access and accepted model/size/options with one small generation.
2. Separately test still-reference editing to GIF.
3. Check original playback, measured timing, queue completion, contact sheet, and private review.
4. Test explicit Discord/web delivery through existing sharing controls and platform size limits.

Reference checked 2026-11-06: [image guide](https://developers.openai.com/api/docs/guides/image-generation)
and [generate API reference](https://developers.openai.com/api/reference/python/resources/images/methods/generate).
Some alternate cached guide URLs disagree on GIF limits; this implementation uses the narrower
2–16 frame contract. Live provider acceptance has not been verified here.

Local retiming/reversal/ping-pong transformations and automated seam/flicker judgments remain
future work. This module creates no recurring render bells.
