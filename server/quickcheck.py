"""Quick check: the light model the server runs on every device.

It does not try to be the final word. It turns the last minute of readings
into a *hypothesis* ("filter clogging, 0.82") using a per-device healthy
baseline and a handful of ratio rules, so it costs microseconds per device.
A hypothesis that holds for 3 evaluations in a row becomes an alert and a
technician job; the technician then confirms on site with the full diagnosis
on the phone (mobile/src/lib/diagnosis.ts).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field

from .protocol import VIRTUAL_BASE

WINDOW = 30            # readings (60 s at the ESP32's 2 s interval)
EVAL_EVERY = 5         # evaluate every 5 readings (10 s)
BASELINE_N = 30        # running readings needed to learn the baseline
ALERT_SCORE = 0.5
ALERT_STREAK = 3

FAULT_LABEL = {
    "normal": "Normal",
    "clog": "Filter/line clogging",
    "pump": "Pump weakness",
    "membrane": "RO membrane wear",
    "turbidity": "Pre-filter exhausted (cloudy water)",
    "sensor": "Sensor fault suspected",
    "idle": "Idle (no water flowing)",
}
FAULT_COMPONENT = {"clog": "filter", "pump": "pump", "membrane": "membrane", "turbidity": "prefilter", "sensor": "sensors"}
COMPONENTS = {"pump": "Booster pump", "membrane": "RO membrane", "filter": "Filter / line", "prefilter": "Sediment pre-filter",
              "sensors": "Sensors"}


def clip(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def rejection(r: dict) -> float:
    return 1 - r["tds_out_ppm"] / max(r["tds_in_ppm"], 1.0)


def is_running(r: dict) -> bool:
    return r["pump_current_A"] > 0.15 or r["flow_rate_Lmin"] > 0.05


@dataclass
class DeviceModel:
    """Per-device rolling state. Cheap: a deque and a few floats."""

    baseline: dict | None = None
    _base_acc: list = field(default_factory=list)
    window: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    n: int = 0
    streak_fault: str | None = None
    streak: int = 0
    last: dict | None = None

    def learn(self, r: dict, overlay_active: bool):
        if self.baseline is not None or overlay_active or not is_running(r):
            return
        self._base_acc.append(r)
        if len(self._base_acc) >= BASELINE_N:
            acc = self._base_acc
            self.baseline = {
                "flow_rate_Lmin": statistics.mean(x["flow_rate_Lmin"] for x in acc),
                "pump_current_A": statistics.mean(x["pump_current_A"] for x in acc),
                "turbidity_voltage_V": statistics.mean(x["turbidity_voltage_V"] for x in acc),
                "pressure_bar": statistics.mean(x["pressure_bar"] for x in acc),
                "rejection": statistics.mean(rejection(x) for x in acc),
                "n": len(acc),
            }
            self._base_acc = []

    def reset_baseline(self):
        self.baseline = None
        self._base_acc = []

    def add(self, r: dict, overlay_active: bool = False) -> dict | None:
        """Add one reading. Returns an evaluation every EVAL_EVERY readings."""
        self.window.append(r)
        self.learn(r, overlay_active)
        self.n += 1
        if self.n % EVAL_EVERY:
            return None
        self.last = evaluate(list(self.window), self.baseline)
        h = self.last
        if h["fault"] not in ("normal", "idle") and h["score"] >= ALERT_SCORE:
            self.streak = self.streak + 1 if self.streak_fault == h["fault"] else 1
            self.streak_fault = h["fault"]
        else:
            self.streak, self.streak_fault = 0, None
        h["streak"] = self.streak
        h["alert"] = self.streak >= ALERT_STREAK
        return h


def _ratio(v, base):
    return v / base if base and base > 1e-6 else 1.0


def evaluate(win: list[dict], baseline: dict | None) -> dict:
    """Rules on the last minute. Returns {fault, label, score, evidence, health, signals}."""
    run = [r for r in win if is_running(r)]
    ev: list[str] = []
    mean = lambda rows, k: statistics.mean(r[k] for r in rows) if rows else float("nan")  # noqa: E731
    b = baseline or {}
    tin = mean(win, "tds_in_ppm")
    tout = mean(win, "tds_out_ppm")
    rej = 1 - tout / max(tin, 1)
    base_rej = b.get("rejection", VIRTUAL_BASE["rejection"])
    rej_drop = (base_rej - rej) * 100
    turb = mean(win, "turbidity_voltage_V")
    rt = _ratio(turb, b.get("turbidity_voltage_V"))
    scores: dict[str, float] = {}

    # ---- sensor plausibility (works with or without a baseline)
    # the last few readings decide plausibility, so a dead sensor is caught at once
    tail = win[-3:]
    sensor_why = []
    if tin > 100 and max(r["tds_out_ppm"] for r in tail) < 3:
        sensor_why.append("TDS-out reads about 0 ppm while TDS in is normal")
    t_tail = [r["turbidity_voltage_V"] for r in tail]
    adc_tail = [r.get("turbidity_ADC", 0) for r in tail]
    if max(t_tail) < 0.05 or min(adc_tail) >= 4090:  # no signal, or the ADC is saturated
        sensor_why.append(f"Turbidity sensor output {t_tail[-1]:.2f} V is out of range (disconnected?)")
    if len(run) >= 5:
        if statistics.pstdev([r["pressure_bar"] for r in run]) < 1e-4:
            sensor_why.append("Pressure reading is frozen")
        cur_ok = b and abs(_ratio(mean(run, "pump_current_A"), b.get("pump_current_A")) - 1) < 0.1
        if cur_ok and mean(run, "flow_rate_Lmin") < 0.02 and mean(run, "pressure_bar") > 0.8 * VIRTUAL_BASE["pressure_bar"]:
            sensor_why.append("Flow reads 0 while pump current and pressure are normal")
    if sensor_why:
        scores["sensor"] = 0.9

    signals = {"tds_in": tin, "tds_out": tout, "rejection_pct": rej * 100, "turbidity_V": turb}
    rq = ri = rp = None
    if len(run) >= 5 and b:
        rq = _ratio(mean(run, "flow_rate_Lmin"), b["flow_rate_Lmin"])
        ri = _ratio(mean(run, "pump_current_A"), b["pump_current_A"])
        rp = _ratio(mean(run, "pressure_bar"), b["pressure_bar"])
        signals.update(flow_ratio=rq, current_ratio=ri, pressure_ratio=rp)
        if rq < 0.88 and rp > 1.07:
            scores["clog"] = clip(0.45 + ((1 - rq) / 0.7 + (rp - 1) / 0.45) / 2)
            ev += [f"Flow {100 * (1 - rq):.0f}% below normal", f"Pressure {100 * (rp - 1):.0f}% above normal"]
        if rp < 0.9 and abs(ri - 1) > 0.07:
            scores["pump"] = clip(0.45 + ((1 - rp) / 0.55 + abs(1 - ri) / 0.35) / 2)
            ev += [f"Pressure {100 * (1 - rp):.0f}% below normal", f"Pump current {100 * abs(1 - ri):.0f}% {'below' if ri < 1 else 'above'} normal"]
    if rej_drop > 4 and "sensor" not in scores:
        scores["membrane"] = clip(0.45 + rej_drop / 45)
        ev.append(f"Salt rejection down {rej_drop:.0f} points")
    # a clogged pre-filter clouds the water gradually; a signal that collapses is a sensor problem
    if b.get("turbidity_voltage_V") and rt < 0.92 and min(r["turbidity_voltage_V"] for r in win) > 0.3 * b["turbidity_voltage_V"]:
        scores["turbidity"] = clip(0.45 + (1 - rt) / 0.3)
        ev.append(f"Turbidity signal {100 * (1 - rt):.0f}% below clear-water level")

    if scores:
        fault = max(scores, key=scores.get)
        score = scores[fault]
        if fault == "sensor":
            ev = sensor_why
    elif not run:
        fault, score = "idle", 0.0
    else:
        fault, score = "normal", 0.0

    # rough component health for the dashboard / customer view (the phone's full check is the reference)
    health = {k: 100.0 for k in COMPONENTS}
    if rq is not None:
        health["filter"] = 100 - 70 * clip(((1 - rq) / 0.7 + max(0, rp - 1) / 0.45) / 2) if "clog" in scores else 100 - 15 * clip(1 - rq)
        health["pump"] = 100 - 70 * clip(((1 - rp) / 0.55 + abs(1 - ri) / 0.35) / 2) if "pump" in scores else 100 - 15 * clip(abs(1 - ri))
    health["membrane"] = 100 - 70 * clip(max(0, rej_drop) / 45)
    if b.get("turbidity_voltage_V"):
        health["prefilter"] = 100 - 70 * clip((1 - rt) / 0.35) if turb >= 0.05 else 100
    health["sensors"] = 30.0 if "sensor" in scores else 100.0
    health = {k: round(v, 1) for k, v in health.items()}

    return {"fault": fault, "label": FAULT_LABEL[fault], "score": round(score, 3), "evidence": ev,
            "health": health, "signals": {k: (None if isinstance(v, float) and math.isnan(v) else round(v, 4)) for k, v in signals.items()},
            "baseline_ready": bool(b), "running": bool(run)}
