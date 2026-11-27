"""Experimental pitch-class energy and template comparisons for PCM passages."""
from __future__ import annotations

import math
import struct
from typing import Any


SAMPLE_RATE = 8000
FFT_SIZE = 2048
HOP_SIZE = 1024
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# A4=440 Hz and twelve-tone equal temperament are analysis assumptions.
_HANN = tuple(0.5 - 0.5 * math.cos(2 * math.pi * i / (FFT_SIZE - 1)) for i in range(FFT_SIZE))
_BIN_CLASS = tuple(
    (index, round(69 + 12 * math.log2((index * SAMPLE_RATE / FFT_SIZE) / 440)) % 12)
    for index in range(1, FFT_SIZE // 2 + 1)
    if 80 <= index * SAMPLE_RATE / FFT_SIZE <= 3000
)
_TWIDDLES = {size: tuple(complex(math.cos(-2 * math.pi * k / size), math.sin(-2 * math.pi * k / size))
                         for k in range(size // 2)) for size in (2 ** n for n in range(1, 12))}
_REVERSED = tuple(int(f"{index:011b}"[::-1], 2) for index in range(FFT_SIZE))


def _power(frame: list[int]) -> tuple[list[float], float]:
    values = [0j] * FFT_SIZE
    for i in range(min(len(frame), FFT_SIZE)):
        values[_REVERSED[i]] = complex(frame[i] * _HANN[i], 0)
    size = 2
    while size <= FFT_SIZE:
        half = size // 2
        roots = _TWIDDLES[size]
        for start in range(0, FFT_SIZE, size):
            for k in range(half):
                upper = values[start + k]
                lower = values[start + k + half] * roots[k]
                values[start + k] = upper + lower
                values[start + k + half] = upper - lower
        size *= 2
    result = [0.0] * 12
    for index, pitch in _BIN_CLASS:
        value = values[index]
        result[pitch] += value.real * value.real + value.imag * value.imag
    full = sum(value.real * value.real + value.imag * value.imag for value in values[:FFT_SIZE // 2 + 1])
    return result, full


def _window(samples: list[int], previous: list[float] | None) -> tuple[dict[str, Any], list[float] | None]:
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768 if samples else 0.0
    if rms < 0.001:
        return {"active": False, "inactive_reason": "below_rms_floor", "rms": round(rms, 6),
                "band_energy_fraction": None, "pitch_class_energy": None,
                "triad_candidates": [], "change_from_previous": None}, None
    starts = list(range(0, max(1, len(samples) - FFT_SIZE + 1), HOP_SIZE))
    tail = max(0, len(samples) - FFT_SIZE)
    if starts[-1] != tail:
        starts.append(tail)
    energies = [0.0] * 12
    full = 0.0
    for start in starts:
        band, frame_full = _power(samples[start:start + FFT_SIZE])
        full += frame_full
        for index, value in enumerate(band):
            energies[index] += value
    total = sum(energies)
    fraction = total / full if full else 0.0
    if total <= 1e-12 or fraction < 0.001:
        return {"active": False, "inactive_reason": "below_band_energy_floor", "rms": round(rms, 6),
                "band_energy_fraction": round(fraction, 6), "pitch_class_energy": None,
                "triad_candidates": [], "change_from_previous": None}, None
    chroma = [item / total for item in energies]
    candidates = []
    for root in range(12):
        for quality, third in (("major", 4), ("minor", 3)):
            intervals = (root, (root + third) % 12, (root + 7) % 12)
            score = sum(chroma[index] for index in intervals)
            if score >= 0.42 and all(chroma[index] >= 0.08 for index in intervals):
                candidates.append({"root": PITCH_CLASSES[root], "quality": quality, "energy_match": round(score, 4)})
    candidates.sort(key=lambda item: (-item["energy_match"], item["root"], item["quality"]))
    change = None
    if previous is not None:
        norm = math.sqrt(sum(item * item for item in chroma) * sum(item * item for item in previous))
        change = round(max(0.0, min(1.0, 1 - sum(a * b for a, b in zip(chroma, previous)) / norm)), 4) if norm else None
    return {"active": True, "inactive_reason": None, "rms": round(rms, 6),
            "band_energy_fraction": round(fraction, 6), "pitch_class_energy": {name: round(value, 4) for name, value in zip(PITCH_CLASSES, chroma)},
            "triad_candidates": candidates[:3], "change_from_previous": change}, chroma


def measure_harmony(raw: bytes, start_ms: int, end_ms: int) -> dict[str, Any]:
    samples = [item[0] for item in struct.iter_unpack("<h", raw)]
    windows = []
    previous = None
    for offset in range(0, len(samples), SAMPLE_RATE):
        chunk = samples[offset:offset + SAMPLE_RATE]
        features, previous = _window(chunk, previous)
        windows.append({"start_ms": start_ms + round(offset * 1000 / SAMPLE_RATE),
                        "end_ms": min(end_ms, start_ms + round((offset + len(chunk)) * 1000 / SAMPLE_RATE)),
                        **features})
    return {
        "method": "mono 8 kHz PCM; Hann 2048-point FFT; 80–3000 Hz bins mapped to 12 pitch classes",
        "analysis_sample_rate_hz": SAMPLE_RATE, "frequency_min_hz": 80, "frequency_max_hz": 3000,
        "tuning_hz": 440, "window_ms": 1000, "fft_samples": FFT_SIZE, "hop_samples": HOP_SIZE,
        "silence_rms_floor": 0.001, "band_energy_floor": 0.001, "triad_match_floor": 0.42, "triad_tone_floor": 0.08,
        "decoded_duration_ms": round(len(samples) * 1000 / SAMPLE_RATE),
        "windows": windows,
        "limitations": "Pitch-class energy includes overtones and percussion, assumes A4=440 and equal temperament, "
                       "and does not identify played notes, key, or emotion. Mono downmix can cancel out-of-phase material. "
                       "Triad matches are simple energy "
                       "overlap scores, not probabilities or verified chords. Change compares neighboring "
                       "spectral profiles, not musical tension; silence breaks the comparison.",
    }
