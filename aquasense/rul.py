"""Remaining Useful Life (build plan 8.3 D): days until a component reaches
the 40 % replacement threshold, given as a range (5th to 95th percentile).

Two quantile models per component:
  trend    uses the health trend when 3+ days of daily health exist (cloud fleet)
  notrend  uses only current health + operating context (a short rig demo,
           or a handheld scan of a card with a few hours of data)

Both are trained on simulated run-to-failure sequences (degradation.py) and
must be presented as simulated.
"""

from __future__ import annotations

import math

import numpy as np

from . import schema

QUANTILES = (0.05, 0.5, 0.95)
DEFAULT_USAGE_H = 2.0  # household-equivalent pump hours per day
TREND_FEATURES = ["health", "slope_14d", "tds_in", "usage_h"]
NOTREND_FEATURES = ["health", "tds_in", "usage_h"]


def predict_rul(rul_models: dict | None, component: str, health: float, tds_in: float,
                usage_h: float = DEFAULT_USAGE_H, slope_14d: float | None = None) -> dict:
    """Returns {'low', 'mid', 'high', 'method'} in days, or zeros when health is
    already below the threshold."""
    if health < schema.REPLACE_THRESHOLD:
        return {"low": 0, "mid": 0, "high": 0, "method": "below threshold"}
    models = (rul_models or {}).get(component)
    use_trend = slope_14d is not None and not (isinstance(slope_14d, float) and math.isnan(slope_14d))
    if models:
        kind = "trend" if use_trend and "trend" in models else "notrend"
        names = TREND_FEATURES if kind == "trend" else NOTREND_FEATURES
        ctx = {"health": health, "slope_14d": slope_14d, "tds_in": tds_in, "usage_h": usage_h}
        x = np.array([[float(ctx[n]) for n in names]])
        q = sorted(max(0.0, float(models[kind][a].predict(x)[0])) for a in QUANTILES)
        return {"low": int(round(q[0])), "mid": int(round(q[1])), "high": int(round(q[2])), "method": kind}
    # Fallback without models: linear extrapolation from the expected wear rate
    from .degradation import wear_rate

    if component not in ("pump", "membrane", "filter"):
        return {"low": 0, "mid": 0, "high": 0, "method": "n/a"}
    rate = -slope_14d if use_trend and slope_14d < 0 else wear_rate(component, tds_in, usage_h) * 1.6
    mid = (health - schema.REPLACE_THRESHOLD) / max(rate, 1e-3)
    return {"low": int(mid * 0.8), "mid": int(mid), "high": int(mid * 1.25), "method": "rate"}


def prob_fail_within(rul: dict, days: float = 30.0) -> float:
    """P(RUL < days) from the three quantiles, by piecewise-linear CDF."""
    lo, mid, hi = rul["low"], rul["mid"], rul["high"]
    if hi <= 0:
        return 1.0
    xs = [max(0.0, lo - (mid - lo) * 1.5), lo, mid, hi, hi + (hi - mid) * 1.5 + 1]
    ys = [0.0, 0.05, 0.5, 0.95, 1.0]
    # make xs strictly increasing
    for i in range(1, len(xs)):
        if xs[i] <= xs[i - 1]:
            xs[i] = xs[i - 1] + 1e-6
    return float(np.clip(np.interp(days, xs, ys), 0.0, 1.0))
