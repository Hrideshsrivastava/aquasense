# Data collection protocol (build plan 8.1 and 8.4)

Two people: one changes the fault, one watches the data and fills the logbook.

## Before every session

1. Rig plumbed and leak-tested; calibration done (`docs/hardware.md`).
2. Re-check TDS probes against the TDS pen (they drift).
3. RTC time correct (the OLED and Serial Monitor show it).
4. Fresh logbook page.

## Training mode

Hold the label button for 2 s: the OLED shows `TRAIN label N`. Short-press to step
through the labels: 0 normal, 1 clogging, 2 pump weakness, 3 membrane wear,
4 sensor fault. In training mode a line is written every 2 s with the label in the
last column. Hold 2 s again to leave training mode.

## What to record

| Class | Variants |
|---|---|
| 0 Normal | salt ~300 ppm (municipal), ~800 ppm, ~1500 ppm (borewell) |
| 1 Clogging | CLOG valve levels 1, 2, 3 |
| 2 Pump weakness | PWM knob levels 1, 2, 3 |
| 3 Membrane wear | BYPASS valve levels 1, 2, 3 |
| 4 Sensor fault | TDS-out probe unplugged; freeze toggle on; (optional) TDS-out frozen |

* 5 to 10 minutes per recording. Wait one full pump cycle (2 min) after changing
  a fault before you start the clock.
* Repeat every variant at least 3 times on different days or after re-priming the rig.
* Mix the salt level across fault recordings too, so the model never learns
  "salty water = fault".
* Target: 35 to 45 runs, several thousand rows.

## Logbook

One line per run in `data/rig_runs/logbook.csv` (template: `docs/logbook_template.csv`):

```
run_id,start,end,class,level,salt_ppm,sensor_mode,notes
R001,2026-11-10T10:02:00,2026-11-10T10:09:30,0,0,300,,fresh membrane
R002,2026-11-10T10:15:00,2026-11-10T10:22:00,1,2,300,,CLOG valve to mark 2
```

`start`/`end` are RTC times; the trainer cuts the SD-card rows by these times.
`level` is 0 for normal and 1 for sensor faults.

## Training and the honesty rules

```
python -m ml.train --real data/rig_runs
```

* The split is **by run**: one repeat of every variant is held out whole.
* The report (`reports/training_report.md`) has the confusion matrix and F1 per
  class on the held-out runs, with and without the cross-check logic.
* High-TDS normal water is tested explicitly (false-alarm rate must be ~0).
* Long-term RUL stays simulated (`aquasense/degradation.py`); say so in the
  presentation.
