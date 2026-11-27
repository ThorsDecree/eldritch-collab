import asyncio
import hashlib
import math
import shutil
import struct
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp import Client

from vestigia_mcp.adapters.archive import ArchiveError, ArchiveSource
from vestigia_mcp.config import Settings
from vestigia_mcp.gramophone import GramophoneService
from vestigia_mcp.server import create_server


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Gramophone audio tests require ffmpeg and ffprobe",
)


@pytest.fixture
def audio(tmp_path):
    root = tmp_path / "archive"
    music = root / "Music"
    music.mkdir(parents=True)
    path = music / "fixture.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        values = (int(12000 * math.sin(2 * math.pi * 440 * index / 8000))
                  for index in range(8000 * 3))
        output.writeframes(b"".join(struct.pack("<h", value) for value in values))
    return root, path, tmp_path / "state"


def test_catalog_and_passage_survive_restart_and_detect_changes(audio):
    root, path, state = audio
    service = GramophoneService(ArchiveSource(root), state, "Music")
    original = path.read_bytes()
    registered = service.register("Music/fixture.wav", "Synthetic test", "Fixture")
    assert registered["sha256"] == hashlib.sha256(original).hexdigest()
    assert registered["duration_ms"] == 3000
    reopened = GramophoneService(ArchiveSource(root), state, "Music")
    assert reopened.list_tracks()["tracks"] == [registered]
    result = reopened.inspect(registered["track_id"], registered["sha256"], 500, 1500)
    assert len(result["bins"]) == 10
    assert 0.25 < result["bins"][0]["rms"] < 0.27
    assert path.read_bytes() == original
    with pytest.raises(ArchiveError, match="range"):
        reopened.inspect(registered["track_id"], registered["sha256"], 0, 3001)
    path.write_bytes(original[:-8])
    with pytest.raises(ArchiveError, match="changed"):
        reopened.inspect(registered["track_id"], registered["sha256"], 500, 1500)
    with pytest.raises(ArchiveError, match="outside"):
        reopened.register("Other/sibling.wav", "No")
    with pytest.raises(ArchiveError):
        reopened.register("Music/../outside.wav", "No")


def test_concurrent_registration_is_idempotent(audio):
    root, _, state = audio
    def register():
        return GramophoneService(ArchiveSource(root), state, "Music").register(
            "Music/fixture.wav", "Fixture"
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        tracks = list(pool.map(lambda _: register(), range(4)))
    assert len({track["track_id"] for track in tracks}) == 1


def test_tools_are_discoverable_and_callable_over_mcp(audio):
    root, _, state = audio
    settings = Settings(live_archive_root=root, snapshot_archive_root=None, state_dir=state, deployment_id="synthetic")

    async def scenario():
        async with Client(create_server(settings)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {"gramophone.register", "gramophone.list", "gramophone.inspect"} <= names
            registered = await client.call_tool(
                "gramophone.register", {"path": "Music/fixture.wav", "title": "Synthetic"}
            )
            assert not registered.is_error
            track = registered.structured_content
            listed = await client.call_tool("gramophone.list", {})
            assert listed.structured_content["tracks"][0]["track_id"] == track["track_id"]
            result = await client.call_tool(
                "gramophone.inspect",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 0, "end_ms": 1000},
            )
            assert not result.is_error
            assert len(result.structured_content["bins"]) == 10
            rejected = await client.call_tool(
                "gramophone.inspect",
                {"track_id": track["track_id"], "expected_sha256": "0" * 64,
                 "start_ms": 0, "end_ms": 1000},
            )
            assert rejected.is_error
    asyncio.run(scenario())
