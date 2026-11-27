from __future__ import annotations

from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .mcp_deployment_tools import require_private_mcp_turn
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
        return call("gramophone.inspect", {
            key: payload[key] for key in ("track_id", "expected_sha256", "start_ms", "end_ms")
        })

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
