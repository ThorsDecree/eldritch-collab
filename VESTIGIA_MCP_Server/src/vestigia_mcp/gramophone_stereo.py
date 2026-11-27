"""Bounded per-channel energy and mid/side measurements for PCM passages."""
from __future__ import annotations

import math
import struct
from typing import Any


SAMPLE_RATE = 16_000


class _Accumulator:
    def __init__(self, channels: int):
        self.channels = channels
        self.count = 0
        self.left = self.right = self.cross = self.mid = self.side = 0.0

    def add(self, left: int, right: int | None) -> None:
        self.count += 1
        self.left += left * left
        if right is not None:
            self.right += right * right
            self.cross += left * right
            self.mid += ((left + right) / 2) ** 2
            self.side += ((left - right) / 2) ** 2

    def result(self) -> dict[str, Any]:
        def rms(energy: float) -> float:
            return round(math.sqrt(energy / self.count) / 32768, 6) if self.count else 0.0
        if self.channels == 1:
            return {"mono_rms": rms(self.left), "left_rms": None, "right_rms": None,
                    "balance": None, "correlation": None, "mid_rms": None, "side_rms": None,
                    "side_energy_fraction": None}
        left, right = math.sqrt(self.left), math.sqrt(self.right)
        total = self.mid + self.side
        return {
            "mono_rms": None, "left_rms": rms(self.left), "right_rms": rms(self.right),
            "balance": round((right - left) / (right + left), 6) if right + left else None,
            "correlation": round(max(-1.0, min(1.0, self.cross / (left * right))), 6) if left and right else None,
            "mid_rms": rms(self.mid), "side_rms": rms(self.side),
            "side_energy_fraction": round(self.side / total, 6) if total else None,
        }


def measure_stereo(raw: bytes, channels: int, start_ms: int, end_ms: int) -> dict[str, Any]:
    if channels not in (1, 2) or len(raw) % (2 * channels):
        raise ValueError("PCM must contain complete mono or stereo frames")
    frames = struct.iter_unpack("<h" if channels == 1 else "<hh", raw)
    overall = _Accumulator(channels)
    current = _Accumulator(channels)
    windows: list[dict[str, Any]] = []
    frame_start = 0
    for frame in frames:
        right = frame[1] if channels == 2 else None
        overall.add(frame[0], right)
        current.add(frame[0], right)
        if current.count == SAMPLE_RATE:
            windows.append({"start_ms": start_ms + round(frame_start * 1000 / SAMPLE_RATE),
                            "end_ms": min(end_ms, start_ms + round(overall.count * 1000 / SAMPLE_RATE)),
                            "samples": current.count, **current.result()})
            frame_start = overall.count
            current = _Accumulator(channels)
    if current.count:
        windows.append({"start_ms": start_ms + round(frame_start * 1000 / SAMPLE_RATE),
                        "end_ms": min(end_ms, start_ms + round(overall.count * 1000 / SAMPLE_RATE)),
                        "samples": current.count, **current.result()})
    return {
        "method": "ffmpeg 16000 Hz signed PCM; one-second RMS and uncentered left/right correlation",
        "source_channels": channels, "analysis_sample_rate_hz": SAMPLE_RATE,
        "stereo_available": channels == 2,
        "decoded_duration_ms": round(overall.count * 1000 / SAMPLE_RATE),
        "window_ms": 1000, "summary": overall.result(), "windows": windows,
        "limitations": "These are channel measurements, not spatial perception, instrument locations, "
                       "or a perceptual width score. Resampling omits content above 8 kHz. "
                       "Correlation is unavailable if either channel is silent; balance and side fraction "
                       "are unavailable if both channels are silent. Mono sources do not provide stereo measurements.",
    }
