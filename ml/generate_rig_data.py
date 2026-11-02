"""Generate a SYNTHETIC stand-in for the labelled rig recordings.

It follows the data collection protocol (build plan 8.1 and
docs/data_collection_protocol.md) exactly, so the training pipeline can be
built and tested before the rig is finished. Once you have real recordings,
put the SD-card CSVs (training mode, with labels) plus docs/logbook.csv in
data/rig_runs/ and run `python -m ml.train --real data/rig_runs`.

    python -m ml.generate_rig_data            # writes data/synthetic_rig/runs.csv + logbook.csv
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from aquasense import schema
from aquasense.rigsim import RunSpec, record_run

ROOT = Path(__file__).resolve().parent.parent
TDS_LEVELS = [300, 800, 1500]


def protocol(repeats: int = 3, seed: int = 7) -> list[RunSpec]:
    """Class / variant plan from build plan 8.1, each repeated `repeats` times
    on different 'days'. 15 variants x 3 repeats = 45 runs."""
    rng = np.random.default_rng(seed)
    variants: list[tuple[int, int, str | None, float | None]] = []
    variants += [(schema.NORMAL, 0, None, t) for t in TDS_LEVELS]
    for cls in (schema.CLOGGING, schema.PUMP, schema.MEMBRANE):
        variants += [(cls, lvl, None, None) for lvl in (1, 2, 3)]
    variants += [(schema.SENSOR, 1, m, None) for m in ("tds_out_unplugged", "tds_out_frozen", "pressure_frozen")]
    specs = []
    day0 = datetime(2026, 11, 10, 10, 0, 0)
    n = 0
    for rep in range(repeats):
        for cls, lvl, mode, tds in variants:
            n += 1
            tds_src = tds if tds is not None else float(rng.choice(TDS_LEVELS))
            specs.append(RunSpec(
                run_id=f"R{n:03d}", cls=cls, level=lvl, tds_source=tds_src * rng.normal(1, 0.04),
                minutes=float(rng.uniform(5, 10)), sensor_mode=mode, seed=int(rng.integers(1e9)),
                start=day0 + timedelta(days=rep * 2, minutes=n * 15),
            ))
    return specs


def generate(out_dir: Path, repeats: int = 3, seed: int = 7) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = protocol(repeats, seed)
    runs_path, log_path = out_dir / "runs.csv", out_dir / "logbook.csv"
    with runs_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=schema.CSV_HEADER + ["run_id", "level"])
        w.writeheader()
        for s in specs:
            for row in record_run(s):
                w.writerow(row)
    with log_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "date", "class", "class_name", "level", "salt_ppm", "sensor_mode", "minutes", "what_changed"])
        for s in specs:
            what = {schema.NORMAL: "nothing (salt level only)", schema.CLOGGING: f"CLOG valve to mark {s.level}",
                    schema.PUMP: f"PWM knob to mark {s.level}", schema.MEMBRANE: f"BYPASS valve to mark {s.level}",
                    schema.SENSOR: s.sensor_mode}[s.cls]
            w.writerow([s.run_id, s.start.date().isoformat(), s.cls, schema.CLASS_NAMES[s.cls], s.level,
                        round(s.tds_source), s.sensor_mode or "", round(s.minutes, 1), what])
    return runs_path, log_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "synthetic_rig"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    r, l = generate(Path(a.out), a.repeats, a.seed)
    print("wrote", r, "and", l)
