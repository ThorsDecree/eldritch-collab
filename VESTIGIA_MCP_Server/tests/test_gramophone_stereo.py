import math
import struct

import pytest

from vestigia_mcp.gramophone_stereo import SAMPLE_RATE, measure_stereo


def stereo_pcm(*, right_sign=1, seconds=1):
    data = []
    for i in range(int(SAMPLE_RATE * seconds)):
        left = int(12000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE))
        data.append(struct.pack("<hh", left, right_sign * left))
    return b"".join(data)


def test_center_opposed_one_sided_and_silence():
    center = measure_stereo(stereo_pcm(), 2, 10000, 11000)
    assert center["stereo_available"]
    assert center["analysis_sample_rate_hz"] == 16000
    assert center["summary"]["correlation"] == 1
    assert center["summary"]["balance"] == 0
    assert center["summary"]["side_energy_fraction"] == 0
    assert center["windows"][0]["start_ms"] == 10000
    opposed = measure_stereo(stereo_pcm(right_sign=-1), 2, 0, 1000)
    assert opposed["summary"]["correlation"] == -1
    assert opposed["summary"]["side_energy_fraction"] == 1
    left_only = measure_stereo(stereo_pcm(right_sign=0), 2, 0, 1000)
    assert left_only["summary"]["correlation"] is None
    assert left_only["summary"]["balance"] == -1
    assert left_only["summary"]["side_energy_fraction"] == 0.5
    silence = measure_stereo(bytes(SAMPLE_RATE * 4), 2, 0, 1000)
    assert silence["summary"]["correlation"] is None
    assert silence["summary"]["balance"] is None
    assert silence["summary"]["side_energy_fraction"] is None


def test_mono_has_no_fabricated_stereo_and_partial_window_is_exact():
    stereo = stereo_pcm(seconds=1.5)
    mono = b"".join(struct.pack("<h", left) for left, _ in struct.iter_unpack("<hh", stereo))
    result = measure_stereo(mono, 1, 2000, 3500)
    assert result["stereo_available"] is False
    assert result["summary"]["mono_rms"] > 0
    assert result["summary"]["left_rms"] is None
    assert result["summary"]["correlation"] is None
    assert [(w["start_ms"], w["end_ms"], w["samples"]) for w in result["windows"]] == [
        (2000, 3000, 16000), (3000, 3500, 8000),
    ]
    with pytest.raises(ValueError):
        measure_stereo(b"\x00", 2, 0, 1000)


def test_channel_relationship_changes_with_time():
    data = stereo_pcm() + stereo_pcm(right_sign=-1)
    result = measure_stereo(data, 2, 0, 2000)
    assert [w["correlation"] for w in result["windows"]] == [1, -1]
    assert [w["side_energy_fraction"] for w in result["windows"]] == [0, 1]
    assert result["summary"]["correlation"] == 0
    assert result["summary"]["side_energy_fraction"] == 0.5
