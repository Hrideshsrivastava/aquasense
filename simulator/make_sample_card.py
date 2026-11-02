"""Build the prepared backup SD card for the demo (build plan 10.1 / 13).

Writes data/sample_sdcard/ in the exact firmware layout: yesterday's file
(normal running) and today's file (normal, then the hotspot goes off and the
pump is weakened with the PWM knob: Scenario 2). sync.txt shows that the last
lines never reached the cloud.

    python -m simulator.make_sample_card [--fault pump --level 2] [--out DIR]
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from aquasense.rigsim import Counters, FaultState, RigCalibration, VirtualRig
from simulator.virtual_rig import SDCard

ROOT = Path(__file__).resolve().parent.parent
FAULTS = {"normal": 0, "clog": 1, "pump": 2, "membrane": 3, "sensor": 4}


def make(out: Path, fault: str = "pump", level: int = 2, tds: float = 800, end: datetime | None = None,
         device: str = "AQ-RIG-01", seed: int = 21) -> Path:
    if out.exists():
        shutil.rmtree(out)
    card = SDCard(out)
    end = (end or datetime(2026, 11, 3, 15, 30, 0))
    rng = np.random.default_rng(seed)
    rig = VirtualRig(device_id=device, start=end - timedelta(days=1, minutes=60), faults=FaultState(tds_source=tds),
                     calib=RigCalibration.random(rng), seed=seed,
                     counters=Counters(pump_runtime_s=1_250_000, pump_cycles=14_200, uv_hours=347.0, filter_hours=212.0))
    for r in rig.run(60 * 60):  # yesterday: one healthy hour
        card.append(r)
    rig.t = end - timedelta(minutes=40)
    for r in rig.run(25 * 60):  # today: healthy, synced to the cloud
        card.append(r)
    while True:  # everything so far reached the cloud
        lines, name, pos = card.unsent(max_lines=10**6)
        if not lines:
            break
        card.save_sync(name, pos)
    rig.faults = FaultState.from_class(FAULTS[fault], level, tds, "tds_out_unplugged")
    for r in rig.run(15 * 60):  # hotspot off, fault injected: only on the SD card
        card.append(r)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "sample_sdcard"))
    ap.add_argument("--fault", choices=list(FAULTS), default="pump")
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--tds", type=float, default=800)
    a = ap.parse_args()
    p = make(Path(a.out), a.fault, a.level, a.tds)
    print("Sample SD card written to", p, "->", sorted(x.name for x in p.iterdir()))
