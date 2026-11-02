# Demo script (about 6 minutes, build plan section 10)

## Setup before the judges arrive

* Rig plumbed, leak-tested, calibrated, fault panel labelled. Fault panel at "off",
  salt at ~300 ppm.
* Laptop and ESP32 on the **phone hotspot** (laptop has a fixed IP). Then:
  ```
  ./run.sh                 # seeds the simulated fleet, starts server + console
  ```
  Console open on **Fleet view**; check that `AQ-RIG-01` shows "🟢 live".
* Handheld (Raspberry Pi) on the home screen: `python -m handheld.app`.
* Backups: the pre-recorded video, and the prepared SD card
  (`data/sample_sdcard`, or the handheld's **Demo card** button). If the rig is
  down, run the virtual rig instead: `./run.sh rig` (same data path, with an
  on-screen fault panel).

## Script

| # | Time | What we do | What the audience sees |
|---|---|---|---|
| 1 | 30 s | One sentence: fixed timers and reactive service; AquaSense gives every component a health score and RUL. | Fleet view |
| 2 | 45 s | Normal operation. | OLED + console: live charts, green status, RUL ranges |
| 3 | 45 s | **Source variation:** add salt to the bucket (→ ~1500 ppm). | TDS in rises, rejection stays ~95 %, **no alarm** |
| 4 | 90 s | **Scenario 1, company notices:** close the CLOG valve to mark 2. | Flow falls, pressure rises → console raises *Filter/line clogging* (3 windows > 0.7). Press **Contact customer** → job card lists *Sediment/carbon filter*, estimate ₹900. Handheld not needed. |
| 5 | 45 s | **Sensor fault vs real fault:** reopen the valve, unplug the TDS-out probe. | Only one signal disagrees → *Sensor fault suspected*, membrane health stays high (not blamed). |
| 6 | 90 s | **Scenario 2, routine visit:** reconnect the probe, **turn the hotspot off**, turn the PWM knob to mark 2, wait a minute. | Console shows nothing new (unit offline). Pull the SD card, insert into the handheld, **Scan card** → *Pump weakness*, pump health ~50 %, RUL range, **₹2,000** (pump ₹1,500 + labour ₹300 + visit ₹200). |
| 7 | 30 s | **Change and re-quote:** PWM back to normal, open the BYPASS valve to mark 3, log a minute, rescan. | Quote changes to *RO membrane* ₹2,300. |
| 8 | 30 s | Wrap up: **Warranty & parts** screen, no-AMC service model for Atomberg. Turn the hotspot back on: the backlog uploads by itself. | Expected repair cost per unit and total; parts forecast |

Timing notes: an alert needs three consecutive confident 60 s windows, so allow
about 90 s after closing the valve. With the virtual rig you can pre-record a
fault quickly: `python -m simulator.virtual_rig --fault pump --level 2 --offline --prefill 10`.

## What to say about the prototype

* The rig and its sensors are real; the faults are real physical conditions.
* Long-term RUL curves use **simulated** degradation, calibrated from the rig.
* Until the labelled rig runs are recorded, the models are trained on synthetic
  data from the rig model (`reports/training_report.md` says so).
* The cloud is a laptop server here; in production it would be a hosted service.

## Questions judges may ask

* **Why not a timer?** Water sources differ widely; a fixed timer replaces parts too
  early or too late. We use component-level health from sensor data.
* **Bad sensor vs real fault?** Cross-checking related signals plus an anomaly
  detector. One abnormal signal on its own is flagged as a sensor fault.
* **Is the data real?** Rig faults and signals are real. Long-term degradation is
  simulated and calibrated from the rig, and we say so.
* **No internet?** The unit logs to the SD card; the handheld works offline; the
  backlog uploads when Wi-Fi returns.
* **How does this help Atomberg?** Right part first time, fewer wasted visits,
  lower warranty cost, no-AMC service made practical.
* **How does it scale?** Same data format and models for every unit; retraining on
  the growing company dataset.
