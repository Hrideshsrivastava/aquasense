"""Seed the console with SIMULATED fleet units (build plan 6: "one real unit
and a few simulated ones to show fleet analytics").

Each simulated unit gets:
  * 60 days of daily component health from the degradation simulator
  * the last 15 minutes of sensor readings from the rig model at matching
    severities, scored through the normal cloud pipeline

    python -m simulator.seed_fleet            # writes straight into data/aquasense.db
    python -m simulator.seed_fleet --reset    # wipe the database first
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import numpy as np

from aquasense import degradation, schema
from aquasense.rigsim import Counters, FaultState, RigCalibration, VirtualRig
from cloud import db, service

# id, name, customer, phone, address, city, source, tds, usage h/day, target health, sensor fault
FLEET = [
    ("H-1042", "Kitchen RO", "Asha Verma", "+91 98100 11042", "B-12 Shanti Nagar", "Jaipur", "borewell", 1400, 3.0,
     {"pump": 48, "membrane": 82, "filter": 87}, None),
    ("H-2210", "Flat 4B RO", "Ravi Menon", "+91 98200 22210", "4B Sea View Apts", "Chennai", "tanker", 1100, 2.5,
     {"pump": 90, "membrane": 43, "filter": 86}, None),
    ("H-0871", "Villa RO", "Meera Iyer", "+91 98300 10871", "17 Palm Grove", "Bengaluru", "borewell", 900, 2.0,
     {"pump": 92, "membrane": 83, "filter": 38}, None),
    ("H-3305", "Office pantry RO", "Kabir Shah", "+91 98400 33305", "3rd floor, Orbit Tower", "Pune", "municipal", 320, 4.0,
     {"pump": 90, "membrane": 88, "filter": 80}, "tds_out_unplugged"),
    ("H-1180", "Home RO", "Nupur Gupta", "+91 98500 11180", "22 Civil Lines", "Lucknow", "municipal", 280, 1.5,
     {"pump": 96, "membrane": 92, "filter": 89}, None),
    ("H-2731", "Clinic RO", "Dr. Arjun Rao", "+91 98600 22731", "Rao Clinic, MG Road", "Hyderabad", "tanker", 750, 3.5,
     {"pump": 81, "membrane": 79, "filter": 85}, None),
]


def history_to(target: float, comp: str, tds: float, usage: float, rng, days: int = 60) -> np.ndarray:
    """A simulated life that passes `target` health; return the `days` leading up to it."""
    for _ in range(200):
        life = degradation.simulate_life(comp, tds, usage, rng)
        idx = int(np.argmin(np.abs(life - target)))
        if abs(life[idx] - target) < 2.5 and idx >= days:
            return life[idx - days + 1: idx + 1]
    hist = np.linspace(min(100, target + days * degradation.wear_rate(comp, tds, usage)), target, days)
    return hist + rng.normal(0, 0.6, days)


def seed(reset: bool = False, minutes: float = 15, seed_: int = 3):
    db.init()
    if reset:
        db.reset()
    rng = np.random.default_rng(seed_)
    today = date.today()
    for (uid, name, cust, phone, addr, city, src, tds, usage, target, sensor) in FLEET:
        service.upsert_unit({"id": uid, "name": name, "customer": cust, "phone": phone, "address": addr, "city": city,
                             "water_source": src, "usage_h": usage, "simulated": 1,
                             "installed": (today - timedelta(days=int(rng.integers(120, 700)))).isoformat()})
        with db.tx() as c:
            for t in ("readings", "assessments", "daily_health", "alert_state", "alerts", "tickets"):
                c.execute(f"DELETE FROM {t} WHERE unit_id=?", (uid,))
            for comp, h in target.items():
                hist = history_to(h, comp, tds, usage, rng)
                for i, v in enumerate(hist):
                    day = (today - timedelta(days=len(hist) - 1 - i)).isoformat()
                    c.execute("INSERT OR REPLACE INTO daily_health(unit_id, day, component, health, n, simulated) "
                              "VALUES(?,?,?,?,1,1)", (uid, day, comp, float(v)))
        # severity from health: health = 100 - 70 * s (schema.LEVEL_HEALTH). Wear above
        # 85 % health is too early to show in the signals, so it is not injected.
        sev = {k: (float(np.clip((100 - v) / 70, 0, 1)) if v < 85 else 0.0) for k, v in target.items()}
        faults = FaultState(clog=sev["filter"], pump=sev["pump"], membrane=sev["membrane"],
                            sensor_mode=sensor, tds_source=float(tds))
        start = datetime.now().replace(microsecond=0) - timedelta(minutes=minutes)
        rig = VirtualRig(device_id=uid, start=start - timedelta(seconds=150), faults=faults,
                         calib=RigCalibration(), seed=int(rng.integers(1e6)),
                         counters=Counters(pump_runtime_s=usage * 3600 * 300, pump_cycles=int(usage * 300 * 30),
                                           uv_hours=usage * 300, filter_hours=usage * 120))
        rig.run(150)
        rows = rig.run(int(minutes * 60))
        res = service.ingest(rows)
        print(f"{uid}: {len(rows)} rows, {res['assessed']} windows scored, alerts: {[a['fault_name'] for a in res['alerts']]}")
    print("Seeded", len(FLEET), "SIMULATED units into", db.DB_PATH)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="delete all data first (including the real rig's)")
    ap.add_argument("--minutes", type=float, default=15)
    a = ap.parse_args()
    seed(a.reset, a.minutes)
