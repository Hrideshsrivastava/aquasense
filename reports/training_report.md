# AquaSense training report

**SIMULATED DATA.** Data: SYNTHETIC rig data (aquasense/rigsim.py, protocol of build plan 8.1). Trained 2026-11-02T19:25:33.

45 runs, 9959 log rows, 548 windows of 60 s.
**Split by run:** 16 whole runs held out for testing (one repeat of every variant).

## Fault classifier (xgboost) on held-out runs

| Metric | Value |
|---|---|
| Macro F1, model only | 0.895 |
| Macro F1, model + cross-check logic | 1.000 |
| Run-level accuracy (majority of windows) | 100% |
| High-TDS normal water flagged as a fault | 0.0 |

### F1 per class (with cross-check)

| Class | F1 |
|---|---|
| Normal | 1.000 |
| Filter/line clogging | 1.000 |
| Pump weakness | 1.000 |
| Membrane wear | 1.000 |
| Sensor fault | 1.000 |

### Confusion matrix, model only (windows)

| true \ predicted | Normal | Clog | Pump | Membrane | Sensor |
|---|---|---|---|---|---|
| **Normal** | 27 | 0 | 0 | 0 | 15 |
| **Clog** | 0 | 39 | 0 | 0 | 0 |
| **Pump** | 0 | 0 | 40 | 0 | 0 |
| **Membrane** | 0 | 0 | 0 | 29 | 0 |
| **Sensor** | 2 | 0 | 2 | 0 | 27 |

### Confusion matrix, model + cross-check (windows)

| true \ predicted | Normal | Clog | Pump | Membrane | Sensor |
|---|---|---|---|---|---|
| **Normal** | 42 | 0 | 0 | 0 | 0 |
| **Clog** | 0 | 39 | 0 | 0 | 0 |
| **Pump** | 0 | 0 | 40 | 0 | 0 |
| **Membrane** | 0 | 0 | 0 | 29 | 0 |
| **Sensor** | 0 | 0 | 0 | 0 | 31 |

## Health regressors (mean absolute error, health points)

pump: 0.78, membrane: 1.53, filter: 2.64

## RUL forecaster (simulated run-to-failure data)

| Component | Model | True RUL inside 5 to 95 % range | Median error (days) |
|---|---|---|---|
| pump | trend | 86% | 78.5 |
| pump | notrend | 87% | 77.6 |
| membrane | trend | 81% | 35.9 |
| membrane | notrend | 79% | 45.0 |
| filter | trend | 84% | 15.7 |
| filter | notrend | 80% | 20.0 |

RUL models are trained on simulated run-to-failure data (aquasense/degradation.py). Present these numbers as simulated.

## Caveat

These scores come from synthetic data produced by the rig model. They prove the pipeline works end to end; they are not evidence of real-world accuracy. Re-run `python -m ml.train --real data/rig_runs` once the 35 to 45 labelled rig runs are recorded.
