"""Train every AquaSense model and write the joblib files the cloud and the
handheld both load (build plan 8.3 to 8.6).

    python -m ml.train                      # synthetic rig data (default)
    python -m ml.train --real data/rig_runs # your labelled rig recordings

Honest-evaluation rules (build plan 8.4):
  * split by RUN, never by row: whole runs are held out for testing
  * confusion matrix and F1 per class on the held-out runs
  * high-TDS normal water is tested explicitly (must NOT be a fault)

Outputs: models/*.joblib, models/model_card.json, reports/training_report.md
"""

from __future__ import annotations

import argparse
import glob
import json
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import classification_report, confusion_matrix, f1_score, mean_absolute_error

from aquasense import degradation, features as F, rul, schema
from aquasense.rigsim import record_run

from .generate_rig_data import protocol

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"

try:
    import xgboost as xgb
except Exception:  # pragma: no cover - fallback path
    xgb = None


# ---------------------------------------------------------------- data
def load_synthetic(repeats: int, seed: int) -> pd.DataFrame:
    rows = []
    for s in protocol(repeats, seed):
        for r in record_run(s):
            r["sensor_mode"] = s.sensor_mode or ""
            r["tds_source"] = s.tds_source
            rows.append(r)
    return pd.DataFrame(rows)


def load_real(folder: Path) -> pd.DataFrame:
    """SD-card CSVs (training mode) + logbook.csv with run_id,start,end,class,level,salt_ppm."""
    book = pd.read_csv(folder / "logbook.csv", parse_dates=["start", "end"])
    frames = [pd.read_csv(p) for p in sorted(glob.glob(str(folder / "*.csv"))) if not p.endswith("logbook.csv")]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    parts = []
    for _, b in book.iterrows():
        seg = df[(df["timestamp"] >= b["start"]) & (df["timestamp"] <= b["end"])].copy()
        seg["run_id"], seg["level"], seg["label"] = b["run_id"], int(b["level"]), int(b["class"])
        sm = b.get("sensor_mode", "")
        seg["sensor_mode"] = "" if pd.isna(sm) else str(sm)
        seg["tds_source"] = float(b.get("salt_ppm", 300))
        parts.append(seg)
    return pd.concat(parts, ignore_index=True)


def windows_from_runs(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for run_id, g in df.groupby("run_id", sort=False):
        meta = g.iloc[0]
        fr = F.to_frame(g[schema.CSV_HEADER])
        for end, feats, _ in F.sliding_windows(fr, step_s=30):
            feats.update(run_id=run_id, cls=int(meta["label"]), level=int(meta["level"]),
                         tds_source=float(meta["tds_source"]), sensor_mode=meta.get("sensor_mode", ""), end=end)
            out.append(feats)
    return pd.DataFrame(out)


def split_by_run(win: pd.DataFrame, test_every: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """For each (class, level, variant) hold out every `test_every`-th run."""
    runs = win.groupby("run_id").first().reset_index()
    runs["variant"] = runs["cls"].astype(str) + "-" + runs["level"].astype(str) + "-" + runs["sensor_mode"].astype(str)
    runs.loc[runs["cls"] == schema.NORMAL, "variant"] += "-" + (runs["tds_source"] / 100).round().astype(int).astype(str)
    test_runs = set()
    for _, g in runs.groupby("variant"):
        ids = list(g["run_id"])
        test_runs.update(ids[test_every - 1::test_every] or ids[-1:])
    is_test = win["run_id"].isin(test_runs).to_numpy()
    return ~is_test, is_test


# ---------------------------------------------------------------- models
def train_classifier(X, y):
    if xgb is not None:
        m = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.08, subsample=0.8,
                              colsample_bytree=0.7, min_child_weight=3, objective="multi:softprob", num_class=5,
                              eval_metric="mlogloss", random_state=7, n_jobs=2)
        m.fit(X, y)
        return m, "xgboost"
    m = RandomForestClassifier(n_estimators=400, min_samples_leaf=2, random_state=7, n_jobs=2)
    m.fit(F.fill_missing(X), y)
    return m, "random_forest"


def health_target(cls: np.ndarray, level: np.ndarray, comp: str) -> np.ndarray:
    target_cls = schema.COMPONENT_TO_CLASS[comp]
    return np.array([schema.LEVEL_HEALTH[int(l)] if c == target_cls else 100.0 for c, l in zip(cls, level)])


def train_health(X, cls, level) -> dict:
    models = {}
    for comp in ("pump", "membrane", "filter"):
        y = health_target(cls, level, comp)
        if xgb is not None:
            m = xgb.XGBRegressor(n_estimators=300, max_depth=3, learning_rate=0.06, subsample=0.8,
                                 colsample_bytree=0.7, min_child_weight=3, random_state=7, n_jobs=2)
        else:
            m = RandomForestRegressor(n_estimators=300, min_samples_leaf=2, random_state=7, n_jobs=2)
        m.fit(F.fill_missing(X), y)
        models[comp] = m
    return models


def rul_dataset(comp: str, n_lives: int, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for _ in range(n_lives):
        tds = float(np.exp(rng.uniform(np.log(150), np.log(2000))))
        usage = float(rng.uniform(0.5, 5.0))
        h = degradation.simulate_life(comp, tds, usage, rng)
        if len(h) < 30:
            continue
        for idx in rng.integers(14, len(h), size=12):
            health = float(np.mean(h[max(0, idx - 2): idx + 1]))
            if health < 25:
                continue
            rows.append({"health": health, "slope_14d": degradation.trend_slope(h[: idx + 1]), "tds_in": tds,
                         "usage_h": usage, "y": degradation.days_to_threshold(h, idx)})
    return pd.DataFrame(rows)


def train_rul(rng: np.random.Generator, n_lives: int = 500) -> tuple[dict, dict]:
    models, metrics = {}, {}
    for comp in ("pump", "membrane", "filter"):
        df = rul_dataset(comp, n_lives, rng)
        df = df[df["health"] >= schema.REPLACE_THRESHOLD]
        n_test = len(df) // 5
        test, train = df.iloc[:n_test], df.iloc[n_test:]
        models[comp], metrics[comp] = {}, {}
        for kind, cols in (("trend", rul.TREND_FEATURES), ("notrend", rul.NOTREND_FEATURES)):
            models[comp][kind] = {}
            preds = {}
            for q in rul.QUANTILES:
                m = GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=250, max_depth=3,
                                              learning_rate=0.06, subsample=0.9, random_state=7)
                m.fit(train[cols].to_numpy(), train["y"].to_numpy())
                models[comp][kind][q] = m
                preds[q] = m.predict(test[cols].to_numpy())
            lo_q, hi_q = rul.QUANTILES[0], rul.QUANTILES[-1]
            inside = (test["y"] >= preds[lo_q]) & (test["y"] <= preds[hi_q])
            metrics[comp][kind] = {
                "coverage_range": round(float(inside.mean()), 3),
                "median_abs_error_days": round(float(np.median(np.abs(test["y"] - preds[0.5]))), 1),
                "train_points": int(len(train)), "test_points": int(len(test)),
            }
    return models, metrics


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", help="folder with real rig recordings + logbook.csv")
    ap.add_argument("--repeats", type=int, default=3, help="synthetic repeats per variant (default 3 = 45 runs)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--models", default=str(MODELS))
    a = ap.parse_args(argv)
    t0 = time.time()
    out_dir = Path(a.models)
    out_dir.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(exist_ok=True)

    source = "real rig recordings" if a.real else "SYNTHETIC rig data (aquasense/rigsim.py, protocol of build plan 8.1)"
    print("Loading", source)
    raw = load_real(Path(a.real)) if a.real else load_synthetic(a.repeats, a.seed)
    win = windows_from_runs(raw)
    records = win.to_dict("records")
    X = F.feature_matrix(records)  # model features only
    X_all = F.feature_matrix(records, F.FEATURE_NAMES)  # everything, for the cross-check baseline
    y, lvl = win["cls"].to_numpy(), win["level"].to_numpy()
    tr, te = split_by_run(win)
    print(f"{raw['run_id'].nunique()} runs, {len(raw)} rows, {len(win)} windows; "
          f"train runs {win[tr]['run_id'].nunique()}, held-out runs {win[te]['run_id'].nunique()}")

    # A. fault classifier
    clf, kind = train_classifier(X[tr], y[tr])
    # B. anomaly detector on normal windows only + healthy baseline for the cross-check
    normal = tr & (y == schema.NORMAL)
    iso = IsolationForest(n_estimators=300, contamination="auto", random_state=7).fit(F.fill_missing(X[normal]))
    thr = float(np.quantile(iso.decision_function(F.fill_missing(X[normal])), 0.01))
    baseline = {n: [float(np.nanmean(X_all[normal][:, i])), float(np.nanstd(X_all[normal][:, i]))] for i, n in enumerate(F.FEATURE_NAMES)}
    # C. health regressors
    health = train_health(X[tr], y[tr], lvl[tr])
    # D. RUL forecaster
    rul_models, rul_metrics = train_rul(np.random.default_rng(a.seed))

    joblib.dump({"model": clf, "kind": kind, "feature_names": F.MODEL_FEATURES, "classes": schema.CLASS_NAMES}, out_dir / "fault_classifier.joblib")
    joblib.dump({"model": iso, "threshold": thr, "baseline": baseline}, out_dir / "anomaly_detector.joblib")
    joblib.dump(health, out_dir / "health_regressors.joblib")
    joblib.dump(rul_models, out_dir / "rul_forecaster.joblib")

    # ------------------------------------------------ evaluation on held-out runs
    from aquasense.engine import Engine

    eng = Engine(out_dir)
    names = [schema.CLASS_NAMES[i] for i in range(5)]
    Xte = X[te]
    raw_pred = clf.predict(Xte if kind == "xgboost" else F.fill_missing(Xte))
    test_rows = win[te].to_dict("records")
    diag = [eng.diagnose_window({k: r[k] for k in F.FEATURE_NAMES}) for r in test_rows]
    full_pred = np.array([d["predicted_class"] for d in diag])
    yte = y[te]

    cm_raw = confusion_matrix(yte, raw_pred, labels=range(5))
    cm_full = confusion_matrix(yte, full_pred, labels=range(5))
    rep_full = classification_report(yte, full_pred, labels=range(5), target_names=names, output_dict=True, zero_division=0)
    f1_raw = f1_score(yte, raw_pred, average="macro")
    f1_full = f1_score(yte, full_pred, average="macro")

    # run-level vote (what the console/handheld show after a few windows)
    tdf = win[te][["run_id", "cls"]].copy()
    tdf["pred"] = full_pred
    run_vote = tdf.groupby("run_id").agg(cls=("cls", "first"), pred=("pred", lambda s: s.mode().iloc[0]))
    run_acc = float((run_vote["cls"] == run_vote["pred"]).mean())

    # explicit high-TDS normal-water test
    hi = (yte == schema.NORMAL) & (win[te]["tds_source"].to_numpy() > 1200)
    hi_false_alarm = float((full_pred[hi] != schema.NORMAL).mean()) if hi.any() else float("nan")

    # health MAE
    Xh = F.fill_missing(Xte)
    health_mae = {c: round(float(mean_absolute_error(health_target(yte, lvl[te], c), np.clip(health[c].predict(Xh), 0, 100))), 2)
                  for c in health}

    card = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": source,
        "simulated": not bool(a.real),
        "runs": int(raw["run_id"].nunique()), "rows": int(len(raw)), "windows": int(len(win)),
        "held_out_runs": int(win[te]["run_id"].nunique()),
        "classifier": kind,
        "macro_f1_model_only": round(float(f1_raw), 3),
        "macro_f1_with_cross_check": round(float(f1_full), 3),
        "f1_per_class": {k: round(v["f1-score"], 3) for k, v in rep_full.items() if k in names},
        "run_level_accuracy": round(run_acc, 3),
        "high_tds_normal_false_alarm_rate": None if np.isnan(hi_false_alarm) else round(hi_false_alarm, 3),
        "health_mae_points": health_mae,
        "rul": rul_metrics,
        "rul_note": "RUL models are trained on simulated run-to-failure data (aquasense/degradation.py).",
        "anomaly_threshold": round(thr, 4),
        "train_seconds": round(time.time() - t0, 1),
    }
    (out_dir / "model_card.json").write_text(json.dumps(card, indent=2))
    write_report(card, cm_raw, cm_full, names, run_vote)
    print(json.dumps({k: card[k] for k in ("macro_f1_model_only", "macro_f1_with_cross_check", "run_level_accuracy",
                                           "high_tds_normal_false_alarm_rate", "health_mae_points")}, indent=2))
    print(f"Saved models to {out_dir} and report to {REPORTS / 'training_report.md'} in {card['train_seconds']} s")
    return card


def _md_cm(cm, names) -> str:
    short = ["Normal", "Clog", "Pump", "Membrane", "Sensor"]
    lines = ["| true \\ predicted | " + " | ".join(short) + " |", "|---" * (len(short) + 1) + "|"]
    for i, row in enumerate(cm):
        lines.append(f"| **{short[i]}** | " + " | ".join(str(int(v)) for v in row) + " |")
    return "\n".join(lines)


def write_report(card, cm_raw, cm_full, names, run_vote):
    sim = "**SIMULATED DATA.** " if card["simulated"] else ""
    f1_rows = "\n".join(f"| {k} | {v:.3f} |" for k, v in card["f1_per_class"].items())
    rul_rows = "\n".join(
        f"| {c} | {k} | {m['coverage_range']:.0%} | {m['median_abs_error_days']} |"
        for c, d in card["rul"].items() for k, m in d.items())
    txt = f"""# AquaSense training report

{sim}Data: {card['data_source']}. Trained {card['trained_at']}.

{card['runs']} runs, {card['rows']} log rows, {card['windows']} windows of 60 s.
**Split by run:** {card['held_out_runs']} whole runs held out for testing (one repeat of every variant).

## Fault classifier ({card['classifier']}) on held-out runs

| Metric | Value |
|---|---|
| Macro F1, model only | {card['macro_f1_model_only']:.3f} |
| Macro F1, model + cross-check logic | {card['macro_f1_with_cross_check']:.3f} |
| Run-level accuracy (majority of windows) | {card['run_level_accuracy']:.0%} |
| High-TDS normal water flagged as a fault | {card['high_tds_normal_false_alarm_rate']} |

### F1 per class (with cross-check)

| Class | F1 |
|---|---|
{f1_rows}

### Confusion matrix, model only (windows)

{_md_cm(cm_raw, names)}

### Confusion matrix, model + cross-check (windows)

{_md_cm(cm_full, names)}

## Health regressors (mean absolute error, health points)

{', '.join(f'{k}: {v}' for k, v in card['health_mae_points'].items())}

## RUL forecaster (simulated run-to-failure data)

| Component | Model | True RUL inside 5 to 95 % range | Median error (days) |
|---|---|---|---|
{rul_rows}

{card['rul_note']} Present these numbers as simulated.

## Caveat

{"These scores come from synthetic data produced by the rig model. They prove the pipeline works end to end; they are not evidence of real-world accuracy. Re-run `python -m ml.train --real data/rig_runs` once the 35 to 45 labelled rig runs are recorded." if card['simulated'] else "Scores come from real rig recordings with whole runs held out."}
"""
    (REPORTS / "training_report.md").write_text(txt)


if __name__ == "__main__":
    main()
