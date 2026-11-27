import asyncio
import base64
import json
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
            assert {"gramophone.register", "gramophone.list", "gramophone.inspect", "gramophone.spectrogram", "gramophone.rhythm", "gramophone.pocket"} <= names
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
            picture = await client.call_tool(
                "gramophone.spectrogram",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 500, "end_ms": 1500},
            )
            assert not picture.is_error
            text, image = picture.content
            metadata = json.loads(text.text)
            assert hashlib.sha256(base64.b64decode(image.data)).hexdigest() == metadata["image_sha256"]
            assert image.mime_type == "image/png"
            rhythmic = await client.call_tool(
                "gramophone.rhythm",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 500, "end_ms": 1500},
            )
            assert not rhythmic.is_error
            assert rhythmic.structured_content["sha256"] == track["sha256"]
            assert rhythmic.structured_content["tempo_candidates"] == []
            pocket = await client.call_tool(
                "gramophone.pocket",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 500, "end_ms": 1500, "bpm": 120, "anchor_ms": 500},
            )
            assert not pocket.is_error
            assert pocket.structured_content["grid"]["source"] == "caller_supplied_unverified"
            assert pocket.structured_content["sha256"] == track["sha256"]
            invalid_grid = await client.call_tool(
                "gramophone.pocket",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 500, "end_ms": 1500, "bpm": 0, "anchor_ms": 500},
            )
            assert invalid_grid.is_error
            refused = await client.call_tool(
                "gramophone.rhythm",
                {"track_id": track["track_id"], "expected_sha256": "0" * 64,
                 "start_ms": 500, "end_ms": 1500},
            )
            assert refused.is_error
            too_short = await client.call_tool(
                "gramophone.rhythm",
                {"track_id": track["track_id"], "expected_sha256": track["sha256"],
                 "start_ms": 500, "end_ms": 600},
            )
            assert too_short.is_error
            rejected = await client.call_tool(
                "gramophone.inspect",
                {"track_id": track["track_id"], "expected_sha256": "0" * 64,
                 "start_ms": 0, "end_ms": 1000},
            )
            assert rejected.is_error
    asyncio.run(scenario())


def test_spectrogram_is_versioned_and_validates_passage(audio):
    import io
    from PIL import Image

    root, path, state = audio
    service = GramophoneService(ArchiveSource(root), state, "Music")
    track = service.register("Music/fixture.wav", "Synthetic")
    metadata, data = service.spectrogram(track["track_id"], track["sha256"], 500, 1500)
    assert metadata["image_sha256"] == hashlib.sha256(data).hexdigest()
    assert (metadata["start_ms"], metadata["end_ms"]) == (500, 1500)
    assert metadata["sha256"] == track["sha256"]
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.width >= 900 and image.height >= 420
        assert image.getbbox() is not None
    with pytest.raises(ArchiveError, match="250 ms"):
        service.spectrogram(track["track_id"], track["sha256"], 0, 100)
    with pytest.raises(ArchiveError, match="digest"):
        service.spectrogram(track["track_id"], "0" * 64, 0, 1000)
    original = path.read_bytes()
    path.write_bytes(original[:-8])
    with pytest.raises(ArchiveError, match="changed"):
        service.spectrogram(track["track_id"], track["sha256"], 0, 1000)
    with pytest.raises(ArchiveError, match="changed"):
        service.rhythm(track["track_id"], track["sha256"], 0, 1000)
    with pytest.raises(ArchiveError, match="changed"):
        service.pocket(track["track_id"], track["sha256"], 0, 1000, 120, 0)


def test_spectrogram_reflects_requested_audio_passage(audio):
    import io
    from PIL import Image, ImageStat

    root, path, state = audio
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        tone = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 8000)))
                        for i in range(8000))
        output.writeframes(tone + bytes(16000))
    service = GramophoneService(ArchiveSource(root), state, "Music")
    track = service.register("Music/fixture.wav", "Tone and silence")
    _, tone_image = service.spectrogram(track["track_id"], track["sha256"], 0, 1000)
    _, silence_image = service.spectrogram(track["track_id"], track["sha256"], 1000, 2000)
    # Sample only the plot area, excluding the axes and legend.
    def brightness(data):
        with Image.open(io.BytesIO(data)) as picture:
            return sum(ImageStat.Stat(picture.convert("RGB").crop((200, 100, 800, 400))).mean)
    assert brightness(tone_image) > brightness(silence_image) + 10


def test_pocket_rejects_invalid_grid_and_passage(audio):
    root, _, state = audio
    service = GramophoneService(ArchiveSource(root), state, "Music")
    track = service.register("Music/fixture.wav", "Synthetic")
    args = (track["track_id"], track["sha256"], 0, 1000)
    for bpm in (float("nan"), float("inf"), 0, 241, True):
        with pytest.raises(ArchiveError):
            service.pocket(*args, bpm, 0)
    for kwargs in ({"subdivisions": 3}, {"anchor_ms": -1}, {"anchor_ms": 3001},
                   {"offset": -1}, {"limit": 0}, {"limit": 101}):
        options = {"bpm": 120, "anchor_ms": 0, **kwargs}
        with pytest.raises(ArchiveError):
            service.pocket(*args, **options)
    with pytest.raises(ArchiveError, match="250 ms"):
        service.pocket(track["track_id"], track["sha256"], 0, 100, 120, 0)
