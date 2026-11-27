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


def test_spectrogram_reaches_private_shelf_from_bell_and_survives_restart(listening_house, monkeypatch):
    home, track_file, track, _ = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    action = {"action": "gramophone.spectrogram", "track_id": track["track_id"],
              "expected_sha256": track["sha256"], "start_ms": 500, "end_ms": 1500}
    result = runtime.house.dispatch(action, turn_id="bell-picture", context={
        "interface": "bell", "invocation": "conversation",
    })
    [picture] = result["images"]
    assert result["retention"] == "runtime_private_image_shelf"
    asset = runtime.house.images.get_asset(picture["image_id"])
    assert asset["privacy"] == "private"
    assert asset["content_hash"] == result["image_sha256"] == picture["sha256"]
    assert asset["media_type"] == "image/png"
    inspected = runtime.house.dispatch({
        "action": "image.inspect", "image_id": picture["image_id"],
        "routes": ["vision_low"], "question": "Examine the plotted frequencies.",
    }, turn_id="bell-vision", context={"interface": "bell", "invocation": "conversation"})
    assert inspected["results"][0]["status"] == "ok"
    assert inspected["results"][0]["model"] == "fake-vision"
    assert hashlib.sha256(track_file.read_bytes()).hexdigest() == track["sha256"]
    receipt = runtime.house.legible.inspect_receipt(result["receipt_id"])
    assert receipt["status"] == "succeeded"
    assert "data:image" not in json.dumps(receipt)
    restarted = CoreRuntime(load_config(home), fake=True)
    assert restarted.house.images.get_asset(picture["image_id"])["content_hash"] == picture["sha256"]
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "false")
    disabled = CoreRuntime(load_config(home), fake=True)
    with pytest.raises(PermissionError):
        disabled.house.dispatch(action, turn_id="disabled", context={
            "interface": "bell", "invocation": "conversation",
        })


def test_spectrogram_rejects_mismatched_provenance(listening_house, monkeypatch):
    home, _, track, _ = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    def response(_self, tool, arguments):
        return {"text_blocks": [json.dumps({**arguments, "sha256": "0" * 64})],
                "image_blocks": [{"mime_type": "image/png", "data": ""}]}
    monkeypatch.setattr(McpStdioClient, "call_tool", response)
    with pytest.raises(RuntimeError, match="provenance"):
        runtime.house.dispatch({"action": "gramophone.spectrogram", "track_id": track["track_id"],
                                "expected_sha256": track["sha256"], "start_ms": 0, "end_ms": 1000},
                               turn_id="forged", context={"interface": "web", "invocation": "conversation"})


def test_listening_session_navigation_notes_and_restart(listening_house):
    home, track_file, track, client = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    context = {"interface": "web", "invocation": "conversation"}
    base = {"track_id": track["track_id"], "source_sha256": track["sha256"]}
    def dispatch(action, **fields):
        return runtime.house.dispatch({"action": action, **base, **fields}, turn_id="session", context=context)

    opened = dispatch("gramophone.session.open", window_ms=1000)
    assert not opened["resumed"]
    assert (opened["session"]["start_ms"], opened["session"]["end_ms"], opened["session"]["revision"]) == (0, 1000, 1)
    for start, end, text, key in [(500, 1500, "overlap secret", "n1"),
                                   (1500, 2000, "middle secret", "n2"),
                                   (2000, 2500, "later secret", "n3")]:
        dispatch("gramophone.note.add", start_ms=start, end_ms=end, content=text, request_id=key)
    view = dispatch("gramophone.session.view", limit=1)
    assert [x["content"] for x in view["notes"]] == ["overlap secret"]
    assert not view["source_verified"]
    receipt = runtime.house.legible.inspect_receipt(view["receipt_id"])
    assert "overlap secret" not in json.dumps(receipt)
    with runtime.house.db.connect() as connection:
        events = connection.execute("SELECT payload_json FROM house_events WHERE action='gramophone.session.view'").fetchall()
    assert all("overlap secret" not in row["payload_json"] for row in events)
    args = {"expected_revision": 1, "mode": "next", "request_id": "move-1"}
    moved = dispatch("gramophone.session.move", **args)
    assert (moved["session"]["start_ms"], moved["session"]["end_ms"], moved["session"]["revision"]) == (1000, 2000, 2)
    assert dispatch("gramophone.session.move", **args)["reused"]
    with pytest.raises(ValueError, match="different"):
        dispatch("gramophone.session.move", **{**args, "mode": "previous"})
    with pytest.raises(ValueError, match="revision"):
        dispatch("gramophone.session.move", **{**args, "request_id": "stale"})
    view = dispatch("gramophone.session.view", limit=1)
    assert [x["content"] for x in view["notes"]] == ["overlap secret"] and view["next_offset"] == 1
    assert [x["content"] for x in dispatch("gramophone.session.view", offset=1)["notes"]] == ["middle secret"]
    end = dispatch("gramophone.session.move", expected_revision=2, mode="next", request_id="move-2")
    assert end["session"]["next_start_ms"] is None
    boundary = dispatch("gramophone.session.move", expected_revision=3, mode="next", request_id="end")
    assert boundary["boundary"] and boundary["session"]["revision"] == 3
    assert dispatch("gramophone.session.move", expected_revision=3, mode="next", request_id="end")["reused"]
    seek = dispatch("gramophone.session.move", expected_revision=3, mode="seek", start_ms=500, request_id="seek")
    assert seek["session"]["start_ms"] == 500
    resized = dispatch("gramophone.session.move", expected_revision=4, mode="resize", window_ms=500, request_id="resize")
    assert resized["session"]["start_ms"] == 500 and resized["session"]["end_ms"] == 1000
    assert resized["session"]["revision"] == 5
    restarted = CoreRuntime(load_config(home), fake=True)
    resumed = restarted.house.dispatch({"action": "gramophone.session.open", **base, "window_ms": 500},
                                       turn_id="restart", context=context)
    assert resumed["resumed"] and resumed["session"]["start_ms"] == 500 and resumed["session"]["window_ms"] == 500
    original = track_file.read_bytes()
    track_file.write_bytes(original[:-8])
    with pytest.raises(RuntimeError, match="changed"):
        dispatch("gramophone.session.move", expected_revision=5, mode="next", request_id="changed")
    assert dispatch("gramophone.session.view")["session"]["start_ms"] == 500
    changed = client.call_tool("gramophone.register", {"path": "Music/fixture.wav", "title": "Changed"})["structured_content"]
    assert changed["sha256"] != track["sha256"]
    new = runtime.house.dispatch({"action": "gramophone.session.open", "track_id": changed["track_id"],
                                  "source_sha256": changed["sha256"], "window_ms": 500}, turn_id="new", context=context)
    assert not new["resumed"] and new["session"]["start_ms"] == 0
    assert dispatch("gramophone.session.view")["notes"][0]["content"] == "overlap secret"


def test_listening_session_concurrent_moves_and_bell_policy(listening_house, monkeypatch):
    home, _, track, _ = listening_house
    runtime = CoreRuntime(load_config(home), fake=True)
    base = {"track_id": track["track_id"], "source_sha256": track["sha256"]}
    bell = {"interface": "bell", "invocation": "conversation"}
    def dispatch(action, **fields):
        return runtime.house.dispatch({"action": action, **base, **fields}, turn_id="bell-session", context=bell)
    dispatch("gramophone.session.open", window_ms=1000)
    def move(_):
        return dispatch("gramophone.session.move", expected_revision=1, mode="next", request_id="same-move")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(move, range(2)))
    assert sorted(item["reused"] for item in results) == [False, True]
    assert all(item["session"]["start_ms"] == 1000 for item in results)
    def compete(mode):
        try:
            return dispatch("gramophone.session.move", expected_revision=2, mode=mode, request_id="race-" + mode)
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        competing = list(pool.map(compete, ("next", "previous")))
    assert sum(isinstance(item, dict) for item in competing) == 1
    assert sum(isinstance(item, str) and "revision" in item for item in competing) == 1
    with pytest.raises(ValueError, match="revision"):
        dispatch("gramophone.session.move", expected_revision=1, mode="next", request_id="another")
    with pytest.raises(ValueError, match="only when seeking"):
        dispatch("gramophone.session.move", expected_revision=3, mode="next", start_ms=50, request_id="bad")
    other_home = initialize_home(home.parent / "OtherSessionHome", name="Other")
    other = CoreRuntime(load_config(other_home), fake=True)
    with pytest.raises(ValueError, match="No listening session"):
        other.house.dispatch({"action": "gramophone.session.view", **base}, turn_id="other", context=bell)
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "false")
    disabled = CoreRuntime(load_config(home), fake=True)
    with pytest.raises(PermissionError):
        disabled.house.dispatch({"action": "gramophone.session.view", **base}, turn_id="disabled", context=bell)


def test_simulated_bell_uses_cursor_to_measure_and_note(listening_house):
    home, _, track, _ = listening_house
    base = {"track_id": track["track_id"], "source_sha256": track["sha256"]}
    actions = [
        {"action": "gramophone.session.open", **base, "window_ms": 1000},
        {"action": "gramophone.inspect", "track_id": track["track_id"],
         "expected_sha256": track["sha256"], "start_ms": 0, "end_ms": 1000},
        {"action": "gramophone.note.add", **base, "start_ms": 0, "end_ms": 1000,
         "content": "The test tone is present.", "request_id": "bell-note"},
        {"action": "gramophone.session.view", **base},
        {"action": "gramophone.session.move", **base, "expected_revision": 1,
         "mode": "next", "request_id": "bell-next"},
    ]
    provider = FakeProvider(['[[TOOL_ACTION ' + json.dumps({**action, "after": "continue"}) + ']]'
                             for action in actions] + ["Saved and moved on."])
    runtime = CoreRuntime(load_config(home), provider=provider, fake=True)
    result = runtime.chat(NormalizedMessage(content="Explore the track", interface="bell", speaker_id="bell:test",
                                            room_id=runtime.room_id, external_id="bell-session"))
    for action in actions:
        receipts = runtime.house.legible.list_receipts(turn_id=result.turn_id, action=action["action"])
        assert len(receipts) == 1
        assert runtime.house.legible.inspect_receipt(receipts[0]["id"])["status"] == "succeeded"
    assert "The test tone is present." in json.dumps(provider.requests[-2].messages)
    view = runtime.house.dispatch({"action": "gramophone.session.view", **base}, turn_id="later",
                                  context={"interface": "web", "invocation": "conversation"})
    assert view["session"]["start_ms"] == 1000


def test_opted_in_bell_measures_rhythm_with_private_tool(listening_house, monkeypatch):
    home, track_file, _, client = listening_house
    rate = 8000
    with wave.open(str(track_file), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        samples = []
        for i in range(rate * 12):
            since = (i / rate) % (60 / 140)
            samples.append(struct.pack("<h", int(18000 * math.sin(2 * math.pi * 800 * since))
                                       if since < 0.025 else 0))
        output.writeframes(b"".join(samples))
    track = client.call_tool("gramophone.register", {
        "path": "Music/fixture.wav", "title": "Synthetic click",
    })["structured_content"]
    action = {"action": "gramophone.rhythm", "track_id": track["track_id"],
              "expected_sha256": track["sha256"], "start_ms": 0, "end_ms": 12000}
    provider = FakeProvider(['[[TOOL_ACTION ' + json.dumps({**action, "after": "continue"}) + ']]',
                             "Measured a pulse."])
    runtime = CoreRuntime(load_config(home), provider=provider, fake=True)
    result = runtime.chat(NormalizedMessage(content="Explore the pulse", interface="bell", speaker_id="bell:test",
                                            room_id=runtime.room_id, external_id="bell-rhythm"))
    receipts = runtime.house.legible.list_receipts(turn_id=result.turn_id, action="gramophone.rhythm")
    assert len(receipts) == 1
    receipt = runtime.house.legible.inspect_receipt(receipts[0]["id"])
    assert receipt["status"] == "succeeded"
    assert any(abs(x["bpm"] - 140) < 4 for x in receipt["result"]["tempo_candidates"])
    assert "tempo_candidates" in json.dumps(provider.requests[-1].messages)
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "false")
    disabled = CoreRuntime(load_config(home), fake=True)
    with pytest.raises(PermissionError):
        disabled.house.dispatch(action, turn_id="disabled", context={"interface": "bell", "invocation": "conversation"})


def test_private_bell_compares_pocket_against_explicit_grid(listening_house, monkeypatch):
    home, track_file, _, client = listening_house
    samples = [0] * (8000 * 3)
    for onset in (450, 1040, 1460, 2010):
        for i in range(160):
            samples[onset * 8 + i] = int(18000 * math.sin(2 * math.pi * 800 * i / 8000))
    with wave.open(str(track_file), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    track = client.call_tool("gramophone.register", {"path": "Music/fixture.wav", "title": "Synthetic pocket"})["structured_content"]
    action = {"action": "gramophone.pocket", "track_id": track["track_id"],
              "expected_sha256": track["sha256"], "start_ms": 0, "end_ms": 3000,
              "bpm": 120, "anchor_ms": 500, "limit": 2}
    provider = FakeProvider(['[[TOOL_ACTION ' + json.dumps({**action, "after": "continue"}) + ']]',
                             "Compared the timing."])
    runtime = CoreRuntime(load_config(home), provider=provider, fake=True)
    result = runtime.chat(NormalizedMessage(content="Explore timing", interface="bell", speaker_id="bell:test",
                                            room_id=runtime.room_id, external_id="bell-pocket"))
    receipts = runtime.house.legible.list_receipts(turn_id=result.turn_id, action="gramophone.pocket")
    assert len(receipts) == 1
    receipt = runtime.house.legible.inspect_receipt(receipts[0]["id"])
    assert receipt["status"] == "succeeded"
    assert [x["offset_ms"] for x in receipt["result"]["attacks"]] == pytest.approx([-50, 40], abs=10)
    assert receipt["result"]["next_offset"] == 2
    assert "caller_supplied_unverified" in json.dumps(provider.requests[-1].messages)
    monkeypatch.setenv("VESTIGIA_GRAMOPHONE_BELLS_ENABLED", "false")
    disabled = CoreRuntime(load_config(home), fake=True)
    with pytest.raises(PermissionError):
        disabled.house.dispatch(action, turn_id="disabled", context={"interface": "bell", "invocation": "conversation"})
