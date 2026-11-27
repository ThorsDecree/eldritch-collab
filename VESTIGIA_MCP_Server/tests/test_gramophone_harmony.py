import math
import random
import struct

import pytest

from vestigia_mcp.gramophone_harmony import measure_harmony


def tones(frequencies, duration=1.0):
    sample_count = int(8000 * duration)
    return b"".join(struct.pack("<h", int(sum(12000 * math.sin(2 * math.pi * hz * i / 8000)
                                                   for hz in frequencies) / max(1, len(frequencies))))
                    for i in range(sample_count))


def test_known_synthetic_major_and_minor_triads_are_tentative_candidates():
    raw = tones([261.626, 329.628, 391.995]) + tones([220, 261.626, 329.628])
    result = measure_harmony(raw, 10000, 12000)
    first, second = result["windows"]
    assert result["tuning_hz"] == 440 and result["triad_match_floor"] == 0.42
    assert (first["start_ms"], first["end_ms"]) == (10000, 11000)
    assert (second["start_ms"], second["end_ms"]) == (11000, 12000)
    assert any(x["root"] == "C" and x["quality"] == "major" for x in first["triad_candidates"])
    assert any(x["root"] == "A" and x["quality"] == "minor" for x in second["triad_candidates"])
    assert sum(first["pitch_class_energy"].values()) == pytest.approx(1, abs=0.002)
    assert 0 <= second["change_from_previous"] <= 1
    assert "not probabilities" in result["limitations"]
    assert "not musical tension" in result["limitations"]


def test_silence_and_single_sine_do_not_invent_triad():
    raw = bytes(16000) + tones([440]) + bytes(16000) + tones([440])
    windows = measure_harmony(raw, 0, 4000)["windows"]
    assert not windows[0]["active"] and windows[0]["pitch_class_energy"] is None
    assert windows[1]["active"] and windows[1]["triad_candidates"] == []
    assert not windows[2]["active"] and windows[3]["change_from_previous"] is None
    assert max(windows[1]["pitch_class_energy"], key=windows[1]["pitch_class_energy"].get) == "A"


def test_candidate_can_be_caused_by_overtones_of_one_fundamental():
    # The 1st, 5th and 6th harmonics of one C fundamental resemble C, E and G.
    result = measure_harmony(tones([261.626, 1308.13, 1569.756]), 0, 1000)
    assert any(x["root"] == "C" and x["quality"] == "major" for x in result["windows"][0]["triad_candidates"])
    assert "includes overtones" in result["limitations"]


def test_partial_window_and_quiet_input():
    result = measure_harmony(tones([440], duration=1.5), 2000, 3500)
    assert [(x["start_ms"], x["end_ms"]) for x in result["windows"]] == [(2000, 3000), (3000, 3500)]
    assert result["decoded_duration_ms"] == 1500
    assert not measure_harmony(tones([440], duration=0.25), 0, 250)["windows"][0]["triad_candidates"]


def test_deterministic_white_noise_has_no_triad_above_template_threshold():
    rng = random.Random(7)
    raw = b"".join(struct.pack("<h", rng.randint(-12000, 12000)) for _ in range(8000))
    window = measure_harmony(raw, 0, 1000)["windows"][0]
    assert window["active"] and window["triad_candidates"] == []


def test_out_of_band_tone_is_not_normalized_into_false_pitch_profile():
    result = measure_harmony(tones([3500]), 0, 1000)
    window = result["windows"][0]
    assert window["rms"] > 0.001
    assert not window["active"] and window["inactive_reason"] == "below_band_energy_floor"
    assert window["pitch_class_energy"] is None and window["triad_candidates"] == []
