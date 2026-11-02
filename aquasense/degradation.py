"""Degradation simulator for long-term RUL (build plan 8.5).

We cannot wait months for real wear, so health falls over simulated days
based on usage and source water TDS (harder water wears the membrane and
clogs the filter faster), with random unit-to-unit spread and daily noise.

ALL OUTPUT OF THIS MODULE IS SIMULATED DATA. Label it as such wherever it is
shown. The rig data tells us how each signal changes with severity; this
module only supplies the time axis.
"""

from __future__ import annotations

import numpy as np

from . import schema

DEGRADING = ["pump", "membrane", "filter"]


def wear_rate(component: str, tds_in: float, usage_h_per_day: float) -> float:
    """Expected health points lost per day at the start of life."""
    tds_k = (max(tds_in, 50.0) / 500.0)
    use_k = usage_h_per_day / 2.0
    if component == "membrane":  # ~18 months at 300 ppm, ~7 months at 1500 ppm (2 h/day)
        return 0.085 * tds_k ** 0.6 * use_k
    if component == "filter":  # ~9 months municipal, ~4 months borewell
        return 0.16 * tds_k ** 0.5 * use_k
    if component == "pump":  # ~3 years, driven by runtime and start cycles
        return 0.045 * use_k ** 1.1
    raise ValueError(component)


def simulate_life(component: str, tds_in: float, usage_h_per_day: float, rng: np.random.Generator,
                  start_health: float = 100.0, max_days: int = 2500) -> np.ndarray:
    """Daily health from start_health until it drops below 15 (one value per day).

    Health(t) = start - a * t^b with an accelerating exponent (wear speeds up
    near end of life), a unit-specific rate multiplier and daily noise.
    """
    base = wear_rate(component, tds_in, usage_h_per_day) * rng.lognormal(0, 0.25)
    b = rng.uniform(1.15, 1.45)
    # pick a so the expected linear-rate life matches base
    life_to_40 = (start_health - schema.REPLACE_THRESHOLD) / base
    a = (start_health - schema.REPLACE_THRESHOLD) / life_to_40 ** b
    days = np.arange(max_days)
    # day-to-day usage varies; the noise is a random walk + measurement noise
    usage_noise = np.cumsum(rng.normal(0, 0.02, size=max_days))
    h = start_health - a * np.power(days, b) * (1 + 0.05 * np.tanh(usage_noise))
    h = h + rng.normal(0, 0.8, size=max_days)
    below = np.nonzero(h < 15)[0]
    end = int(below[0]) + 1 if len(below) else max_days
    return np.clip(h[:end], 0, 100)


def days_to_threshold(h: np.ndarray, start_idx: int, threshold: float = schema.REPLACE_THRESHOLD) -> int:
    """True remaining days from start_idx until the (smoothed) health crosses the threshold."""
    k = 5
    sm = np.convolve(h, np.ones(k) / k, mode="same")
    after = np.nonzero(sm[start_idx:] < threshold)[0]
    return int(after[0]) if len(after) else int(len(h) - start_idx)


def trend_slope(history: np.ndarray, days: int = 14) -> float:
    """Health points per day over the last `days` daily values."""
    y = np.asarray(history[-days:], dtype=float)
    if len(y) < 3:
        return float("nan")
    return float(np.polyfit(np.arange(len(y)), y, 1)[0])


def simulate_fleet_history(rng: np.random.Generator, tds_in: float, usage_h: float, age_days: int,
                           start_health: dict | None = None) -> dict[str, np.ndarray]:
    """Daily health history (age_days long) for each degrading component of one unit."""
    out = {}
    for comp in DEGRADING:
        sh = (start_health or {}).get(comp, 100.0)
        life = simulate_life(comp, tds_in, usage_h, rng, start_health=sh)
        if len(life) < age_days:
            life = np.concatenate([life, np.full(age_days - len(life), life[-1])])
        out[comp] = life[:age_days]
    return out
