"""Render bounded periodicity candidates without inventing a continuous melody."""
from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1100, 560
PLOT_LEFT, PLOT_TOP, PLOT_RIGHT, PLOT_BOTTOM = 100, 110, 1030, 440


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_pitch_chart(result: dict[str, Any], start_ms: int, end_ms: int) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#120d19")
    draw = ImageDraw.Draw(image)
    title, body, small = _font(23), _font(15), _font(12)
    draw.text((PLOT_LEFT, 26), "GRAMOPHONE  /  PITCH CANDIDATES", font=title, fill="#f5dce9")
    draw.text((PLOT_LEFT, 60), "Dots are periodicity estimates, not identified vocals or verified melody.", font=body, fill="#ddb8ca")
    draw.text((PLOT_LEFT, 80), "Source: " + result["source_context"].replace("_", " ") + " (caller declaration)", font=small, fill="#bfa8bd")
    draw.text((PLOT_LEFT + 455, 80), "Track " + result["track_id"][-8:] + "  SHA-256 " + result["sha256"][:12] + "...", font=small, fill="#bfa8bd")
    draw.rectangle((PLOT_LEFT, PLOT_TOP, PLOT_RIGHT, PLOT_BOTTOM), fill="#1e1528", outline="#725b79")
    log_min, log_max = math.log2(55), math.log2(1000)
    def y_of(hz: float) -> float:
        return PLOT_BOTTOM - (math.log2(hz) - log_min) / (log_max - log_min) * (PLOT_BOTTOM - PLOT_TOP)
    for hz in (55, 110, 220, 440, 880, 1000):
        y = y_of(hz)
        draw.line((PLOT_LEFT, y, PLOT_RIGHT, y), fill="#49384f", width=1)
        draw.text((48, y - 6), str(hz), font=small, fill="#d8c4d5")
    draw.text((35, PLOT_TOP - 24), "Hz", font=small, fill="#d8c4d5")
    duration = end_ms - start_ms
    for part in range(5):
        x = PLOT_LEFT + (PLOT_RIGHT - PLOT_LEFT) * part / 4
        time_ms = start_ms + duration * part / 4
        draw.line((x, PLOT_TOP, x, PLOT_BOTTOM), fill="#49384f", width=1)
        draw.text((x - 17, PLOT_BOTTOM + 12), f"{time_ms / 1000:.2f}", font=small, fill="#d8c4d5")
    draw.text((PLOT_LEFT, PLOT_BOTTOM + 37), "Time in track (seconds), dots at frame midpoints", font=small, fill="#d8c4d5")
    plotted = 0
    for frame in result["frames"]:
        hz = frame.get("candidate_hz")
        if hz is None:
            continue
        center = (frame["start_ms"] + frame["end_ms"]) / 2
        x = PLOT_LEFT + (center - start_ms) / duration * (PLOT_RIGHT - PLOT_LEFT)
        y = y_of(hz)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#ffba75")
        plotted += 1
    if not plotted:
        draw.text((PLOT_LEFT + 250, PLOT_TOP + 145), "NO PERIODICITY CANDIDATES IN THIS PASSAGE", font=body, fill="#e1cad8")
    draw.text((PLOT_LEFT, 514), "Blank regions are unresolved. Mixed audio and octave errors can mislead.", font=small, fill="#d8c4d5")
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
