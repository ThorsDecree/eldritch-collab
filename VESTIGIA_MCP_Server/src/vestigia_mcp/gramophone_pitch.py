"""Bounded single-period candidates, not voice detection or source separation."""
from __future__ import annotations

import math
import struct
from typing import Any


SAMPLE_RATE = 8000
FRAME_SAMPLES = 640  # 80 ms
HOP_SAMPLES = 400  # 50 ms
MIN_LAG = 8  # 1000 Hz
MAX_LAG = 145  # approximately 55 Hz
RMS_FLOOR = 0.001
PERIODICITY_FLOOR = 0.75


def _estimate(frame: tuple[int, ...]) -> dict[str, Any]:
    rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame)) / 32768
    if rms < RMS_FLOOR:
        return {"status": "below_rms_floor", "rms": round(rms, 6),
                "candidate_hz": None, "periodicity": None}
    mean = sum(frame) / len(frame)
    samples = [sample - mean for sample in frame]
    width = len(samples) - MAX_LAG - 1
    reference = samples[:width]
    powers = [0.0]
    for sample in samples:
        powers.append(powers[-1] + sample * sample)
    reference_power = powers[width]
    scores = [0.0] * (MAX_LAG + 2)
    for lag in range(MIN_LAG - 1, MAX_LAG + 2):
        other_power = powers[lag + width] - powers[lag]
        denominator = math.sqrt(reference_power * other_power) if reference_power > 0 and other_power > 0 else 0.0
        if denominator:
            scores[lag] = max(-1.0, min(1.0, sum(a * b for a, b in zip(reference, samples[lag:lag + width])) / denominator))
    peaks = [lag for lag in range(MIN_LAG, MAX_LAG + 1)
             if scores[lag] >= scores[lag - 1] and scores[lag] > scores[lag + 1]]
    if not peaks:
        return {"status": "no_candidate", "rms": round(rms, 6), "candidate_hz": None, "periodicity": None}
    best = max(scores[lag] for lag in peaks)
    if best < PERIODICITY_FLOOR:
        return {"status": "below_periodicity_floor", "rms": round(rms, 6),
                "candidate_hz": None, "periodicity": round(best, 4)}
    # Prefer the shortest well-supported period, but preserve octave ambiguity.
    lag = next(lag for lag in peaks if scores[lag] >= max(PERIODICITY_FLOOR, best * 0.95))
    left, center, right = scores[lag - 1], scores[lag], scores[lag + 1]
    curvature = left - 2 * center + right
    adjustment = max(-0.5, min(0.5, 0.5 * (left - right) / curvature)) if curvature else 0.0
    frequency = SAMPLE_RATE / (lag + adjustment)
    if not 55 <= frequency <= 1000:
        return {"status": "outside_frequency_range", "rms": round(rms, 6),
                "candidate_hz": None, "periodicity": round(center, 4)}
    return {"status": "periodic_candidate", "rms": round(rms, 6),
            "candidate_hz": round(frequency, 2), "periodicity": round(center, 4)}


def measure_pitch(raw: bytes, start_ms: int, source_context: str, offset: int, limit: int) -> dict[str, Any]:
    samples = tuple(sample[0] for sample in struct.iter_unpack("<h", raw))
    count = max(0, 1 + (len(samples) - FRAME_SAMPLES) // HOP_SAMPLES)
    frames = []
    for index in range(offset, min(count, offset + limit)):
        begin = index * HOP_SAMPLES
        frames.append({"start_ms": start_ms + round(begin * 1000 / SAMPLE_RATE),
                       "end_ms": start_ms + round((begin + FRAME_SAMPLES) * 1000 / SAMPLE_RATE),
                       **_estimate(samples[begin:begin + FRAME_SAMPLES])})
    return {
        "method": "mean-centered normalized autocorrelation; shortest strong local period; parabolic interpolation",
        "analysis_sample_rate_hz": SAMPLE_RATE, "frame_ms": 80, "hop_ms": 50,
        "frequency_min_hz": 55, "frequency_max_hz": 1000,
        "rms_floor": RMS_FLOOR, "periodicity_floor": PERIODICITY_FLOOR,
        "source_context": source_context, "source_context_provenance": "caller_declared_unverified",
        "decoded_duration_ms": round(len(samples) * 1000 / SAMPLE_RATE),
        "total_frames": count, "frames": frames,
        "next_offset": offset + limit if offset + limit < count else None,
        "limitations": "A periodicity candidate is not evidence of a human voice, singer, instrument identity, "
                       "or emotion. A full mix can produce misleading pitches; this is a monophonic estimator. "
                       "Octave errors, percussion, multiple sources, and mono downmix cancellation are possible. "
                       "Periodicity is not a probability or calibrated confidence. "
                       "No candidate does not mean no voice. A partial final frame is omitted.",
    }
