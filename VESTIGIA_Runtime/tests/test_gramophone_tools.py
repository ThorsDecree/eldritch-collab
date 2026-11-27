from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.mcp_stdio import McpStdioClient
from vestigia.models import NormalizedMessage
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


pytestmark = pytest.mark.skipif(
    any(importlib.util.find_spec(name) is None for name in ("mcp", "vestigia_mcp"))
    or not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="requires the sibling MCP server, mcp-context, ffmpeg and ffprobe",
)


@pytest.fixture
def listening_house(tmp_path, monkeypatch):
    archive = tmp_path / "Archive"
    (archive / "Music").mkdir(parents=True)
    track_file = archive / "Music" / "fixture.wav"
    with wave.open(str(track_file), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * index / 8000)))
            for index in range(8000 * 3)
        ))
    profile = tmp_path / "mcp.env"
    profile.write_text(
        f"VESTIGIA_MCP_LIVE_ARCHIVE_ROOT={archive}\n"
        f"VESTIGIA_MCP_STATE_DIR={tmp_path / 'mcp-state'}\n"
        "VESTIGIA_MCP_DEPLOYMENT_ID=gramophone-test\n", encoding="utf-8"
    )
    monkeypatch.setenv("VESTIGIA_CONTEXT_MCP_DEPLOYMENT_ENV_FILE", str(profile))
    monkeypatch.setenv("VESTIGIA_CONTEXT_MCP_ENABLED", "false")
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "true")
    monkeypatch.setenv("VESTIGIA_CURATION_ENABLED", "false")
    home = initialize_home(tmp_path / "Home", name="Resident")
    client = McpStdioClient(30, args=("--deployment-env", str(profile)))
    track = client.call_tool("gramophone.register", {
        "path": "Music/fixture.wav", "title": "Synthetic track",
    })["structured_content"]
    return home, track_file, track, client


def test_bell_writes_versioned_private_note_and_reopens_after_restart(listening_house):
    home, track_file, track, client = listening_house
    action = {
        "action": "gramophone.note.add", "track_id": track["track_id"],
        "source_sha256": track["sha256"], "start_ms": 500, "end_ms": 1500,
        "content": "A private synthetic observation", "request_id": "bell-test-1",
    }
    provider = FakeProvider([
        '[[TOOL_ACTION ' + json.dumps({
            "action": "gramophone.inspect", "track_id": track["track_id"],
            "expected_sha256": track["sha256"], "start_ms": 500, "end_ms": 1500,
            "after": "continue",
        }) + ']]',
        '[[TOOL_ACTION ' + json.dumps({**action, "after": "continue"}) + ']]',
        "The note is tucked away.",
    ])
    runtime = CoreRuntime(load_config(home), provider=provider, fake=True)
    message = NormalizedMessage(
        content="Spend time with the track", interface="bell", speaker_id="bell:test",
        room_id=runtime.room_id, external_id="bell-run-test",
    )
    result = runtime.chat(message)
    evidence = runtime.house.legible.list_receipts(turn_id=result.turn_id, action="gramophone.inspect")
    assert len(evidence) == 1
    measured = runtime.house.legible.inspect_receipt(evidence[0]["id"])
    assert measured["status"] == "succeeded"
    assert measured["result"]["sha256"] == track["sha256"]
    assert len(measured["result"]["bins"]) == 10
    assert "bin_ms" in json.dumps(provider.requests[1].messages)
    receipts = runtime.house.legible.list_receipts(turn_id=result.turn_id, action="gramophone.note.add")
    assert len(receipts) == 1
    receipt = runtime.house.legible.inspect_receipt(receipts[0]["id"])
    assert receipt["status"] == "succeeded"
    assert action["content"] not in json.dumps(receipt)
    assert action["content"] in json.dumps(provider.requests[-1].messages)

    restarted = CoreRuntime(load_config(home), fake=True)
    private = {"interface": "web", "invocation": "conversation"}
    query = {"action": "gramophone.note.list", "track_id": track["track_id"],
             "source_sha256": track["sha256"]}
    notes = restarted.house.dispatch(query, turn_id="read", context=private)["notes"]
    assert len(notes) == 1 and notes[0]["content"] == action["content"]
    assert notes[0]["resident_id"] == restarted.resident_id
    retry = restarted.house.dispatch(action, turn_id="retry", context=private)
    assert retry["reused"] and retry["note"]["note_id"] == notes[0]["note_id"]
    with pytest.raises(ValueError, match="different"):
        restarted.house.dispatch({**action, "content": "Different"}, turn_id="conflict", context=private)
    with restarted.house.db.connect() as connection:
        events = connection.execute(
            "SELECT payload_json FROM house_events WHERE action LIKE 'gramophone.note.%'"
        ).fetchall()
    assert all(action["content"] not in event["payload_json"] for event in events)

    # Change and recatalog the same path. Historical notes remain attached to the prior hash.
    original = track_file.read_bytes()
    track_file.write_bytes(original[:-8])
    with pytest.raises(RuntimeError, match="changed"):
        restarted.house.dispatch({**action, "request_id": "changed-source"},
                                 turn_id="changed-source", context=private)
    changed = client.call_tool("gramophone.register", {
        "path": "Music/fixture.wav", "title": "Synthetic track",
    })["structured_content"]
    assert changed["track_id"] == track["track_id"] and changed["sha256"] != track["sha256"]
    assert restarted.house.dispatch(query, turn_id="history", context=private)["notes"] == notes
    assert restarted.house.dispatch({**query, "source_sha256": changed["sha256"]},
                                    turn_id="new-version", context=private)["notes"] == []
    assert hashlib.sha256(original).hexdigest() == track["sha256"]
    assert restarted.house.dispatch(action, turn_id="late-retry", context=private)["reused"]

    other_home = initialize_home(home.parent / "OtherHome", name="Other Resident")
    other = CoreRuntime(load_config(other_home), fake=True)
    assert other.house.dispatch(query, turn_id="other-resident", context=private)["notes"] == []


def test_policy_and_concurrent_retry(listening_house, monkeypatch):
    home, _, track, _ = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    note = {"action": "gramophone.note.add", "track_id": track["track_id"],
            "source_sha256": track["sha256"], "start_ms": 0, "end_ms": 500,
            "content": "same", "request_id": "same-request"}
    bell = {"interface": "bell", "invocation": "conversation"}
    with pytest.raises(PermissionError):
        runtime.house.dispatch({"action": "mcp.call", "tool": "gramophone.list"},
                               turn_id="bell", context=bell)
    private = {"interface": "web", "invocation": "conversation"}
    def save(_):
        return runtime.house.dispatch(note, turn_id="race", context=private)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert len({result["note"]["note_id"] for result in results}) == 1
    assert sorted(result["reused"] for result in results) == [False, True]
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "false")
    disabled = CoreRuntime(load_config(home), fake=True)
    with pytest.raises(PermissionError):
        disabled.house.dispatch({"action": "gramophone.list"}, turn_id="bell", context=bell)
    with pytest.raises(PermissionError):
        runtime.house.dispatch({"action": "gramophone.list"}, turn_id="guild", context={
            "interface": "discord", "invocation": "conversation",
            "delivery_target": {"kind": "discord_channel", "id": "1"},
        })


def test_long_passage_receipt_preserves_the_end(listening_house, monkeypatch):
    home, _, track, _ = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    samples = [{"peak": 0.1, "rms": 0.1}] * 120 + [{"peak": 0.9, "rms": 0.9}]
    def response(_self, tool, arguments):
        assert tool == "gramophone.inspect"
        return {"structured_content": {
            **arguments, "sha256": track["sha256"], "bin_ms": 100, "bins": samples,
        }}
    monkeypatch.setattr(McpStdioClient, "call_tool", response)
    result = runtime.house.dispatch({
        "action": "gramophone.inspect", "track_id": track["track_id"],
        "expected_sha256": track["sha256"], "start_ms": 0, "end_ms": 12050,
    }, turn_id="long", context={"interface": "web", "invocation": "conversation"})
    assert len(result["bins"]) == 61
    assert result["bin_ms"] == 200 and result["source_bin_ms"] == 100
    assert result["bins"][-1]["peak"] == 0.9
    receipt = runtime.house.legible.inspect_receipt(result["receipt_id"])
    assert receipt["result"]["bins"] == result["bins"]
