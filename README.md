# AquaSense: from purifiers to intelligent water ecosystems

Team Clusters · Atomberg Hackathon · track *Rewriting the Rules of Clean Water*

Purifiers report only TDS and one filter-life percentage, yet failures span many
components, and fixed timers ignore how sharply Indian water sources differ.
AquaSense gives **every component a health score and a Remaining Useful Life
(RUL)**, names the fault only when related signals agree, and turns that into a
**dynamic repair price**, online through the company cloud and offline through a
technician handheld that reads the purifier's SD card.

This repository is the complete prototype from the build plan:

| Unit | What it is | Where |
|---|---|---|
| **A. RO test rig** | ESP32 + TDS in/out, pressure, flow, pump current, temperature, RTC, SD card, Wi-Fi; fault-injection panel | `firmware/aquasense_rig/`, `docs/hardware.md` |
| **B. Technician handheld** | Raspberry Pi + touchscreen + card reader, Tkinter app, models run offline | `handheld/` |
| **C. Cloud + company console** | Flask `/ingest` → SQLite → ML engine → alerts; Streamlit console (fleet, unit detail, alerts & job cards, warranty & parts) | `cloud/`, `console/` |
| **D. ML models** | XGBoost fault classifier, Isolation Forest anomaly detector, health regressors, quantile RUL forecaster, SHAP explanations | `aquasense/`, `ml/`, `models/` |
| Mobile app | Expo companion app (same stack as MuscleSense): customer view + technician jobs | `mobile/` |
| Virtual rig | Software stand-in for Unit A with an on-screen fault panel (rehearsal and backup) | `simulator/` |

```
[Water loop + sensors] -> ESP32 -> SD card log ----------------------------.
                              \-> Wi-Fi (HTTP, every 10 s, with backlog)    |
                                   -> Flask /ingest -> SQLite -> ML engine  | technician removes
                                      -> alerts -> Contact customer         | the card on a visit
                                      -> Streamlit console / mobile app     v
                                              Raspberry Pi handheld -> same ML -> health, fault, RUL, price
```

Both paths carry the same CSV format and load the same model files, so the cloud
and the handheld give the same answer.

## The two demo scenarios

| | Scenario 1: company notices | Scenario 2: routine visit |
|---|---|---|
| Trigger | the cloud model flags a fault (p > 0.7 for 3 consecutive windows) | no alert (Wi-Fi down, or fault too mild) |
| What happens | console → **Contact customer** → ticket + job card with the part to carry | technician plugs the SD card into the handheld |
| Handheld? | no | yes: component health, fault, RUL range, dynamic price |

Full 6-minute script with timings: [`docs/demo_script.md`](docs/demo_script.md).

## Quick start (laptop)

Python 3.10+.

```bash
./run.sh            # Windows: run.bat
```

This creates `.venv`, installs `requirements.txt`, trains the models if
`models/` is empty, seeds six **simulated** fleet units, starts the cloud server
on port 5000 and opens the console. Then feed it live data:

```bash
./run.sh rig        # virtual rig with a fault-injection panel (or power the real ESP32 rig)
./run.sh handheld   # technician handheld in a window; "Demo card" loads data/sample_sdcard
```

Manual equivalents:

```bash
python -m cloud.server                       # Flask API on :5000
python -m simulator.seed_fleet               # simulated fleet units
streamlit run console/app.py                 # company console
python -m simulator.virtual_rig --panel      # virtual rig (headless: type "clog 2", "wifi off", ...)
python -m handheld.app --windowed            # handheld; --cli --card PATH prints the quote
```

Scenario 2 without hardware: `python -m simulator.virtual_rig --fault pump --level 2 --offline --prefill 10`
writes ten minutes of a weak pump to `data/sdcard/` without reaching the cloud;
point the handheld at that folder.

## Unit A: rig and firmware

Wiring, BOM, dividers, calibration, fault panel and safety rules are in
[`docs/hardware.md`](docs/hardware.md). Firmware highlights
(`firmware/aquasense_rig/aquasense_rig.ino`, settings in `config.h`):

* pump 90 s ON / 30 s OFF; pump starts counted; build-up time measured at 10 Hz after
  each start (time to 80 % of the healthy baseline pressure, capped at the ON period)
* 1 Hz readings averaged into a log line every 5 s (2 s in training mode); TDS uses the
  standard module formula with temperature compensation, median of 30 readings,
  probes powered only while sampling
* one CSV per day (`2026-11-03.csv`), RTC timestamps, file closed after every line
* every 10 s all unsent lines go to `/ingest`; the position of the last sent line is
  kept in `/sync.txt`, so a Wi-Fi outage only delays the upload
* OLED live values; green/yellow/red LEDs from local thresholds; label button for
  training mode; freeze toggle for the sensor-fault demo

The sketch passes a syntax check but has **not been run on the hardware yet**.

## Unit B: technician handheld (Raspberry Pi)

```bash
sudo apt install python3-tk
pip install -r requirements-handheld.txt
python -m handheld.app                 # full screen; Esc leaves full screen
```

Copy the whole repository (or at least `aquasense/`, `handheld/`, `models/`) to the
Pi. The app polls `/media/*`, `/mnt/*` (and drive letters on Windows) for a card
with `YYYY-MM-DD.csv` files, reads the last 30 minutes of the newest device,
and shows **Health / Fault / Quote / Unit** tabs. *Save quote* writes a text quote and
a JSON job summary to `~/AquaSense_quotes/`. It works at 480×320 and 800×480.

## Unit C: cloud server and console

`cloud/server.py` endpoints (JSON):

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | rows from the ESP32: CSV lines (SD format, header optional) or JSON; duplicates ignored |
| GET | `/api/units`, `/api/units/<id>` | fleet list; unit assessment (fault, reason, health, RUL, quote, alerts, tickets) |
| GET | `/api/units/<id>/readings?minutes=15`, `/api/units/<id>/history` | live sensor rows; daily health history |
| POST | `/api/units/<id>/contact` | Scenario 1: ticket + job card (one open job per unit and fault) |
| GET/POST | `/api/alerts`, `/api/alerts/<id>/ack` | alerts |
| GET/PATCH | `/api/tickets`, `/api/tickets/<id>`, `/api/tickets/<id>/jobcard` | tickets and job cards |
| GET | `/api/warranty` | expected repair cost per unit and in total, 30-day parts forecast |

Every 10 s of new data is scored with a 60 s window, in time order, so a backlog
uploaded after an outage is scored the same way as live data. Set
`AQUASENSE_API_KEY` to require an `X-Api-Key` header on `/ingest`.

Networking: college Wi-Fi often blocks device-to-device traffic. Put the laptop and
the ESP32 on a **phone hotspot**, give the laptop a fixed IP, and put
`http://<laptop-ip>:5000/ingest` in `config.h`.

## Unit D: ML

```bash
python -m ml.train                      # synthetic rig data (default), about 40 s
python -m ml.train --real data/rig_runs # your labelled rig recordings + logbook.csv
```

| Model | Algorithm | Job |
|---|---|---|
| A. Fault classifier | XGBoost (Random Forest fallback) | normal / clogging / pump / membrane / sensor |
| B. Anomaly detector | Isolation Forest on normal windows | unusual behaviour; separates sensor faults from wear |
| C. Health score | XGBoost regressor per component, on fault-panel levels (100 healthy, 77 / 53 / 30 for levels 1 / 2 / 3) | pump, membrane, filter/line; sensors from the sensor-fault probability |
| D. RUL | quantile gradient boosting (5 / 50 / 95 %) on simulated run-to-failure data | e.g. "41–52 days" to the 40 % threshold |
| Explanations | TreeSHAP (XGBoost `pred_contribs`, no extra package on the Pi) | which signals drove the decision |

**Features** (per 60 s window, pump-ON steady state): mean, standard deviation and
slope of pressure, flow, current, rejection % and pressure per amp, plus build-up
time, start-up current, TDS noise, and pressure/flow while the pump is off. Rows are
first averaged into 10 s bins and the 10 s after each pump start are kept apart, so
2 s training-mode logs and 5 s normal-mode logs produce identical features.

**Cross-check** (abstract Fig. 3): a fault is named only when related signals agree.
Low pressure counts as pump degradation only with abnormal current and/or slow
build-up; clogging needs low flow *and* high pressure; membrane wear needs falling
rejection with plausible TDS readings. Physically impossible readings (TDS out ≈ 0,
TDS out > TDS in, a frozen value, pressure while the pump is off) or one abnormal
signal on its own give **Sensor fault suspected**, and no component is blamed.

**Honest evaluation** (`reports/training_report.md`): split by run (whole runs held
out), confusion matrix and F1 per class, run-level accuracy, and an explicit
high-TDS normal-water test. On the current synthetic data: macro F1 0.90 for the
model alone and 1.00 with the cross-check on 16 held-out runs, no false alarms on
1500 ppm water. **These numbers come from synthetic data and are not evidence of
real-world accuracy**; retrain on the 35–45 real rig runs
([`docs/data_collection_protocol.md`](docs/data_collection_protocol.md)).

**RUL data problem:** we cannot wait months for real wear. `aquasense/degradation.py`
simulates health over days from usage and source TDS (harder water wears the
membrane and clogs the filter faster), and the RUL model is trained on those
simulated run-to-failure sequences. Present RUL as simulated.

## Dynamic repair pricing

`aquasense/pricing.py` (placeholder prices: filter ₹400, membrane ₹1,800, pump
₹1,500, sensor/electronics ₹600, labour ₹300, visit ₹200):

* **Replace now** if health < 40 % or failure probability within 30 days > 0.6
* **Watch list** if failure probability is 0.2–0.6 (not billed)
* **Repair price** = parts replaced now + labour + visit charge
* **Expected cost** (warranty planning) = Σ failure probability × part cost

Failure probability = max(P(RUL < 30 days) from the RUL quantiles, fault confidence ×
severity). The build plan's worked example (pump + filter → ₹2,400) is a unit test.
Change the fault on the rig, rescan the card, and the quote moves.

## Data format

One CSV per day on the SD card, the same columns over Wi-Fi:

```
timestamp,device_id,tds_in,tds_out,temp_c,pressure_bar,flow_lpm,pump_current_a,pump_on,buildup_s,pump_runtime_s,pump_cycles,uv_hours,filter_hours,label
```

`label` is empty in normal mode; in training mode it holds the class (0 normal,
1 clogging, 2 pump weakness, 3 membrane wear, 4 sensor fault).

## Tests

```bash
pytest -q
```

Covers the pricing rules and worked example, every fault class through the engine,
sensor-fault handling, health vs. severity, 2 s/5 s feature equivalence, the cloud path
(ingest, duplicates, backlog scoring, alert after 3 windows, job card, warranty,
API key), the SD-card backlog across midnight, and the handheld on the sample card.

## Repository map

```
aquasense/      shared core: schema, rig model, features, engine, RUL, degradation, pricing
cloud/          Flask server + SQLite + business logic
console/        Streamlit company console
handheld/       Raspberry Pi Tkinter app + SD-card reader
ml/             synthetic data generator (follows the protocol) and training
models/         trained joblib files + model_card.json (loaded by cloud and handheld)
reports/        training report (confusion matrices, F1, RUL coverage)
simulator/      virtual rig, simulated fleet seeder, sample SD card builder
firmware/       ESP32 sketch + config.h
mobile/         Expo companion app
data/           sample_sdcard (demo backup), rig_runs (your recordings)
docs/           hardware, data collection protocol, logbook template, demo script
legacy/         the earlier abstract-round prototype (FastAPI), kept for reference
```

## Known limitations

* Models are trained on synthetic data from the rig model until real labelled runs
  exist. Long-term RUL is simulated by design.
* The engine compares each unit against a fleet-wide healthy baseline. A unit whose
  own healthy pressure/flow sits far (2σ+) from the fleet average can be mistaken
  for a level-1 fault. Capturing a per-unit baseline at installation (as the rig
  calibration does) is the next step.
* Firmware is untested on hardware. The cloud is a laptop server with no user
  accounts; use HTTPS, authentication and a real database before any deployment.
* RUL is an estimate, not a water-safety certification.
