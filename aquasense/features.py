"""Window features (build plan 8.2).

For every 60-second window: mean, standard deviation and slope of each
signal, plus build-up time, start-up current, rejection % and pressure per
amp. Statistics use pump-ON rows only; two pump-OFF features are kept
because a frozen or disconnected sensor is easiest to spot while the pump is
off (pressure should collapse, flow should be zero).

The same code runs in the cloud (on rows received over Wi-Fi) and on the
handheld (on rows read from the SD card), so both give identical results.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from . import schema

SIGNALS = ["tds_in", "tds_out", "temp_c", "pressure_bar", "flow_lpm", "pump_current_a", "rejection_pct", "p_per_amp"]
STATS = ["mean", "std", "slope"]
EXTRA = ["buildup_s", "startup_current_a", "p_off_mean", "flow_off_mean"]
FEATURE_NAMES = [f"{s}_{k}" for s in SIGNALS for k in STATS] + EXTRA + ["tds_in_cv", "tds_out_cv"]

# Features the models are allowed to see. Absolute TDS levels and water
# temperature are left out on purpose: with only a few runs per fault level a
# model would otherwise learn "this run had warm water" instead of the fault,
# and a high-TDS source must never look like a fault. TDS enters the models
# only through rejection % and the noise level (coefficient of variation).
_EXCLUDE = {"temp_c_mean", "temp_c_std", "temp_c_slope", "tds_in_mean", "tds_in_std", "tds_in_slope",
            "tds_out_mean", "tds_out_std", "tds_out_slope"}
MODEL_FEATURES = [n for n in FEATURE_NAMES if n not in _EXCLUDE]
MIN_ON_ROWS = 3
BIN_S = 10
TRANSIENT_S = 10

PRETTY = {
    "tds_in": "TDS in",
    "tds_out": "TDS out",
    "temp_c": "Water temperature",
    "pressure_bar": "Pressure",
    "flow_lpm": "Reject flow",
    "pump_current_a": "Pump current",
    "rejection_pct": "Rejection %",
    "p_per_amp": "Pressure per amp",
    "buildup_s": "Build-up time",
    "startup_current_a": "Start-up current",
    "p_off_mean": "Pressure while pump off",
    "flow_off_mean": "Flow while pump off",
    "tds_in_cv": "TDS in (noise)",
    "tds_out_cv": "TDS out (noise)",
}
STAT_PRETTY = {"mean": "level", "std": "variability", "slope": "trend"}


def pretty_feature(name: str) -> str:
    if name in PRETTY:
        return PRETTY[name]
    base, _, stat = name.rpartition("_")
    return f"{PRETTY.get(base, base)} ({STAT_PRETTY.get(stat, stat)})"


def to_frame(rows: Iterable[dict] | pd.DataFrame) -> pd.DataFrame:
    """Normalise raw CSV rows (strings or numbers) into a typed DataFrame."""
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        return pd.DataFrame(columns=schema.CSV_HEADER)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for c in schema.NUMERIC_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["timestamp", "pressure_bar", "flow_lpm", "pump_current_a", "tds_in", "tds_out"])
    df = df.sort_values("timestamp").drop_duplicates(subset=["device_id", "timestamp"] if "device_id" in df else ["timestamp"])
    df = df.reset_index(drop=True)
    df["pump_on"] = df["pump_on"].fillna(0).astype(int)
    df["pump_cycles"] = df["pump_cycles"].fillna(-1)
    # Rows in the first 10 s after a pump start are the start-up transient. Marking
    # them by time (not by row count) treats 2 s and 5 s logging identically.
    on = df["pump_on"] == 1
    df["_transient"] = False
    if on.any():
        first = df.loc[on].groupby(df.loc[on, "pump_cycles"])["timestamp"].transform("min")
        df.loc[on, "_transient"] = (df.loc[on, "timestamp"] - first).dt.total_seconds() < TRANSIENT_S
    df = resample(df)
    tin = df["tds_in"].clip(lower=1.0)
    df["rejection_pct"] = (1 - df["tds_out"] / tin) * 100
    df["p_per_amp"] = df["pressure_bar"] / df["pump_current_a"].clip(lower=0.05)
    return df


def resample(df: pd.DataFrame, bin_s: int = BIN_S) -> pd.DataFrame:
    """Average rows into 10 s bins (never mixing pump ON and OFF, or two pump
    cycles). Training mode logs every 2 s and normal mode every 5 s; binning
    makes the variability features identical for both, so a model trained on
    training-mode recordings works on normal-mode data in the field."""
    if df.empty:
        return df
    epoch = ((df["timestamp"] - pd.Timestamp("2000-01-01")).dt.total_seconds()).astype("int64")
    keys = [epoch // bin_s, df["pump_on"], df["pump_cycles"], df["_transient"]]
    num = [c for c in schema.NUMERIC_COLUMNS if c in df.columns and c not in ("pump_on", "pump_cycles")]
    agg = {c: "mean" for c in num}
    agg.update(timestamp="last", pump_on="first", pump_cycles="first", _transient="first")
    if "device_id" in df.columns:
        agg["device_id"] = "last"
    if "label" in df.columns:
        agg["label"] = "last"
    out = df.groupby(keys, sort=False).agg(agg).reset_index(drop=True)
    return out.sort_values("timestamp").reset_index(drop=True)


def _slope_per_min(t_s: np.ndarray, y: np.ndarray) -> float:
    if len(y) < 3 or np.ptp(t_s) == 0:
        return 0.0
    return float(np.polyfit(t_s, y, 1)[0] * 60.0)


def window_features(w: pd.DataFrame) -> dict | None:
    """Features for one window of rows (already typed by to_frame)."""
    on_all = w[w["pump_on"] == 1]
    # Steady-state statistics skip the start-up transient; start-up behaviour is
    # captured by buildup_s and the start-up current.
    on = on_all[~on_all["_transient"]]
    if len(on) < MIN_ON_ROWS:
        return None
    t_s = (on["timestamp"] - on["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
    f: dict[str, float] = {}
    for s in SIGNALS:
        y = on[s].to_numpy(dtype=float)
        f[f"{s}_mean"] = float(np.mean(y))
        f[f"{s}_std"] = float(np.std(y))
        f[f"{s}_slope"] = _slope_per_min(t_s, y)
    f["buildup_s"] = float(on["buildup_s"].mean())
    starts = on_all[on_all["_transient"]]
    f["startup_current_a"] = float(starts["pump_current_a"].mean()) if len(starts) else np.nan
    off = w[w["pump_on"] == 0]
    f["p_off_mean"] = float(off["pressure_bar"].mean()) if len(off) else np.nan
    f["flow_off_mean"] = float(off["flow_lpm"].mean()) if len(off) else np.nan
    f["tds_in_cv"] = f["tds_in_std"] / max(f["tds_in_mean"], 1.0)
    f["tds_out_cv"] = f["tds_out_std"] / max(f["tds_out_mean"], 1.0)
    return f


def sliding_windows(df: pd.DataFrame, window_s: int = schema.WINDOW_S, step_s: int = 30) -> list[tuple[pd.Timestamp, dict, pd.DataFrame]]:
    """All windows of `window_s` seconds, every `step_s` seconds. Returns
    (window end time, features, rows) for windows with enough pump-ON rows."""
    if df.empty:
        return []
    out = []
    t0, t1 = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
    end = t0 + pd.Timedelta(seconds=window_s)
    ts = df["timestamp"].to_numpy()
    while end <= t1 + pd.Timedelta(seconds=1):
        lo = np.searchsorted(ts, np.datetime64(end - pd.Timedelta(seconds=window_s)), side="right")
        hi = np.searchsorted(ts, np.datetime64(end), side="right")
        w = df.iloc[lo:hi]
        feats = window_features(w)
        if feats is not None:
            out.append((end, feats, w))
        end += pd.Timedelta(seconds=step_s)
    return out


def latest_window(df: pd.DataFrame, window_s: int = schema.WINDOW_S, end: pd.Timestamp | None = None) -> tuple[pd.Timestamp, dict, pd.DataFrame] | None:
    """The window ending at `end` (default: the newest row). Widens up to 3x
    if the pump was mostly off in the last minute."""
    if df.empty:
        return None
    if end is None:
        end = df["timestamp"].iloc[-1]
    else:
        df = df[df["timestamp"] <= end]
    for mult in (1, 2, 3):
        w = df[df["timestamp"] > end - pd.Timedelta(seconds=window_s * mult)]
        feats = window_features(w)
        if feats is not None:
            return end, feats, w
    return None


def feature_matrix(feature_dicts: list[dict], names: list[str] | None = None) -> np.ndarray:
    names = names or MODEL_FEATURES
    X = np.array([[d.get(n, np.nan) for n in names] for d in feature_dicts], dtype=float)
    return X


def fill_missing(X: np.ndarray) -> np.ndarray:
    """Models that cannot take NaN (Random Forest, Isolation Forest) get -1."""
    return np.where(np.isnan(X), -1.0, X)
