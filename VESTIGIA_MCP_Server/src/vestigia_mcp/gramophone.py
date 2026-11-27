from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import subprocess
import tempfile
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.archive import ArchiveError, ArchiveSource, normalize_relative_path
from .gramophone_rhythm import measure_pocket, measure_rhythm
from .gramophone_stereo import measure_stereo


MAX_BYTES = 30 * 1024 * 1024
MAX_DURATION_MS = 600_000
MAX_PASSAGE_MS = 60_000
SAMPLE_RATE = 8_000


class GramophoneService:
    """Local audio catalog and bounded, reproducible passage measurements."""

    def __init__(self, source: ArchiveSource, state_dir: Path, prefix: str):
        self.source = source
        self.state_dir = state_dir / "gramophone"
        self.prefix = normalize_relative_path(prefix)

    def _connect(self) -> sqlite3.Connection:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.state_dir / "catalog.sqlite3", timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tracks (
                track_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                credits TEXT NOT NULL, sha256 TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL, codec TEXT NOT NULL
            )"""
        )
        return connection

    def _bytes(self, path: str) -> tuple[str, bytes]:
        relative = normalize_relative_path(path)
        if not (relative == self.prefix or relative.startswith(self.prefix + "/")):
            raise ArchiveError("Track is outside the configured Gramophone prefix")
        if PurePosixPath(relative).suffix.lower() not in {".mp3", ".wav"}:
            raise ArchiveError("Gramophone supports MP3 and WAV")
        if self.source.kind != "directory":
            raise ArchiveError("Gramophone requires a live directory Archive")
        cursor = self.source.root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ArchiveError("Symlink paths are not supported by Gramophone")
        with self.source._stream_member(relative) as (stream, size, _revision):
            if size <= 0 or size > MAX_BYTES:
                raise ArchiveError("Audio file exceeds the Gramophone size limit or is empty")
            data = stream.read(MAX_BYTES + 1)
        if len(data) != size:
            raise ArchiveError("Audio file changed while it was read")
        return relative, data

    @staticmethod
    def _run(command: list[str], *, input_data: bytes | None = None) -> bytes:
        try:
            result = subprocess.run(command, input=input_data, capture_output=True, timeout=60)
        except FileNotFoundError as exc:
            raise ArchiveError("Gramophone requires ffmpeg and ffprobe on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise ArchiveError("Audio processing timed out") from exc
        if result.returncode:
            raise ArchiveError("Audio decoder could not process this file")
        return result.stdout

    def register(self, path: str, title: str, credits: str = "") -> dict[str, Any]:
        if not title.strip() or len(title) > 200 or len(credits) > 300:
            raise ArchiveError("Track title or credits are invalid")
        relative, content = self._bytes(path)
        digest = hashlib.sha256(content).hexdigest()
        suffix = PurePosixPath(relative).suffix.lower()
        with tempfile.TemporaryDirectory(prefix="gramophone-") as temporary:
            copy = Path(temporary) / ("track" + suffix)
            copy.write_bytes(content)
            probe = self._run([
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-f", suffix[1:], "-select_streams", "a:0", "-show_entries",
                "format=duration:stream=codec_name,sample_rate,channels", "-of", "json",
                str(copy),
            ])
        try:
            metadata = json.loads(probe)
            stream, = metadata["streams"]
            duration = float(metadata["format"]["duration"])
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
            codec = str(stream["codec_name"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ArchiveError("Audio metadata is incomplete") from exc
        if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION_MS / 1000:
            raise ArchiveError("Audio duration is invalid or exceeds ten minutes")
        if channels not in (1, 2) or not 8_000 <= sample_rate <= 192_000:
            raise ArchiveError("Unsupported audio sample rate or channel count")
        # SQLite serializes concurrent upserts. An edited source remains the same track,
        # but its changed digest is visible to consumers and old version references fail.
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT track_id FROM tracks WHERE path=?", (relative,)).fetchone()
            track_id = row["track_id"] if row else "track_" + uuid.uuid4().hex
            db.execute(
                """INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET title=excluded.title, credits=excluded.credits,
                   sha256=excluded.sha256, duration_ms=excluded.duration_ms,
                   sample_rate=excluded.sample_rate, channels=excluded.channels, codec=excluded.codec""",
                (track_id, relative, title.strip(), credits.strip(), digest,
                 round(duration * 1000), sample_rate, channels, codec),
            )
        return self.get(track_id)

    def get(self, track_id: str, *, verify_source: bool = False) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone()
        if row is None:
            raise ArchiveError("Unknown Gramophone track ID")
        track = dict(row)
        if verify_source:
            _, content = self._bytes(track["path"])
            if hashlib.sha256(content).hexdigest() != track["sha256"]:
                raise ArchiveError("Track source changed since it was cataloged")
        return track

    def list_tracks(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ArchiveError("Invalid Gramophone pagination")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM tracks ORDER BY title COLLATE NOCASE, track_id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        return {
            "tracks": [dict(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def _decode_passage(self, track_id: str, expected_sha256: str, start_ms: int,
                        end_ms: int, sample_rate: int, channels: int = 1) -> bytes:
        track = self.get(track_id)
        if expected_sha256 != track["sha256"]:
            raise ArchiveError("Requested track digest differs from the catalog")
        if (isinstance(start_ms, bool) or isinstance(end_ms, bool) or
            not isinstance(start_ms, int) or not isinstance(end_ms, int) or
            start_ms < 0 or end_ms <= start_ms or end_ms > track["duration_ms"] or
            end_ms - start_ms > MAX_PASSAGE_MS):
            raise ArchiveError("Passage must be an increasing range within the track and at most 60 seconds")
        _, content = self._bytes(track["path"])
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArchiveError("Track source changed since it was cataloged")
        suffix = PurePosixPath(track["path"]).suffix.lower()
        with tempfile.TemporaryDirectory(prefix="gramophone-") as temporary:
            copy = Path(temporary) / ("track" + suffix)
            copy.write_bytes(content)
            raw = self._run([
                "ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-f", suffix[1:], "-i", str(copy), "-ss", f"{start_ms / 1000:.3f}",
                "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-map", "0:a:0",
                "-ac", str(channels), "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
            ])
        expected_samples = round((end_ms - start_ms) * sample_rate / 1000)
        if len(raw) % (2 * channels) or len(raw) // (2 * channels) < expected_samples - sample_rate // 20:
            raise ArchiveError("Decoded passage was incomplete")
        return raw

    def inspect(self, track_id: str, expected_sha256: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        raw = self._decode_passage(track_id, expected_sha256, start_ms, end_ms, SAMPLE_RATE)
        samples = struct.iter_unpack("<h", raw)
        bins = []
        squares = 0
        maximum = 0
        count = 0
        for (sample,) in samples:
            squares += sample * sample
            maximum = max(maximum, abs(sample))
            count += 1
            if count == SAMPLE_RATE // 10:
                bins.append({"peak": round(maximum / 32768, 5),
                             "rms": round(math.sqrt(squares / count) / 32768, 5)})
                count = squares = maximum = 0
        if count:
            bins.append({"peak": round(maximum / 32768, 5),
                         "rms": round(math.sqrt(squares / count) / 32768, 5)})
        return {
            "track_id": track_id, "sha256": expected_sha256,
            "start_ms": start_ms, "end_ms": end_ms,
            "method": "ffmpeg mono 8000 Hz signed PCM; peak and RMS in 100 ms bins",
            "bin_ms": 100, "bins": bins,
        }

    def rhythm(self, track_id: str, expected_sha256: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        raw = self._decode_passage(track_id, expected_sha256, start_ms, end_ms, SAMPLE_RATE)
        if end_ms - start_ms < 250:
            raise ArchiveError("Rhythm passages must be at least 250 ms")
        return {"track_id": track_id, "sha256": expected_sha256,
                "start_ms": start_ms, "end_ms": end_ms, **measure_rhythm(raw, start_ms, end_ms)}

    def pocket(self, track_id: str, expected_sha256: str, start_ms: int, end_ms: int,
               bpm: float, anchor_ms: int, subdivisions: int = 1, offset: int = 0,
               limit: int = 50) -> dict[str, Any]:
        if (isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or
            not math.isfinite(bpm) or not 40 <= bpm <= 240):
            raise ArchiveError("Grid BPM must be finite and between 40 and 240")
        if (isinstance(anchor_ms, bool) or not isinstance(anchor_ms, int) or anchor_ms < 0 or
            isinstance(subdivisions, bool) or not isinstance(subdivisions, int) or subdivisions not in (1, 2, 4) or
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100):
            raise ArchiveError("Invalid grid anchor, subdivisions, or pagination")
        track = self.get(track_id)
        if anchor_ms > track["duration_ms"]:
            raise ArchiveError("Grid anchor must be within the track")
        raw = self._decode_passage(track_id, expected_sha256, start_ms, end_ms, SAMPLE_RATE)
        if end_ms - start_ms < 250:
            raise ArchiveError("Pocket passages must be at least 250 ms")
        return {"track_id": track_id, "sha256": expected_sha256,
                "start_ms": start_ms, "end_ms": end_ms,
                **measure_pocket(raw, start_ms, bpm, anchor_ms, subdivisions, offset, limit)}

    def stereo(self, track_id: str, expected_sha256: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        track = self.get(track_id)
        channels = track["channels"]
        raw = self._decode_passage(track_id, expected_sha256, start_ms, end_ms, 16_000, channels)
        if end_ms - start_ms < 250:
            raise ArchiveError("Stereo passages must be at least 250 ms")
        return {"track_id": track_id, "sha256": expected_sha256,
                "start_ms": start_ms, "end_ms": end_ms, **measure_stereo(raw, channels, start_ms, end_ms)}

    def spectrogram(self, track_id: str, expected_sha256: str, start_ms: int,
                    end_ms: int) -> tuple[dict[str, Any], bytes]:
        # The duration bounds and source digest are checked before any decoding.
        raw = self._decode_passage(track_id, expected_sha256, start_ms, end_ms, 16_000)
        if end_ms - start_ms < 250:
            raise ArchiveError("Spectrogram passages must be at least 250 ms")
        image = self._run([
            "ffmpeg", "-v", "error", "-f", "s16le", "-ar", "16000", "-ac", "1",
            "-i", "pipe:0", "-lavfi",
            "showspectrumpic=s=900x420:legend=1:fscale=log:scale=log:color=magma:start=40:stop=8000",
            "-frames:v", "1", "-threads", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
        ], input_data=raw)
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 4 * 1024 * 1024:
            raise ArchiveError("Spectrogram renderer returned an invalid image")
        return ({
            "track_id": track_id, "sha256": expected_sha256,
            "start_ms": start_ms, "end_ms": end_ms,
            "method": "ffmpeg showspectrumpic; mono, 16000 Hz; logarithmic frequency and magnitude",
            "frequency_min_hz": 40, "frequency_max_hz": 8000,
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "mime_type": "image/png", "retention": "response_only",
            "time_axis": "seconds relative to start_ms",
        }, image)
