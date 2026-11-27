import math
import struct

import pytest

from vestigia_mcp.gramophone_rhythm import measure_pocket


def pulses(milliseconds, duration_ms):
    samples = [0] * (duration_ms * 8)
    for onset in milliseconds:
        for i in range(160):
            samples[onset * 8 + i] = int(18000 * math.sin(2 * math.pi * 800 * i / 8000))
    return b"".join(struct.pack("<h", sample) for sample in samples)


def test_user_grid_shows_early_late_and_supports_pagination():
    data = pulses([450, 1040, 1460, 2010], 3000)
    first = measure_pocket(data, 0, 120, 500, 1, offset=0, limit=2)
    second = measure_pocket(data, 0, 120, 500, 1, offset=2, limit=2)
    assert first["attack_count"] == 4 and first["next_offset"] == 2
    assert second["next_offset"] is None
    attacks = first["attacks"] + second["attacks"]
    assert [attack["grid_time_ms"] for attack in attacks] == [500, 1000, 1500, 2000]
    assert [attack["offset_ms"] for attack in attacks] == pytest.approx([-50, 40, -40, 10], abs=10)
    assert first["grid"]["source"] == "caller_supplied_unverified"
    assert "not instrument labels" in first["limitations"]


def test_offsets_are_relative_to_explicit_grid_and_track_time():
    data = pulses([450, 1040], 2000)
    one = measure_pocket(data, 10000, 120, 10500, 1)
    shifted = measure_pocket(data, 10000, 120, 10530, 1)
    assert one["attacks"][0]["time_ms"] == 10450
    assert one["attacks"][0]["offset_ms"] == pytest.approx(-50, abs=10)
    assert shifted["attacks"][0]["offset_ms"] == pytest.approx(-80, abs=10)
    assert measure_pocket(bytes(8000 * 2 * 2), 0, 120, 0, 1)["attacks"] == []


def test_dense_passage_can_page_beyond_100_attacks():
    data = pulses(list(range(200, 40200, 200)), 41000)
    first = measure_pocket(data, 0, 150, 0, 2, offset=0, limit=100)
    second = measure_pocket(data, 0, 150, 0, 2, offset=100, limit=100)
    assert first["attack_count"] == 200 and first["next_offset"] == 100
    assert len(first["attacks"]) == len(second["attacks"]) == 100
    assert second["next_offset"] is None
    assert second["attacks"][0]["time_ms"] > first["attacks"][-1]["time_ms"]


def test_grid_midpoint_ties_and_subdivisions():
    data = pulses([250, 750], 1200)
    beats = measure_pocket(data, 0, 120, 0, 1)
    assert [item["grid_time_ms"] for item in beats["attacks"]] == [500, 1000]
    eighths = measure_pocket(data, 0, 120, 0, 2)
    assert [item["grid_time_ms"] for item in eighths["attacks"]] == [250, 750]
    assert [item["offset_ms"] for item in eighths["attacks"]] == [0, 0]
