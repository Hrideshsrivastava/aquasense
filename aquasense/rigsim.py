"""Software model of the Unit A RO test rig.

Used for three things:
  1. ml/generate_rig_data.py: synthetic labelled runs that follow the data
     collection protocol (build plan 8.1) until real rig recordings exist.
  2. simulator/virtual_rig.py: a stand-in ESP32 for demos without hardware.
  3. simulator/seed_fleet.py: the simulated fleet units on the console.

The physics is deliberately simple but follows the fault signatures in build
plan 4.4 and the abstract's Fig. 3:

  clogging (CLOG valve)   flow falls, pressure rises
  pump weakness (PWM)     pressure low, current abnormal, build-up slow
  membrane wear (BYPASS)  TDS out rises, rejection falls
  sensor fault            one signal is abnormal, the others stay normal
  high-TDS source         TDS in rises; rejection stays healthy (NOT a fault)

Severity is continuous in [0, 1]; the fault-panel levels 1/2/3 map to 1/3,
2/3 and 1. Output rows use exactly the SD-card CSV format (schema.CSV_HEADER).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from . import schema

SENSOR_MODES = ("tds_out_unplugged", "pressure_frozen", "tds_out_frozen")


@dataclass
class FaultState:
    """What the fault-injection panel is set to."""

    clog: float = 0.0  # CLOG ball valve, 0..1
    pump: float = 0.0  # PWM speed controller turned down, 0..1
    membrane: float = 0.0  # BYPASS needle valve, 0..1
    sensor_mode: str | None = None  # one of SENSOR_MODES
    tds_source: float = 300.0  # salt in the bucket (ppm)

    @staticmethod
    def from_class(cls: int, level: int, tds_source: float = 300.0, sensor_mode: str | None = None) -> "FaultState":
        s = max(0, min(3, level)) / 3.0
        f = FaultState(tds_source=tds_source)
        if cls == schema.CLOGGING:
            f.clog = s
        elif cls == schema.PUMP:
            f.pump = s
        elif cls == schema.MEMBRANE:
            f.membrane = s
        elif cls == schema.SENSOR:
            f.sensor_mode = sensor_mode or "tds_out_unplugged"
        return f

    def dominant_class(self) -> int:
        if self.sensor_mode:
            return schema.SENSOR
        sev = {schema.CLOGGING: self.clog, schema.PUMP: self.pump, schema.MEMBRANE: self.membrane}
        cls, s = max(sev.items(), key=lambda kv: kv[1])
        return cls if s >= 0.15 else schema.NORMAL


@dataclass
class RigCalibration:
    """Per-run variation: re-priming the rig, sensor drift, a slightly
    different pump. This is what makes 'split by run' matter."""

    p0: float = 5.5  # healthy steady pressure, bar
    q0: float = 0.60  # healthy reject flow, L/min
    i0: float = 0.95  # healthy pump current, A
    rej0: float = 0.955  # healthy salt rejection
    tau0: float = 1.1  # pressure build-up time constant, s
    tds_bias_in: float = 1.0
    tds_bias_out: float = 1.0
    temp0: float = 27.0

    @staticmethod
    def random(rng: np.random.Generator) -> "RigCalibration":
        return RigCalibration(
            p0=5.5 * rng.normal(1, 0.03),
            q0=0.60 * rng.normal(1, 0.04),
            i0=0.95 * rng.normal(1, 0.03),
            rej0=float(np.clip(rng.normal(0.955, 0.008), 0.93, 0.975)),
            tau0=1.1 * rng.normal(1, 0.1),
            tds_bias_in=rng.normal(1, 0.03),
            tds_bias_out=rng.normal(1, 0.04),
            temp0=rng.uniform(22, 32),
        )


@dataclass
class Counters:
    pump_runtime_s: float = 0.0
    pump_cycles: int = 0
    uv_hours: float = 0.0
    filter_hours: float = 0.0


class VirtualRig:
    """Second-by-second rig model with the firmware's logging behaviour."""

    def __init__(
        self,
        device_id: str = "AQ-RIG-01",
        start: datetime | None = None,
        faults: FaultState | None = None,
        calib: RigCalibration | None = None,
        counters: Counters | None = None,
        training_label: int | None = None,
        seed: int | None = None,
        cycle_offset_s: int = 0,
    ):
        self.device_id = device_id
        self.t = start or datetime.now().replace(microsecond=0)
        self.faults = faults or FaultState()
        self.rng = np.random.default_rng(seed)
        self.calib = calib or RigCalibration()
        self.counters = counters or Counters()
        self.training_label = training_label  # None => normal mode (5 s, empty label)
        self.cycle_t = cycle_offset_s % (schema.PUMP_ON_S + schema.PUMP_OFF_S)
        self.pressure = 0.2
        self.buildup_s = float(schema.PUMP_ON_S)  # last measured build-up time
        self._since_start = 0
        self._acc: list[dict] = []
        self._acc_state: int | None = None
        self._since_log = 0
        self._frozen: dict[str, float] = {}
        self._drift = 0.0

    # ------------------------------------------------------------ physics
    @property
    def log_interval(self) -> int:
        return schema.LOG_INTERVAL_TRAINING_S if self.training_label is not None else schema.LOG_INTERVAL_NORMAL_S

    def _steady(self) -> tuple[float, float, float, float, float]:
        """Steady-state (pressure, flow, current, rejection, tau) for the current faults."""
        c, f = self.calib, self.faults
        p = c.p0 * (1 + 0.45 * f.clog) * (1 - 0.55 * f.pump) * (1 - 0.06 * f.membrane)
        q = c.q0 * (1 - 0.70 * f.clog) * (1 - 0.45 * f.pump) * (1 - 0.10 * f.membrane)
        i = c.i0 * (1 + 0.15 * f.clog) * (1 - 0.35 * f.pump)
        # rejection improves a little with pressure, falls with membrane bypass
        rej = 1 - (1 - c.rej0) * math.sqrt(c.p0 / max(p, 0.5))
        rej -= 0.45 * f.membrane
        tau = c.tau0 * (1 + 5.0 * f.pump) * (1 - 0.2 * f.clog)
        return p, q, i, max(0.0, min(0.995, rej)), tau

    def _second(self) -> dict:
        """Advance one second and return the 1 Hz internal reading."""
        rng, c, f = self.rng, self.calib, self.faults
        on = self.cycle_t < schema.PUMP_ON_S
        p_ss, q_ss, i_ss, rej, tau = self._steady()
        self._drift += rng.normal(0, 0.002)
        self._drift = max(-0.05, min(0.05, self._drift))

        if on:
            if self.cycle_t == 0:  # pump start
                self.counters.pump_cycles += 1
                self._since_start = 0
                self.buildup_s = self._measure_buildup(p_ss, tau)
            self._since_start += 1
            ramp = 1 - math.exp(-self._since_start / tau)
            self.pressure = p_ss * ramp
            ripple = 1 + 2.5 * f.pump
            pressure = self.pressure * (1 + self._drift) + rng.normal(0, 0.07 * ripple)
            flow = q_ss * min(1.0, ramp * 1.05) * rng.normal(1, 0.025) + rng.normal(0, 0.008)
            inrush = 1 + 0.6 * math.exp(-self._since_start / 1.5)
            current = i_ss * inrush * (1 + rng.normal(0, 0.025 * ripple))
            self.counters.pump_runtime_s += 1
            self.counters.uv_hours += 1 / 3600
            self.counters.filter_hours += 1 / 3600
        else:
            self.pressure = max(0.15, self.pressure * 0.6)
            pressure = self.pressure + rng.normal(0, 0.03)
            flow = max(0.0, rng.normal(0, 0.003))
            current = abs(rng.normal(0.01, 0.005))

        temp = c.temp0 + 0.4 * math.sin(self._since_start / 600) + rng.normal(0, 0.08)
        tds_in = f.tds_source * c.tds_bias_in * rng.normal(1, 0.015)
        tds_out = f.tds_source * (1 - rej) * c.tds_bias_out * rng.normal(1, 0.03)
        if not on:
            tds_out *= 1.08  # TDS creep while the permeate line is stagnant

        # sensor faults: one signal goes wrong, the physics stays the same
        if f.sensor_mode == "tds_out_unplugged":
            tds_out = abs(rng.normal(0.6, 0.6))
        elif f.sensor_mode == "tds_out_frozen":
            tds_out = self._frozen.setdefault("tds_out", tds_out)
        elif f.sensor_mode == "pressure_frozen":
            pressure = self._frozen.setdefault("pressure", max(pressure, 3.0))
        if f.sensor_mode != "tds_out_frozen":
            self._frozen.pop("tds_out", None)
        if f.sensor_mode != "pressure_frozen":
            self._frozen.pop("pressure", None)

        self.cycle_t = (self.cycle_t + 1) % (schema.PUMP_ON_S + schema.PUMP_OFF_S)
        self.t += timedelta(seconds=1)
        return {
            "on": int(on),
            "tds_in": max(0.0, tds_in),
            "tds_out": max(0.0, tds_out),
            "temp_c": temp,
            "pressure_bar": max(0.0, pressure),
            "flow_lpm": max(0.0, flow),
            "pump_current_a": max(0.0, current),
        }

    def _measure_buildup(self, p_ss: float, tau: float) -> float:
        """Firmware: time from pump start until pressure reaches 80 % of the
        healthy baseline, sampled at 10 Hz; capped at the ON period."""
        if self.faults.sensor_mode == "pressure_frozen":
            return 0.1  # a frozen sensor 'reaches' target instantly
        target = 0.8 * self.calib.p0
        if p_ss <= target * 1.01:
            return float(schema.PUMP_ON_S)
        t = -tau * math.log(1 - target / p_ss)
        t = round(t * 10) / 10 + self.rng.normal(0, 0.1)
        return float(max(0.1, min(schema.PUMP_ON_S, t)))

    # ------------------------------------------------------------ logging
    def _flush(self) -> dict | None:
        if not self._acc:
            return None
        a = self._acc
        row = {
            "timestamp": self.t.strftime("%Y-%m-%dT%H:%M:%S"),
            "device_id": self.device_id,
        }
        for k in ("tds_in", "tds_out", "temp_c", "pressure_bar", "flow_lpm", "pump_current_a"):
            row[k] = float(np.mean([s[k] for s in a]))
        row["pump_on"] = a[-1]["on"]
        row["buildup_s"] = self.buildup_s
        row["pump_runtime_s"] = int(self.counters.pump_runtime_s)
        row["pump_cycles"] = self.counters.pump_cycles
        row["uv_hours"] = self.counters.uv_hours
        row["filter_hours"] = self.counters.filter_hours
        row["label"] = "" if self.training_label is None else int(self.training_label)
        self._acc = []
        self._since_log = 0
        return format_row(row)

    def step(self) -> dict | None:
        """Advance one second. Returns a CSV row dict when a log line is written."""
        s = self._second()
        out = None
        # A pump state change flushes the partial line so a log line never
        # mixes ON and OFF samples (the firmware does the same).
        if self._acc_state is not None and s["on"] != self._acc_state:
            out = self._flush()
        self._acc.append(s)
        self._acc_state = s["on"]
        self._since_log += 1
        if self._since_log >= self.log_interval:
            flushed = self._flush()
            out = flushed if out is None else out  # at most one line per second
        return out

    def run(self, seconds: int) -> list[dict]:
        rows = []
        for _ in range(int(seconds)):
            r = self.step()
            if r is not None:
                rows.append(r)
        return rows


def format_row(row: dict) -> dict:
    """Round values the way the firmware prints them."""
    out = dict(row)
    for k, nd in (("tds_in", 1), ("tds_out", 1), ("temp_c", 2), ("pressure_bar", 3), ("flow_lpm", 3),
                  ("pump_current_a", 3), ("buildup_s", 1), ("uv_hours", 3), ("filter_hours", 3)):
        if k in out and out[k] != "":
            out[k] = round(float(out[k]), nd)
    return out


@dataclass
class RunSpec:
    """One labelled recording, as in the logbook (build plan 8.1)."""

    run_id: str
    cls: int
    level: int
    tds_source: float
    minutes: float
    sensor_mode: str | None = None
    seed: int = 0
    start: datetime = field(default_factory=lambda: datetime(2026, 11, 3, 10, 0, 0))


def record_run(spec: RunSpec, device_id: str = "AQ-RIG-01") -> list[dict]:
    rng = np.random.default_rng(spec.seed)
    faults = FaultState.from_class(spec.cls, spec.level, spec.tds_source, spec.sensor_mode)
    # valve and knob positions are never exactly repeatable
    jitter = rng.normal(0, 0.05)
    for name in ("clog", "pump", "membrane"):
        v = getattr(faults, name)
        if v > 0:
            setattr(faults, name, float(np.clip(v + jitter, 0.1, 1.0)))
    rig = VirtualRig(
        device_id=device_id,
        start=spec.start,
        faults=faults,
        calib=RigCalibration.random(rng),
        counters=Counters(pump_runtime_s=rng.uniform(0, 4e5), pump_cycles=int(rng.uniform(0, 4000)),
                          uv_hours=rng.uniform(0, 150), filter_hours=rng.uniform(0, 150)),
        training_label=spec.cls,
        seed=spec.seed + 1,
        cycle_offset_s=int(rng.integers(0, 120)),
    )
    rig.run(150)  # let the rig settle before the recording starts
    rows = rig.run(int(spec.minutes * 60))
    for r in rows:
        r["run_id"] = spec.run_id
        r["level"] = spec.level
    return rows
