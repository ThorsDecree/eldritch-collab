# Gramophone

Gramophone catalogs explicitly selected MP3 and WAV files in the live Archive and
measures passages. The original audio remains in place. Install `ffmpeg` and
`ffprobe` on the server's PATH.

The source prefix defaults to `Music` and may be changed with
`VESTIGIA_MCP_GRAMOPHONE_PREFIX`. It is relative to the live Archive. Copy audio
into that directory through your existing provisioning workflow.

- `gramophone.register(path, title, credits)` creates or refreshes one catalog entry.
- `gramophone.list(offset, limit)` returns track metadata, hashes, and a next offset.
- `gramophone.inspect(track_id, expected_sha256, start_ms, end_ms)` measures a
  passage of at most 60 seconds in 100 ms peak and RMS bins. It refuses a changed
  file or mismatched catalog version.

Registration assigns a stable ID to a path. Registering that path again refreshes
its metadata and digest while preserving the ID. Consumers should retain the digest
with observations, because a later registration may describe different bytes.

The catalog lives under the MCP state directory in `gramophone/catalog.sqlite3`.
The audio source is copied to a temporary directory for each decoder invocation and
the copy is removed afterward. The decoder receives a bounded local file, and the
returned measurements describe its mono 8 kHz PCM conversion. These values do not
identify instruments, lyrics or subjective qualities of the track.

The current MCP connection can discover these tools using its usual tool listing.
Runtime's generic dedicated-MCP doorway currently supports private conversational
turns. Shared persistent notes, direct audio delivery, a catalog browser, and
scheduled-turn access remain future work.
