"""Bounded energy-attack and periodicity measurements for Gramophone passages."""
from __future__ import annotations

import math
import struct
from typing import Any


FRAME_MS = 10
SAMPLE_RATE = 8000
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
MAX_ATTACKS = 100


def measure_rhythm(raw: bytes, start_ms: int, end_ms: int) -> dict[str, Any]:
    samples = struct.iter_unpack("<h", raw)
    energies: list[float] = []
    total = count = 0
    for (sample,) in samples:
        total += sample * sample
        count += 1
        if count == FRAME_SAMPLES:
            energies.append(math.sqrt(total / count) / 32768)
            total = count = 0
    if count:
        energies.append(math.sqrt(total / count) / 32768)
    rises = [max(0.0, energy - (energies[i - 1] if i else energy))
             for i, energy in enumerate(energies)]
    # A fixed absolute floor suppresses quantization and steady-tone ripple. The
    # relative floor limits noisy spurious peaks. Quiet/legato attacks may be missed.
    threshold = max(0.015, max(rises, default=0.0) * 0.10)
    attacks: list[tuple[int, float]] = []
    for i, strength in enumerate(rises):
        if (strength < threshold or (i and strength < rises[i - 1]) or
                (i + 1 < len(rises) and strength <= rises[i + 1])):
            continue
        if attacks and i - attacks[-1][0] < 8:
            if strength > attacks[-1][1]:
                attacks[-1] = (i, strength)
        else:
            attacks.append((i, strength))
    candidates: list[dict[str, float]] = []
    if end_ms - start_ms >= 5000 and len(attacks) >= 4:
        # Binary impulses avoid treating louder hits as automatically more important.
        impulses = [0] * len(energies)
        for index, _ in attacks:
            impulses[index] = 1
        scores: dict[int, tuple[float, int]] = {}
        for lag in range(25, 121):  # 240 to 50 BPM, 10 ms resolution.
            pairs = sum(impulses[i] * impulses[i - lag] for i in range(lag, len(impulses)))
            left = sum(impulses[lag:])
            right = sum(impulses[:-lag])
            score = pairs / math.sqrt(left * right) if left and right else 0.0
            scores[lag] = (score, pairs)
        peaks = [(score, lag) for lag, (score, pairs) in scores.items()
                 if pairs >= 3 and score >= 0.2
                 and score >= scores.get(lag - 1, (0.0, 0))[0]
                 and score > scores.get(lag + 1, (0.0, 0))[0]]
        for score, lag in sorted(peaks, key=lambda entry: (-entry[0], entry[1]))[:5]:
            candidates.append({"bpm": round(6000 / lag, 1), "periodicity": round(score, 3)})
    return {
        "method": "10 ms mono RMS increases and binary attack autocorrelation",
        "frame_ms": FRAME_MS,
        "attack_threshold": round(threshold, 5),
        "attack_count": len(attacks),
        "attacks": [{"time_ms": start_ms + i * FRAME_MS, "strength": round(strength, 5)}
                    for i, strength in attacks[:MAX_ATTACKS]],
        "attacks_truncated": len(attacks) > MAX_ATTACKS,
        "tempo_candidates": candidates,
        "limitations": "Energy attacks can miss soft or legato sounds and do not identify instruments. "
                       "The passage start has no preceding audio and is not counted as an attack. "
                       "Tempo candidates are estimates, not probabilities; half/double tempo and syncopation are ambiguous.",
    }
