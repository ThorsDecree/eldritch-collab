import math
import struct

from vestigia_mcp.gramophone_rhythm import measure_rhythm


def click_track(bpm=140, seconds=12):
    rate = 8000
    period = 60 / bpm
    data = []
    for i in range(rate * seconds):
        time = i / rate
        since = time % period
        sample = int(18000 * math.sin(2 * math.pi * 800 * since)) if since < 0.025 else 0
        data.append(struct.pack("<h", sample))
    return b"".join(data)


def test_click_track_reports_nearby_attacks_and_possible_tempo():
    measured = measure_rhythm(click_track(), 10000, 22000)
    assert 25 <= measured["attack_count"] <= 30
    # The clip starts in a sound; without preceding context it is not labeled an attack.
    assert abs(measured["attacks"][0]["time_ms"] - 10430) <= 10
    assert any(abs(item["bpm"] - 140) < 4 for item in measured["tempo_candidates"])
    assert "not probabilities" in measured["limitations"]


def test_silence_and_steady_tone_do_not_invent_periodicity():
    silence = measure_rhythm(bytes(8000 * 8 * 2), 0, 8000)
    assert silence["attack_count"] == 0 and silence["tempo_candidates"] == []
    tone = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 8000)))
                    for i in range(8000 * 8))
    steady = measure_rhythm(tone, 0, 8000)
    assert steady["attack_count"] <= 1 and steady["tempo_candidates"] == []


def test_short_window_avoids_tempo_claim_and_long_output_is_bounded():
    short = measure_rhythm(click_track(seconds=4), 0, 4000)
    assert short["attack_count"] > 4 and short["tempo_candidates"] == []
    long = measure_rhythm(click_track(bpm=200, seconds=40), 0, 40000)
    assert long["attack_count"] > 100
    assert len(long["attacks"]) == 100 and long["attacks_truncated"]


def test_layered_kick_and_hi_hat_can_have_multiple_tempo_candidates():
    rate = 8000
    values = []
    for i in range(rate * 12):
        time = i / rate
        beat = time % (60 / 140)
        hat = time % (30 / 140)
        value = (11000 * math.sin(2 * math.pi * 100 * beat) if beat < 0.04 else 0)
        value += (4000 * math.sin(2 * math.pi * 2400 * hat) if hat < 0.025 else 0)
        values.append(struct.pack("<h", int(value)))
    measured = measure_rhythm(b"".join(values), 0, 12000)
    assert any(abs(item["bpm"] - 140) < 4 for item in measured["tempo_candidates"])
    assert len(measured["tempo_candidates"]) > 1
