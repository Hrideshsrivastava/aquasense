"""The AquaSense diagnosis engine: one class used by the cloud server and
the technician handheld, loading the same joblib files so results match
(build plan 8.6).

Per 60 s window:
  A. fault classifier (XGBoost, Random Forest fallback)  -> class probabilities
  B. anomaly detector (Isolation Forest, normal data only) -> unusual behaviour
  C. health regressors, one per component                  -> 0..100
  cross-check (abstract Fig. 3): a fault is named only when related signals
  agree; one abnormal signal on its own => "Sensor fault suspected".
Per unit (many windows):
  D. RUL range per component, failure probability within 30 days, and the
  dynamic repair quote (pricing.py).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as F
from . import pricing, rul, schema

DEFAULT_MODEL_DIR = Path(os.environ.get("AQUASENSE_MODELS", Path(__file__).resolve().parent.parent / "models"))

MODEL_FILES = {
    "classifier": "fault_classifier.joblib",
    "anomaly": "anomaly_detector.joblib",
    "health": "health_regressors.joblib",
    "rul": "rul_forecaster.joblib",
}

# Soft thresholds for the cross-check, as ratios to the healthy baseline.
P_LOW, P_HIGH, Q_LOW, I_DEV, CLEARLY_NORMAL = 0.90, 1.07, 0.88, 0.07, 0.05
BUILD_SLOW_S, REJ_DROP_PTS = 2.5, 4.0


class ModelsMissing(RuntimeError):
    pass


class Engine:
    def __init__(self, model_dir: str | Path | None = None):
        import joblib

        self.model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
        missing = [f for f in MODEL_FILES.values() if not (self.model_dir / f).exists()]
        if missing:
            raise ModelsMissing(
                f"Model files missing in {self.model_dir}: {', '.join(missing)}. Train them with: python -m ml.train"
            )
        clf = joblib.load(self.model_dir / MODEL_FILES["classifier"])
        self.clf, self.clf_kind = clf["model"], clf["kind"]
        self.feature_names = clf["feature_names"]
        an = joblib.load(self.model_dir / MODEL_FILES["anomaly"])
        self.iso, self.iso_threshold, self.baseline = an["model"], an["threshold"], an["baseline"]
        self.health_models = joblib.load(self.model_dir / MODEL_FILES["health"])
        self.rul_models = joblib.load(self.model_dir / MODEL_FILES["rul"])
        card = self.model_dir / "model_card.json"
        self.card = json.loads(card.read_text()) if card.exists() else {}

    # ------------------------------------------------------------ helpers
    def _x(self, feats: dict) -> np.ndarray:
        return np.array([[feats.get(n, np.nan) for n in self.feature_names]], dtype=float)

    def _ratio(self, feats: dict, name: str) -> float:
        base = self.baseline[name][0]
        return float(feats[name]) / base if base else 1.0

    def _z(self, feats: dict, name: str) -> float:
        mean, std = self.baseline[name]
        v = feats.get(name)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 0.0
        return float((v - mean) / (std if std > 1e-9 else 1e-9))

    def _probs(self, X: np.ndarray) -> np.ndarray:
        Xm = X if self.clf_kind == "xgboost" else F.fill_missing(X)
        return self.clf.predict_proba(Xm)[0]

    def _shap_top(self, X: np.ndarray, cls: int, k: int = 3) -> list[dict]:
        """Top signals behind the decision. XGBoost's pred_contribs gives exact
        TreeSHAP values without the shap package (light enough for a Pi)."""
        try:
            if self.clf_kind == "xgboost":
                import xgboost as xgb

                contrib = self.clf.get_booster().predict(xgb.DMatrix(X, feature_names=self.feature_names), pred_contribs=True)
                vals = np.asarray(contrib)[0, cls, :-1] if np.asarray(contrib).ndim == 3 else np.asarray(contrib)[0, :-1]
            else:  # Random Forest fallback: importance x deviation from normal
                imp = self.clf.feature_importances_
                z = np.array([abs(self._z(dict(zip(self.feature_names, X[0])), n)) for n in self.feature_names])
                vals = imp * np.minimum(z, 10)
        except Exception:
            return []
        order = np.argsort(-vals)[:k]
        return [{"feature": self.feature_names[i], "label": F.pretty_feature(self.feature_names[i]),
                 "value": round(float(X[0, i]), 3), "impact": round(float(vals[i]), 3)} for i in order if vals[i] > 0]

    # ------------------------------------------------------------ cross-check
    def indicators(self, feats: dict) -> dict:
        b = self.baseline
        r_p, r_q, r_i = self._ratio(feats, "pressure_bar_mean"), self._ratio(feats, "flow_lpm_mean"), self._ratio(feats, "pump_current_a_mean")
        i_std_high = feats["pump_current_a_std"] > b["pump_current_a_std"][0] + 4 * b["pump_current_a_std"][1]
        build_slow = feats["buildup_s"] > b["buildup_s"][0] + max(3 * b["buildup_s"][1], BUILD_SLOW_S)
        rej_drop = b["rejection_pct_mean"][0] - feats["rejection_pct_mean"]
        return {
            "pressure_ratio": r_p, "flow_ratio": r_q, "current_ratio": r_i,
            "pressure_low": r_p < P_LOW, "pressure_high": r_p > P_HIGH, "flow_low": r_q < Q_LOW,
            "current_abnormal": abs(r_i - 1) > I_DEV or bool(i_std_high), "buildup_slow": bool(build_slow),
            "rejection_low": rej_drop > max(4 * b["rejection_pct_mean"][1], REJ_DROP_PTS),
            "rejection_drop_pts": rej_drop,
            "pressure_normal": abs(r_p - 1) < CLEARLY_NORMAL, "flow_normal": abs(r_q - 1) < CLEARLY_NORMAL * 1.5,
        }

    @staticmethod
    def implausible(feats: dict) -> list[str]:
        """Physical impossibilities that point at a sensor, not a component."""
        why = []
        if feats["tds_in_mean"] > 100 and feats["tds_out_mean"] < 3:
            why.append("TDS-out reads about 0 ppm while TDS in is normal (probe disconnected?)")
        if feats["tds_out_mean"] > feats["tds_in_mean"] * 1.05:
            why.append("TDS out is higher than TDS in")
        for sig, name in (("pressure_bar", "Pressure"), ("tds_out", "TDS-out"), ("tds_in", "TDS-in"),
                          ("flow_lpm", "Flow"), ("pump_current_a", "Pump current")):
            if feats[f"{sig}_std"] < 1e-6 and feats[f"{sig}_mean"] > 0.05:
                why.append(f"{name} reading is frozen at {feats[f'{sig}_mean']:.2f}")
        p_off, q_off = feats.get("p_off_mean"), feats.get("flow_off_mean")
        # a working sensor decays towards 0 within seconds of the pump stopping
        if p_off is not None and not np.isnan(p_off) and p_off > max(1.5, 0.7 * feats["pressure_bar_mean"]):
            why.append(f"Pressure reads {p_off:.1f} bar while the pump is off")
        if q_off is not None and not np.isnan(q_off) and q_off > 0.1:
            why.append(f"Flow reads {q_off:.2f} L/min while the pump is off")
        if feats["pressure_bar_mean"] > 10.5 or feats["tds_in_mean"] > 3000 or not (0 < feats["temp_c_mean"] < 60):
            why.append("A reading is outside the sensor's physical range")
        return why

    def _single_signal(self, feats: dict) -> str | None:
        """Name of the only key signal that is far from normal, if exactly one is.
        Rejection counts only when it falls (a better-than-usual membrane is
        not a fault)."""
        keys = ["pressure_bar_mean", "flow_lpm_mean", "pump_current_a_mean", "rejection_pct_mean", "buildup_s"]
        z = {k: abs(self._z(feats, k)) for k in keys}
        z["rejection_pct_mean"] = max(0.0, -self._z(feats, "rejection_pct_mean"))
        big = [k for k, v in z.items() if v > 3.5]
        if len(big) == 1 and all(v < 2.0 for k, v in z.items() if k not in big):
            return big[0]
        return None

    def _evidence(self, ind: dict) -> list[str]:
        ev = []
        def pct(r):
            return f"{abs(r - 1) * 100:.0f}%"
        if ind["pressure_low"]:
            ev.append(f"Pressure {pct(ind['pressure_ratio'])} below normal")
        if ind["pressure_high"]:
            ev.append(f"Pressure {pct(ind['pressure_ratio'])} above normal")
        if ind["flow_low"]:
            ev.append(f"Reject flow {pct(ind['flow_ratio'])} below normal")
        if ind["current_abnormal"]:
            d = "above" if ind["current_ratio"] > 1 else "below"
            ev.append(f"Pump current {pct(ind['current_ratio'])} {d} normal")
        if ind["buildup_slow"]:
            ev.append("Pressure build-up after pump start is slow")
        if ind["rejection_low"]:
            ev.append(f"Salt rejection down {ind['rejection_drop_pts']:.0f} points")
        return ev

    def diagnose_window(self, feats: dict) -> dict:
        X = self._x(feats)
        probs = self._probs(X)
        pred = int(np.argmax(probs))
        iso_score = float(self.iso.decision_function(F.fill_missing(X))[0])
        anomaly = iso_score < self.iso_threshold
        ind = self.indicators(feats)
        evidence = self._evidence(ind)
        sensor_reasons = self.implausible(feats)
        final, conf, method, reason = pred, float(probs[pred]), "model", ""

        def demote(exclude: int):
            p = probs.copy()
            p[exclude] = 0
            p = p / p.sum() if p.sum() > 0 else p
            c = int(np.argmax(p))
            return c, float(p[c])

        if sensor_reasons:
            final, conf, method = schema.SENSOR, max(float(probs[schema.SENSOR]), 0.85), "cross-check"
            reason = sensor_reasons[0] + ". Other signals look normal, so no component is blamed."
        elif pred == schema.PUMP:
            agree = [ind["current_abnormal"], ind["buildup_slow"]]
            if ind["pressure_low"] and all(agree):
                reason = "Low pressure, abnormal pump current and slow build-up all agree."
            elif ind["pressure_low"] and any(agree):
                conf *= 0.85
                reason = "Low pressure with " + ("abnormal pump current" if agree[0] else "slow build-up") + "."
            elif ind["pressure_low"]:
                final, conf, method = schema.SENSOR, 0.7, "cross-check"
                reason = ("Pressure is low but pump current and build-up time are normal, so the pump is not "
                          "blamed. Pressure sensor suspected.")
            else:
                final, conf = demote(schema.PUMP)
                method = "cross-check"
                reason = "Pump pattern without low pressure; pump not blamed."
        elif pred == schema.CLOGGING:
            if ind["flow_low"] and ind["pressure_high"]:
                reason = "Low flow with high pressure: the line or filter is restricted."
            elif (ind["flow_low"] and ind["pressure_normal"]) or (ind["pressure_high"] and ind["flow_normal"]):
                final, conf, method = schema.SENSOR, 0.7, "cross-check"
                which = "Flow" if ind["flow_low"] else "Pressure"
                reason = f"{which} is abnormal but the related signal is normal. {which} sensor suspected."
            elif not (ind["flow_low"] or ind["pressure_high"]):
                final, conf = demote(schema.CLOGGING)
                method = "cross-check"
                reason = "Clog pattern without low flow or high pressure; filter not blamed."
            else:
                conf *= 0.85
                reason = "Flow and pressure partly agree with a restriction."
        elif pred == schema.MEMBRANE:
            if ind["rejection_low"]:
                reason = f"Salt rejection has fallen {ind['rejection_drop_pts']:.0f} points with plausible TDS readings."
            else:
                final, conf = demote(schema.MEMBRANE)
                method = "cross-check"
                reason = "Rejection is still healthy; membrane not blamed."
        elif pred == schema.SENSOR or (pred == schema.NORMAL and anomaly):
            # No physical impossibility was found. A sensor fault is raised only
            # when exactly ONE key signal is far from normal and the others are
            # normal (abstract Fig. 3); otherwise the model alone is not trusted.
            single = self._single_signal(feats) if anomaly else None
            if single:
                final, conf, method = schema.SENSOR, max(0.6, float(probs[schema.SENSOR])), "anomaly"
                reason = f"Only {F.pretty_feature(single).lower()} is unusual while related signals are normal; sensor fault suspected."
            elif pred == schema.SENSOR:
                final, conf = demote(schema.SENSOR)
                method = "cross-check"
                reason = "Readings are plausible and related signals agree; no sensor fault raised."
            else:
                reason = "Unusual pattern that does not match a known fault. Monitor."
        if not reason:
            reason = "All signals agree. No fault suspected." if final == schema.NORMAL else "Model decision."

        # ---- health per component
        health = {}
        Xh = F.fill_missing(X)
        for comp in ("pump", "membrane", "filter"):
            h = float(np.clip(self.health_models[comp].predict(Xh)[0], 0, 100))
            if final == schema.SENSOR:
                h = max(h, 80.0)  # do not blame a component for a sensor problem
            health[comp] = round(h, 1)
        p_sensor = 1.0 if final == schema.SENSOR else float(probs[schema.SENSOR])
        health["sensors"] = round(float(np.clip(100 - 70 * p_sensor, 0, 100)), 1)

        return {
            "predicted_class": final,
            "fault": schema.CLASS_KEYS[final],
            "fault_name": schema.CLASS_NAMES[final] + (" suspected" if final == schema.SENSOR else ""),
            "confidence": round(float(conf), 3),
            "model_class": pred,
            "probabilities": {schema.CLASS_KEYS[i]: round(float(p), 3) for i, p in enumerate(probs)},
            "method": method,
            "reason": reason,
            "evidence": evidence if final != schema.SENSOR else sensor_reasons or evidence,
            "anomaly": bool(anomaly),
            "anomaly_score": round(iso_score, 4),
            "health": health,
            "explanation": self._shap_top(X, final) if final != schema.NORMAL else self._shap_top(X, pred),
        }

    # ------------------------------------------------------------ per unit
    def diagnose_rows(self, rows) -> list[dict]:
        """Diagnose every 60 s window (30 s step) in a set of rows."""
        df = F.to_frame(rows)
        out = []
        for end, feats, _w in F.sliding_windows(df, step_s=30):
            r = self.diagnose_window(feats)
            r["window_end"] = end.isoformat()
            r["features"] = {k: (None if isinstance(v, float) and np.isnan(v) else round(float(v), 4)) for k, v in feats.items()}
            out.append(r)
        return out

    def summarise(self, windows: list[dict], df: pd.DataFrame | None = None, lookback: int = 6,
                  usage_h: float = rul.DEFAULT_USAGE_H, slopes: dict | None = None,
                  health_override: dict | None = None) -> dict:
        """Combine the latest windows into one assessment with RUL and a quote."""
        if not windows:
            return {"status": "no_data", "message": "Not enough pump-on data for a 60 s window yet."}
        recent = windows[-lookback:]
        votes = Counter()
        for w in recent:
            votes[w["predicted_class"]] += w["confidence"]
        final = votes.most_common(1)[0][0]
        same = [w for w in recent if w["predicted_class"] == final]
        conf = float(np.mean([w["confidence"] for w in same])) * len(same) / len(recent)
        latest = same[-1]
        health = {c: float(np.median([w["health"][c] for w in recent])) for c in schema.COMPONENTS}
        if health_override:
            health.update(health_override)
        tds_in = float(np.median([w["features"]["tds_in_mean"] for w in recent if w.get("features")] or [300.0]))

        comps = []
        for c in schema.COMPONENTS:
            h = health[c]
            slope = (slopes or {}).get(c)
            r = rul.predict_rul(self.rul_models, c, h, tds_in, usage_h, slope) if c != "sensors" else None
            p_rul = rul.prob_fail_within(r) if r else 0.0
            p_fault = 0.0
            if schema.CLASS_TO_COMPONENT.get(final) == c:
                severity = 1.0 if c == "sensors" else float(np.clip((100 - h) / 45, 0, 1))
                p_fault = conf * severity
            elif c == "sensors":
                p_fault = float(np.mean([w["probabilities"]["sensor"] for w in recent])) * 0.5
            fp = max(p_rul, p_fault)
            comps.append({
                "component": c, "name": schema.COMPONENT_NAMES[c], "health": round(h, 1),
                "status": schema.health_status(h), "rul": r, "failure_prob": round(fp, 3),
            })
        q = pricing.quote(comps)
        decisions = {l["component"]: l["decision"] for l in q["lines"]}
        for comp in comps:
            comp["decision"] = decisions.get(comp["component"], "ok")
        status = schema.STATUS_OK
        if final != schema.NORMAL or any(c["status"] == schema.STATUS_WARN for c in comps) or latest["anomaly"]:
            status = schema.STATUS_WARN
        if q["replace_now"]:
            status = schema.STATUS_REPLACE
        out = {
            "status": status,
            "fault": schema.CLASS_KEYS[final],
            "fault_name": latest["fault_name"],
            "confidence": round(conf, 3),
            "reason": latest["reason"],
            "evidence": latest["evidence"],
            "explanation": latest["explanation"],
            "anomaly": latest["anomaly"],
            "components": comps,
            "quote": q,
            "windows_used": len(recent),
            "window_end": recent[-1]["window_end"],
            "tds_in": round(tds_in, 1),
            "rejection_pct": round(float(np.median([w["features"]["rejection_pct_mean"] for w in recent])), 1),
            "rul_note": "RUL ranges come from a model trained on SIMULATED degradation calibrated from the rig.",
        }
        if df is not None and not df.empty:
            last = df.iloc[-1]
            out["counters"] = {
                "pump_runtime_h": round(float(last["pump_runtime_s"]) / 3600, 2),
                "pump_cycles": int(last["pump_cycles"]),
                "uv_hours": round(float(last["uv_hours"]), 2),
                "filter_hours": round(float(last["filter_hours"]), 2),
            }
            out["data_span"] = {"first": df["timestamp"].iloc[0].isoformat(), "last": df["timestamp"].iloc[-1].isoformat(),
                                "rows": int(len(df))}
        return out

    def assess(self, rows, lookback: int = 6, usage_h: float = rul.DEFAULT_USAGE_H, slopes: dict | None = None) -> dict:
        df = F.to_frame(rows)
        windows = self.diagnose_rows(df)
        return self.summarise(windows, df, lookback=lookback, usage_h=usage_h, slopes=slopes)


_ENGINE: Engine | None = None


def get_engine(model_dir: str | Path | None = None) -> Engine:
    global _ENGINE
    if _ENGINE is None or (model_dir and Path(model_dir) != _ENGINE.model_dir):
        _ENGINE = Engine(model_dir)
    return _ENGINE
