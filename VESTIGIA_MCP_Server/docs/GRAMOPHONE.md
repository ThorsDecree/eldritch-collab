# Gramophone

Gramophone catalogs explicitly selected MP3 and WAV files in the live Archive and
measures passages. The original audio remains in place. Install `ffmpeg` and
`ffprobe` on the server's PATH.

The source prefix defaults to `Music` and may be changed with
`VESTIGIA_MCP_GRAMOPHONE_PREFIX`. It is relative to the live Archive. Copy audio
into that directory through your existing provisioning workflow.

- `gramophone.register(path, title, credits)` creates or refreshes one catalog entry.
- `gramophone.list(offset, limit)` returns track metadata, hashes, and a next offset.
- `gramophone.track(track_id, verify_source)` returns one catalog entry and
  optionally checks its current source against the recorded digest.
- `gramophone.inspect(track_id, expected_sha256, start_ms, end_ms)` measures a
  passage of at most 60 seconds in 100 ms peak and RMS bins. It refuses a changed
  file or mismatched catalog version.
- `gramophone.rhythm(track_id, expected_sha256, start_ms, end_ms)` estimates
  energy attacks and possible rhythmic periodicities for a passage of 250 ms to
  60 seconds. Timestamps are absolute track milliseconds. The first passage
  frame is not labeled as an attack because its preceding audio is unavailable.
  At most 100 attacks are returned; `attack_count` and `attacks_truncated` show
  when to inspect a shorter range. Tempo candidates require at least five
  seconds and four attacks, and can be empty. The periodicity scores are
  autocorrelation measurements, not calibrated probabilities. Half/double
  tempo, syncopation, low-energy attacks and steady notes make the estimate
  uncertain; the method does not identify instruments or provide audio input.
- `gramophone.pocket(track_id, expected_sha256, start_ms, end_ms, bpm, anchor_ms,
  subdivisions, offset, limit)` compares estimated energy attacks against an
  explicitly supplied, fixed timing grid. `anchor_ms` is a track-relative grid
  line; `subdivisions` is 1, 2, or 4 per beat. The caller may use an estimated
  tempo but must choose the grid; the tool does not verify the tempo or phase.
  Each attack has its nearest grid time and signed offset in milliseconds: positive
  is late relative to the chosen grid, negative is early. The grid extends in both
  directions from its anchor. A midpoint tie selects the later grid line.
  Results are paged and repeat source hash, range, and grid on each page. Each
  page is regenerated against the source and rejected if its digest changed.
  Measurements are quantized to 10 ms frames and attacks closer than 80 ms can
  merge or be missed; numeric precision is not temporal accuracy. These events
  do not identify instruments, swing, or intentional groove. The initial frame
  has no preceding audio for comparison. Request another grid to compare readings.
- `gramophone.stereo(track_id, expected_sha256, start_ms, end_ms)` measures a
  250 ms–60 s passage in one-second windows plus a passage-wide summary. For a
  stereo source, it returns left/right RMS, uncentered channel correlation,
  mid/side RMS, side-energy fraction, and balance `(R_RMS - L_RMS) / (R_RMS +
  L_RMS)`. RMS values are normalized to full scale; mid is `(L + R) / 2`, side
  is `(L - R) / 2`, and the fraction is side energy divided by mid plus side
  energy. Correlation is null if either channel has zero energy; balance and
  side-energy fraction are null if both are digitally silent. A mono source has
  mono RMS and explicitly reports stereo measurements as unavailable. Time
  ranges are track-relative, and decoded duration is recorded. Analysis uses
  16 kHz PCM, so content above 8 kHz is not represented. These measurements do
  not identify instrument locations or establish perceived width or motion;
  side energy can also reflect phase differences. The source remains unchanged.
- `gramophone.harmony(track_id, expected_sha256, start_ms, end_ms)` explores
  pitch-class energy in a 250 ms–60 s passage. It divides mono 8 kHz PCM into
  one-second windows, applies a Hann-windowed 2048-point FFT, and maps 80–3000 Hz
  bins to twelve equal-tempered pitch classes at A4=440 Hz. The normalized energy
  describes the spectrum; it is not a list of played notes. Quiet windows below
  0.001 RMS, or with less than 0.001 of spectral energy in the analyzed band,
  return no profile or chord candidates. Each active window may return
  up to three major/minor triad template matches when the template contains at
  least 0.42 of energy and each chord tone has at least 0.08. These thresholds are
  heuristics, and the overlap scores are not calibrated probabilities. A single
  note's overtones can resemble a triad. Other chord types are not modeled.
  `change_from_previous` is cosine distance between adjacent active spectral
  profiles (0 means identical, 1 maximally dissimilar for nonnegative profiles);
  a quiet window breaks the comparison. Changes can reflect timbre or percussion,
  not just harmony, and are not an emotional-tension measurement. Mono downmix
  can cancel out-of-phase material. Source hashes and requested and decoded times
  accompany the result.
- `gramophone.spectrogram(track_id, expected_sha256, start_ms, end_ms)` returns
  a PNG and metadata for a passage between 250 ms and 60 seconds. Its horizontal
  axis is seconds from the requested start, and its vertical axis is logarithmic
  frequency. Mono 16 kHz conversion limits the plot to 8 kHz; it cannot recover
  frequencies missing from the source. The source and image hashes travel with
  the time range. The MCP server keeps no copy of the image.

Registration assigns a stable ID to a path. Registering that path again refreshes
its metadata and digest while preserving the ID. Consumers should retain the digest
with observations, because a later registration may describe different bytes.

The catalog lives under the MCP state directory in `gramophone/catalog.sqlite3`.
The audio source is copied to a temporary directory for each decoder invocation and
the copy is removed afterward. The decoder receives a bounded local file, and the
returned measurements describe its mono 8 kHz PCM conversion. These values do not
identify instruments, lyrics or subjective qualities of the track.

The current MCP connection can discover these tools using its usual tool listing.
With a dedicated MCP deployment configured, Runtime also exposes focused
`gramophone.list`, `gramophone.inspect`, `gramophone.rhythm`, `gramophone.pocket`,
`gramophone.stereo`, `gramophone.harmony`, `gramophone.spectrogram`, `gramophone.note.add`, and
`gramophone.note.list` capabilities. Notes belong to the Runtime resident and
stay in that Runtime home's continuity database. Notes identify the track and
exact source digest plus a start and end time in milliseconds.

For `gramophone.note.add`, provide a unique `request_id` and reuse it on retry.
Repeating the same request returns the saved note. Reusing it with different
content or coordinates is rejected. Note listing filters by the resident and
track version. The note text is excluded from general capability receipts and
house events, though the resident's current tool result contains the text.
Runtime prompt-capture settings and provider processing still apply.

Set `VESTIGIA_GRAMOPHONE_BELLS_ENABLED=true` in the Runtime environment to
allow the focused capabilities during scheduled bell turns. Private conversations
may use them without that setting. The spectrogram enters the Runtime resident’s
private image shelf. Use `image.inspect` and a configured vision route to interpret
it; producing the image alone does not send it to a model. Vision may incur provider
costs. The broader `mcp.call` capability retains its
own private-conversation policy. Direct audio delivery, a shared browser, and
authorized note export remain future work.

Runtime combines longer passage measurements to at most 100 bins using maximum
peak and duration-weighted RMS, so its durable receipt preserves the full range.
The MCP tool retains its 100 ms bins.

## Listening cursor

The focused Runtime capabilities also provide a private, durable cursor for each
resident and exact track digest. `gramophone.session.open` verifies the current
source and starts a session with a 250 ms to 60 s window, or resumes the saved
cursor without changing its existing position or window. `gramophone.session.view`
returns the current range and paginated notes that overlap it, in time order.
A view is available even if the source has changed or become unavailable and
reports `source_verified: false`; analysis tools independently recheck the source.
Note text is removed from general session-view receipts and house events, while
normal Runtime prompt-capture settings and provider processing still apply.

`gramophone.session.move` supports `next`, `previous`, `seek` with an explicit
`start_ms`, and `resize` with an explicit `window_ms`. It checks the source and requires the current session
`expected_revision`. Each move has a caller-chosen `request_id`; a retry with the
same arguments returns the originally committed result, even after a restart or
source change. Reusing the key for a different move fails. Concurrent moves at the
same revision cannot both change the cursor. A boundary move reports `boundary`
without advancing the revision. A successful retry returns the original result,
which may be older than the current session; use `session.view` for current state.

Use the returned track ID, digest, start and end with `gramophone.inspect`,
`gramophone.rhythm`, `gramophone.pocket`, `gramophone.stereo`, `gramophone.harmony`, `gramophone.spectrogram`,
or `gramophone.note.add`. Notes remain private to the
resident and attached to their own ranges and track version. These tools do not
deliver actual audio to a model or provide a graphical playback interface.
