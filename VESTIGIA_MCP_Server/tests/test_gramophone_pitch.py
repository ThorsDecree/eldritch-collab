import math
import random
import struct

import pytest

from vestigia_mcp.gramophone_pitch import measure_pitch


def tone(hz, duration=1, amplitude=12000):
    return b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * hz * i / 8000)))
                    for i in range(int(8000 * duration)))


@pytest.mark.parametrize("frequency", [80, 110, 220, 440, 880])
def test_monophonic_sines_return_nearby_period_candidates(frequency):
    result = measure_pitch(tone(frequency), 10000, "caller_asserted_monophonic", 0, 5)
    assert result["total_frames"] == 19 and result["next_offset"] == 5
    assert result["frames"][0]["start_ms"] == 10000
    assert result["frames"][0]["end_ms"] == 10080
    assert all(f["status"] == "periodic_candidate" for f in result["frames"])
    assert all(f["candidate_hz"] == pytest.approx(frequency, rel=0.015) for f in result["frames"])
    assert "not evidence of a human voice" in result["limitations"]
    assert result["source_context_provenance"] == "caller_declared_unverified"


def test_vowel_like_harmonics_and_pitch_changes_without_claiming_voice():
    def complex_tone(frequency):
        return b"".join(struct.pack("<h", int(sum(3000 * math.sin(2 * math.pi * frequency * k * i / 8000)
                                                     for k in range(1, 5)))) for i in range(8000))
    raw = complex_tone(200) + complex_tone(300)
    first = measure_pitch(raw, 0, "caller_asserted_monophonic", 0, 5)
    second = measure_pitch(raw, 0, "caller_asserted_monophonic", 20, 5)
    assert all(f["candidate_hz"] == pytest.approx(200, rel=0.02) for f in first["frames"])
    assert all(f["candidate_hz"] == pytest.approx(300, rel=0.02) for f in second["frames"])


def test_silence_and_noise_are_not_voice_labels():
    rng = random.Random(13)
    silence = measure_pitch(bytes(16000), 0, "unverified_mix", 0, 10)
    assert all(x["status"] == "below_rms_floor" and x["candidate_hz"] is None for x in silence["frames"])
    noise = b"".join(struct.pack("<h", rng.randint(-12000, 12000)) for _ in range(8000))
    result = measure_pitch(noise, 0, "unverified_mix", 0, 10)
    assert all(x["candidate_hz"] is None for x in result["frames"])
    assert result["source_context"] == "unverified_mix"
    assert "No candidate does not mean no voice" in result["limitations"]


def test_pages_are_stable_and_partial_tail_is_not_fabricated():
    raw = tone(220, duration=3)
    first = measure_pitch(raw, 5000, "unverified_mix", 0, 30)
    second = measure_pitch(raw, 5000, "unverified_mix", first["next_offset"], 30)
    assert first["total_frames"] == 59
    assert first["next_offset"] == 30 and second["next_offset"] is None
    assert len(first["frames"]) == 30 and len(second["frames"]) == 29
    assert second["frames"][-1]["end_ms"] == 7980
    assert first["frames"][-1]["start_ms"] < second["frames"][0]["start_ms"]


def test_a_mixture_can_create_a_strong_candidate_at_neither_component_pitch():
    data = b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 220 * i / 8000) +
                                           5000 * math.sin(2 * math.pi * 330 * i / 8000)))
                    for i in range(8000))
    result = measure_pitch(data, 0, "unverified_mix", 0, 1)
    frame = result["frames"][0]
    assert frame["candidate_hz"] == pytest.approx(110, rel=0.02)
    assert frame["periodicity"] > 0.9
    assert "not a probability" in result["limitations"]


def test_synthetic_pitch_sweep_tracks_a_clear_change_over_time():
    raw = b"".join(struct.pack("<h", int(12000 * math.sin(
        2 * math.pi * (180 * (i / 8000) + 45 * (i / 8000) ** 2)
    ))) for i in range(16000))
    early = measure_pitch(raw, 0, "caller_asserted_monophonic", 0, 3)["frames"]
    late = measure_pitch(raw, 0, "caller_asserted_monophonic", 30, 3)["frames"]
    assert all(x["candidate_hz"] is not None for x in early + late)
    assert early[0]["candidate_hz"] == pytest.approx(183.6, rel=0.04)
    assert late[0]["candidate_hz"] == pytest.approx(318.6, rel=0.04)
    assert late[0]["candidate_hz"] > early[-1]["candidate_hz"] + 100
