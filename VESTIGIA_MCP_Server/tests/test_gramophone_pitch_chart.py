import io
import math

from PIL import Image

from vestigia_mcp.gramophone_pitch_chart import (
    PLOT_BOTTOM, PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, render_pitch_chart,
)


def point_x(ms):
    return round(PLOT_LEFT + (PLOT_RIGHT - PLOT_LEFT) * ms / 1000)


def point_y(hz):
    return round(PLOT_BOTTOM - (math.log2(hz) - math.log2(55)) /
                 (math.log2(1000) - math.log2(55)) * (PLOT_BOTTOM - PLOT_TOP))


def test_chart_places_estimates_and_does_not_connect_unresolved_gap():
    result = {"track_id": "track_" + "1" * 32, "sha256": "a" * 64, "source_context": "unverified_mix",
              "frames": [
                  {"start_ms": 0, "end_ms": 80, "candidate_hz": 220},
                  {"start_ms": 400, "end_ms": 480, "candidate_hz": None},
                  {"start_ms": 800, "end_ms": 880, "candidate_hz": 440},
              ]}
    png = render_pitch_chart(result, 0, 1000)
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG" and image.size == (1100, 560)
        pixels = image.convert("RGB")
        assert pixels.getpixel((point_x(40), point_y(220))) == (255, 186, 117)
        assert pixels.getpixel((point_x(840), point_y(440))) == (255, 186, 117)
        gap = pixels.crop((point_x(440) - 5, PLOT_TOP + 1, point_x(440) + 5, PLOT_BOTTOM - 1))
        data = gap.tobytes()
        assert not any(data[i:i + 3] == b"\xff\xba\x75" for i in range(0, len(data), 3))


def test_silence_chart_contains_no_pitch_dots():
    result = {"track_id": "track_" + "1" * 32, "sha256": "a" * 64, "source_context": "unverified_mix",
              "frames": [{"start_ms": 0, "end_ms": 80, "candidate_hz": None}]}
    with Image.open(io.BytesIO(render_pitch_chart(result, 0, 1000))) as image:
        plot = image.convert("RGB").crop((PLOT_LEFT, PLOT_TOP, PLOT_RIGHT, PLOT_BOTTOM))
        data = plot.tobytes()
        assert not any(data[i:i + 3] == b"\xff\xba\x75" for i in range(0, len(data), 3))
