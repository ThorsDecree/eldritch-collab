from __future__ import annotations

import base64
import hashlib
import json
import math
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .mcp_deployment_tools import _save_images, require_private_mcp_turn
from .utils import new_id, utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS gramophone_notes (
    note_id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(resident_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_gramophone_notes_track
ON gramophone_notes(resident_id, track_id, source_sha256, created_at);
CREATE TABLE IF NOT EXISTS gramophone_sessions (
    resident_id TEXT NOT NULL, track_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    duration_ms INTEGER NOT NULL, window_ms INTEGER NOT NULL, start_ms INTEGER NOT NULL,
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(resident_id, track_id, source_sha256)
);
CREATE TABLE IF NOT EXISTS gramophone_session_moves (
    resident_id TEXT NOT NULL, request_id TEXT NOT NULL, request_json TEXT NOT NULL,
    result_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(resident_id, request_id)
);
"""


def register_gramophone_tools(house: Any, client: Any) -> None:
    with house.db.connect() as connection:
        connection.executescript(SCHEMA)
    resident_id = str(house.config.get("resident.id"))

    def authorize(spec: CapabilitySpec, payload: dict[str, Any], context: dict[str, Any]) -> None:
        if (
            context.get("interface") == "bell"
            and context.get("invocation") == "conversation"
            and context.get("turn_id")
            and house.config.get("context_sources.mcp_archive.gramophone_bells_enabled", False)
        ):
            return
        require_private_mcp_turn(spec, payload, context)

    def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = client.call_tool(tool, arguments)
        data = result.get("structured_content")
        if not isinstance(data, dict):
            raise RuntimeError(f"{tool} did not return structured data")
        return data

    def browse(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        return call("gramophone.list", {"offset": payload.get("offset", 0), "limit": payload.get("limit", 20)})

    def inspect(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        result = call("gramophone.inspect", {
            key: payload[key] for key in ("track_id", "expected_sha256", "start_ms", "end_ms")
        })
        bins = result.get("bins", [])
        if len(bins) > 100:
            factor = math.ceil(len(bins) / 100)
            bin_ms = int(result["bin_ms"])
            duration_ms = int(result["end_ms"]) - int(result["start_ms"])
            combined = []
            for start in range(0, len(bins), factor):
                group = bins[start:start + factor]
                weights = [
                    min(bin_ms, max(0, duration_ms - (start + index) * bin_ms))
                    for index in range(len(group))
                ]
                total = sum(weights)
                if not total:
                    raise ValueError("Gramophone returned bins outside the requested passage")
                combined.append({
                    "peak": max(item["peak"] for item in group),
                    "rms": round(math.sqrt(sum(
                        item["rms"] ** 2 * weight for item, weight in zip(group, weights)
                    ) / total), 5),
                })
            result["bins"] = combined
            result["source_bin_ms"] = bin_ms
            result["bin_ms"] = factor * bin_ms
            result["aggregation"] = "Maximum peak and duration-weighted RMS"
        return result

    def spectrogram(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        arguments = {key: payload[key] for key in ("track_id", "expected_sha256", "start_ms", "end_ms")}
        response = client.call_tool("gramophone.spectrogram", arguments)
        text = response.get("text_blocks", [])
        images = response.get("image_blocks", [])
        if len(text) != 1 or len(images) != 1:
            raise RuntimeError("Gramophone did not return one spectrogram with its metadata")
        metadata = json.loads(text[0])
        if not isinstance(metadata, dict) or any((
            metadata.get("track_id") != arguments["track_id"],
            metadata.get("sha256") != arguments["expected_sha256"],
            metadata.get("start_ms") != arguments["start_ms"],
            metadata.get("end_ms") != arguments["end_ms"],
            images[0].get("mime_type") != "image/png",
        )):
            raise RuntimeError("Gramophone spectrogram provenance does not match the request")
        image_bytes = base64.b64decode(images[0]["data"], validate=True)
        if hashlib.sha256(image_bytes).hexdigest() != metadata.get("image_sha256"):
            raise RuntimeError("Gramophone spectrogram image digest does not match")
        saved = _save_images(house, "gramophone.spectrogram", images, {
            "track_id": arguments["track_id"], "source_sha256": arguments["expected_sha256"],
            "start_ms": arguments["start_ms"], "end_ms": arguments["end_ms"],
        })
        return {**metadata, "retention": "runtime_private_image_shelf", "images": saved,
                "next_step": "Use image.inspect on the image ID to examine the plot."}

    def add_note(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        content = payload["content"].strip()
        if not content:
            raise ValueError("A listening note must contain text")
        request_id = payload["request_id"].strip()
        if not request_id:
            raise ValueError("A listening note needs a retry key")
        start, end = payload["start_ms"], payload["end_ms"]
        if end <= start:
            raise ValueError("The note must have an increasing time range")
        track_id, digest = payload["track_id"], payload["source_sha256"]
        # An existing key can be recovered even if the source has since changed.
        with house.db.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM gramophone_notes WHERE resident_id=? AND request_id=?",
                (resident_id, request_id),
            ).fetchone()
        if existing:
            note = dict(existing)
            expected = (track_id, digest, start, end, content)
            actual = tuple(note[key] for key in ("track_id", "source_sha256", "start_ms", "end_ms", "content"))
            if actual != expected:
                raise ValueError("Retry key already belongs to a different listening note")
            return {"note": note, "reused": True}
        track = call("gramophone.track", {"track_id": track_id, "verify_source": True})
        if track.get("sha256") != digest:
            raise ValueError("Track digest differs from the current catalog")
        if end > int(track["duration_ms"]):
            raise ValueError("The note range extends past the track")
        note = {
            "note_id": new_id("music_note"), "resident_id": resident_id, "request_id": request_id,
            "track_id": track_id, "source_sha256": digest, "start_ms": start,
            "end_ms": end, "content": content, "created_at": utc_now_iso(),
        }
        with house.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM gramophone_notes WHERE resident_id=? AND request_id=?",
                (resident_id, request_id),
            ).fetchone()
            if existing:
                saved = dict(existing)
                expected = (track_id, digest, start, end, content)
                actual = tuple(saved[key] for key in ("track_id", "source_sha256", "start_ms", "end_ms", "content"))
                if actual != expected:
                    raise ValueError("Retry key already belongs to a different listening note")
                return {"note": saved, "reused": True}
            connection.execute(
                """INSERT INTO gramophone_notes
                (note_id,resident_id,request_id,track_id,source_sha256,start_ms,end_ms,content,created_at)
                VALUES (:note_id,:resident_id,:request_id,:track_id,:source_sha256,:start_ms,:end_ms,:content,:created_at)""",
                note,
            )
        return {"note": note, "reused": False}

    def list_notes(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        limit = payload.get("limit", 20)
        offset = payload.get("offset", 0)
        with house.db.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM gramophone_notes
                WHERE resident_id=? AND track_id=? AND source_sha256=?
                ORDER BY created_at, note_id LIMIT ? OFFSET ?""",
                (resident_id, payload["track_id"], payload["source_sha256"], limit + 1, offset),
            ).fetchall()
        return {"notes": [dict(row) for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}

    def session_key(payload: dict[str, Any]) -> tuple[str, str, str]:
        return resident_id, payload["track_id"], payload["source_sha256"]

    def session_snapshot(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["end_ms"] = min(item["start_ms"] + item["window_ms"], item["duration_ms"])
        item["previous_start_ms"] = max(0, item["start_ms"] - item["window_ms"]) if item["start_ms"] else None
        item["next_start_ms"] = item["end_ms"] if item["end_ms"] < item["duration_ms"] else None
        return item

    def verify_track(payload: dict[str, Any]) -> dict[str, Any]:
        track = call("gramophone.track", {"track_id": payload["track_id"], "verify_source": True})
        if track.get("sha256") != payload["source_sha256"]:
            raise ValueError("Track digest differs from the current catalog")
        return track

    def open_session(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        track = verify_track(payload)
        duration = int(track["duration_ms"])
        start = payload.get("start_ms", 0)
        window = payload.get("window_ms", 15000)
        if start >= duration:
            raise ValueError("The starting position must be inside the track")
        key = session_key(payload)
        with house.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM gramophone_sessions WHERE resident_id=? AND track_id=? AND source_sha256=?", key,
            ).fetchone()
            if existing:
                return {"session": session_snapshot(existing), "resumed": True}
            connection.execute(
                "INSERT INTO gramophone_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*key, duration, window, start, 1, utc_now_iso()),
            )
            row = connection.execute(
                "SELECT * FROM gramophone_sessions WHERE resident_id=? AND track_id=? AND source_sha256=?", key,
            ).fetchone()
        return {"session": session_snapshot(row), "resumed": False}

    def view_session(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        key = session_key(payload)
        limit, offset = payload.get("limit", 20), payload.get("offset", 0)
        with house.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM gramophone_sessions WHERE resident_id=? AND track_id=? AND source_sha256=?", key,
            ).fetchone()
            if not row:
                raise ValueError("No listening session exists for this track version")
            session = session_snapshot(row)
            notes = connection.execute(
                """SELECT * FROM gramophone_notes WHERE resident_id=? AND track_id=? AND source_sha256=?
                AND start_ms < ? AND end_ms > ? ORDER BY start_ms, created_at, note_id LIMIT ? OFFSET ?""",
                (*key, session["end_ms"], session["start_ms"], limit + 1, offset),
            ).fetchall()
        return {"session": session, "notes": [dict(note) for note in notes[:limit]],
                "next_offset": offset + limit if len(notes) > limit else None,
                "source_verified": False}

    def move_session(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        request_id = payload["request_id"].strip()
        if not request_id:
            raise ValueError("A session move needs a retry key")
        mode = payload["mode"]
        if (mode == "seek") != ("start_ms" in payload):
            raise ValueError("Supply start_ms only when seeking")
        if (mode == "resize") != ("window_ms" in payload):
            raise ValueError("Supply window_ms only when resizing")
        request = {key: payload[key] for key in ("track_id", "source_sha256", "expected_revision", "mode")}
        if mode == "seek":
            request["start_ms"] = payload["start_ms"]
        if mode == "resize":
            request["window_ms"] = payload["window_ms"]
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))

        def replay(connection: Any) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT request_json, result_json FROM gramophone_session_moves WHERE resident_id=? AND request_id=?",
                (resident_id, request_id),
            ).fetchone()
            if row is None:
                return None
            if row["request_json"] != encoded:
                raise ValueError("Retry key already belongs to a different session move")
            return {**json.loads(row["result_json"]), "reused": True}

        with house.db.connect() as connection:
            prior = replay(connection)
        if prior is not None:
            return prior
        track = verify_track(payload)
        key = session_key(payload)
        with house.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = replay(connection)
            if prior is not None:
                return prior
            row = connection.execute(
                "SELECT * FROM gramophone_sessions WHERE resident_id=? AND track_id=? AND source_sha256=?", key,
            ).fetchone()
            if not row:
                raise ValueError("No listening session exists for this track version")
            current = session_snapshot(row)
            if current["revision"] != payload["expected_revision"]:
                raise ValueError("Listening session revision changed; view it before moving")
            if int(track["duration_ms"]) != current["duration_ms"]:
                raise ValueError("Catalog duration differs from the listening session")
            window = current["window_ms"]
            if mode == "seek":
                start = payload["start_ms"]
                if start >= current["duration_ms"]:
                    raise ValueError("The requested position must be inside the track")
            elif mode == "resize":
                start, window = current["start_ms"], payload["window_ms"]
            else:
                start = current["next_start_ms" if mode == "next" else "previous_start_ms"]
            boundary = start is None
            if not boundary and (start != current["start_ms"] or window != current["window_ms"]):
                connection.execute(
                    """UPDATE gramophone_sessions SET start_ms=?, window_ms=?, revision=revision+1, updated_at=?
                    WHERE resident_id=? AND track_id=? AND source_sha256=?""",
                    (start, window, utc_now_iso(), *key),
                )
            row = connection.execute(
                "SELECT * FROM gramophone_sessions WHERE resident_id=? AND track_id=? AND source_sha256=?", key,
            ).fetchone()
            result = {"session": session_snapshot(row), "boundary": boundary}
            connection.execute(
                "INSERT INTO gramophone_session_moves VALUES (?, ?, ?, ?, ?)",
                (resident_id, request_id, encoded, json.dumps(result, sort_keys=True), utc_now_iso()),
            )
        return {**result, "reused": False}

    track = {"type": "string", "pattern": "track_[0-9a-f]{32}"}
    digest = {"type": "string", "pattern": "[0-9a-f]{64}"}
    millis = {"type": "integer", "minimum": 0, "maximum": 600000}
    pagination = {"offset": {"type": "integer", "minimum": 0},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 50}}
    definitions = (
        ("gramophone.list", "Browse cataloged tracks.", pagination, (), browse, ("process:spawn_local",)),
        ("gramophone.inspect", "Measure a passage of a cataloged track.", {
            "track_id": track, "expected_sha256": digest, "start_ms": millis, "end_ms": millis,
        }, ("track_id", "expected_sha256", "start_ms", "end_ms"), inspect, ("process:spawn_local",)),
        ("gramophone.spectrogram", "Render a private time-frequency plot for a cataloged passage.", {
            "track_id": track, "expected_sha256": digest, "start_ms": millis, "end_ms": millis,
        }, ("track_id", "expected_sha256", "start_ms", "end_ms"), spectrogram,
         ("process:spawn_local", "filesystem:write_private_image_cache")),
        ("gramophone.session.open", "Open or resume a private, versioned listening cursor.",
         {"track_id": track, "source_sha256": digest, "start_ms": millis,
          "window_ms": {"type": "integer", "minimum": 250, "maximum": 60000}},
         ("track_id", "source_sha256"), open_session, ("database:write", "process:spawn_local")),
        ("gramophone.session.view", "View your current passage and overlapping private notes.",
         {"track_id": track, "source_sha256": digest, **pagination},
         ("track_id", "source_sha256"), view_session, ("database:read",)),
        ("gramophone.session.move", "Move, seek, or resize the listening window; use the current revision and a retry key.",
         {"track_id": track, "source_sha256": digest,
          "expected_revision": {"type": "integer", "minimum": 1},
          "mode": {"type": "string", "enum": ["next", "previous", "seek", "resize"]}, "start_ms": millis,
          "window_ms": {"type": "integer", "minimum": 250, "maximum": 60000},
          "request_id": {"type": "string", "minLength": 1, "maxLength": 120}},
         ("track_id", "source_sha256", "expected_revision", "mode", "request_id"),
         move_session, ("database:write", "process:spawn_local")),
        ("gramophone.note.add", "Save a private listening note. Reuse the same request_id to retry.",
         {"track_id": track, "source_sha256": digest, "start_ms": millis, "end_ms": millis,
          "content": {"type": "string", "minLength": 1, "maxLength": 4000},
          "request_id": {"type": "string", "minLength": 1, "maxLength": 120}},
         ("track_id", "source_sha256", "start_ms", "end_ms", "content", "request_id"),
         add_note, ("database:write", "process:spawn_local")),
        ("gramophone.note.list", "Read your own listening notes for an exact track version.",
         {"track_id": track, "source_sha256": digest, **pagination},
         ("track_id", "source_sha256"), list_notes, ("database:read",)),
    )
    example_track = "track_" + "0" * 32
    example_hash = "0" * 64
    examples = {
        "gramophone.list": {"limit": 20},
        "gramophone.inspect": {
            "track_id": example_track, "expected_sha256": example_hash,
            "start_ms": 0, "end_ms": 1000,
        },
        "gramophone.spectrogram": {
            "track_id": example_track, "expected_sha256": example_hash,
            "start_ms": 0, "end_ms": 1000,
        },
        "gramophone.session.open": {"track_id": example_track, "source_sha256": example_hash, "window_ms": 15000},
        "gramophone.session.view": {"track_id": example_track, "source_sha256": example_hash},
        "gramophone.session.move": {"track_id": example_track, "source_sha256": example_hash,
                                     "expected_revision": 1, "mode": "next", "request_id": "my-move-1"},
        "gramophone.note.add": {
            "track_id": example_track, "source_sha256": example_hash,
            "start_ms": 0, "end_ms": 1000, "content": "My observation",
            "request_id": "my-unique-note-key",
        },
        "gramophone.note.list": {
            "track_id": example_track, "source_sha256": example_hash,
        },
    }
    for name, description, fields, required, handler, effects in definitions:
        house.registry.register(
            CapabilitySpec(
                name=name, description=description, effects=effects,
                confirmation="resident_authenticated_doorway",
                group="mcp", result_visibility="resident_private",
                input_schema=object_schema(
                    {"action": {"type": "string", "const": name}, **fields,
                     "after": {"type": "string", "enum": ["continue", "finish"]}},
                    required=("action", *required),
                ),
                example_envelopes=({"action": name, **examples[name], "after": "continue"},),
                next_step="Continue listening or inspect another Gramophone capability.",
            ),
            handler,
            authorizer=authorize,
        )
