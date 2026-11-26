# AquaSense live system: ESP32 + company server + Android app

```
 ESP32 purifier (firmware/aquasense_esp32_v2)
   real sensors: flow, pump current, turbidity
   virtual sensors: TDS in/out, pressure, temperature
        │  Wi-Fi: POST /sensor-data every 2 s   ◄── reply carries the fault overlay
        ▼
 Company server on the laptop (python -m server)
   quick check per device (light rules + learnt baseline) ──► alert ──► job ──► technician
   WebSocket /ws pushes every change in real time
        │                          │
        ▼                          ▼
 Company dashboard (browser)    Android app (one APK, two roles)
   fleet, live charts,            customer: purifier live, visit progress, book service
   fault injection, jobs          technician: jobs, Bluetooth to the ESP32,
                                  FULL diagnosis on the phone, result back to the server
```

Why two levels of model: the server runs a cheap **quick check** on every
device and only forms a *hypothesis*. The heavy **full diagnosis** runs on the
technician's phone, from the Bluetooth stream at the purifier, and confirms or
rejects that hypothesis, with no internet needed on site.

## 1. Start the server (laptop)

Python 3.10+ on the laptop, on the same Wi-Fi or hotspot as the ESP32 and the phones.

```bash
pip install -r requirements.txt
python -m server
```

It prints the dashboard address and the exact `SERVER_URL` line for the
ESP32, for example `http://192.168.1.20:5000/sensor-data`. Open the dashboard,
sign in as `admin` / `1234`.

* No hardware yet, or you want a bigger fleet? Add simulated purifiers:
  `python -m server.device_sim --count 4` (or `./run.sh live` for both).
  They talk to the server exactly like the ESP32 and obey the same fault buttons.
* Windows: allow Python through the firewall on port 5000 when asked.
* Start from scratch: `python -m server --reset`.

## 2. Flash the ESP32

1. Arduino IDE → Boards Manager: **esp32** by Espressif. Board: *ESP32 Dev Module*.
2. Library Manager: **NimBLE-Arduino** (h2zero, 2.x) and **ArduinoJson** (7.x).
3. Open `firmware/aquasense_esp32_v2/aquasense_esp32_v2.ino`, set `WIFI_SSID`,
   `WIFI_PASSWORD` and `SERVER_URL` (from step 1). Pins are the same as firmware V1.
4. Upload with the **pump OFF** (the ACS712 zero is calibrated at power-up).
5. Serial Monitor (115200) shows `Device ID: AQ-A1B2C3` and one block every 2 s.

Within seconds the device appears on the dashboard as a new, unassigned device.
Open it, give it a name and a customer, and save. The server learns the
device's healthy baseline from its first 30 readings with water flowing.

> The sketch is syntax-checked but has not been compiled with the real ESP32
> toolchain here. If the NimBLE 2.x names differ in your version, the BLE block
> (`setupBLE`, `notifyBLE`) is the only part to adjust.

## 3. Install the Android app

* **APK:** every push builds `AquaSense.apk` on GitHub (Actions → *Android APK*).
  Download it from the **android-latest** release (or the run's artifacts) and
  install it on the phone (allow installs from this source).
* **From source** (USB-connected phone, Android SDK installed):
  `cd mobile && npm install && npx expo run:android`.

On the sign-in screen enter the server address from step 1 (no `/sensor-data`).

| Role | Username | PIN | Sees |
|---|---|---|---|
| Service desk (dashboard only) | admin | 1234 | everything, fault injection, jobs |
| Technician | ravi | 1111 | jobs assigned to Ravi |
| Technician | anita | 2222 | jobs assigned to Anita |
| Customer | kavya | 3333 | purifiers linked to Kavya |
| Customer | arjun | 4444 | … |
| Customer | meera | 5555 | … |

## 4. The service flow

1. **Trigger a fault** on the dashboard (device page → *Fault injection*): e.g.
   *RO membrane wear 2*. The ESP32 picks it up in its next reply (the panel
   shows "✓ applied on device") and its readings change, on Wi-Fi **and** Bluetooth.
   Physical faults work too: squeeze the line (flow drops), unplug a sensor.
2. The server's **quick check** sees it; after 3 consistent windows (≈30–70 s)
   it raises an **alert**, opens a **job** and assigns the least busy technician.
3. The technician's phone gets the job instantly (WebSocket) with a vibration
   and an alert. The customer's app shows *Issue detected → Technician assigned*.
4. Technician: *On my way* → *I have arrived* → **Find purifier** (Bluetooth,
   "AquaSense-A1B2C3") → after 30 s of data **Run full diagnosis** → the phone
   shows confirmed / different fault / no fault, component health, remaining
   life and the quote → **Send result to the server** (queued if offline).
5. **Repair done: close the job.** The server clears the injected fault on the
   device, and dashboard + customer app return to healthy.

Customers can also book a visit themselves from the app.

## 5. Fault overlay and virtual sensors

| Dashboard control | What the ESP32 does to its readings | Detected as |
|---|---|---|
| Filter / line clogging 1-3 | flow ×(1-0.7s), pressure ×(1+0.45s), current ×(1+0.15s) | Filter/line clogging |
| Booster pump weakness 1-3 | flow ×(1-0.45s), pressure ×(1-0.55s), current ×(1-0.35s) | Pump weakness |
| RO membrane wear 1-3 | rejection 95.5% − 45s points → TDS out rises | RO membrane wear |
| Pre-filter exhausted 1-3 | turbidity signal ×(1-0.35s) | Pre-filter exhausted |
| Sensor fault | flow stuck at 0 / TDS-out dead / turbidity disconnected / pressure frozen | Sensor fault (no component blamed) |
| Source water 300/800/1500 | TDS in changes, rejection unchanged | nothing (must not alarm) |

s = level / 3. TDS in/out, pressure and temperature are fabricated on the ESP32
until those sensors are fitted; flow, pump current and turbidity are measured.

## 6. Data contract

`server/protocol.py` is the reference (mirrored in `mobile/src/lib/protocol.ts`).

* Wi-Fi: JSON POST to `/sensor-data` with the V1 fields plus `device_id`, `fw`,
  `seq`, `overlay_rev`, `tds_in_ppm`, `tds_out_ppm`, `pressure_bar`, `water_temp_C`, `running`.
  Reply: `{"ok":true,"overlay":{...},"interval_ms":2000}`.
* Bluetooth LE: service `8f2a0001-5a1c-4c2e-9b6e-61717561736e`, notify
  characteristic `…0002…` sends a 20-byte little-endian packet every 2 s
  (flow mL/min, current mA, turbidity mV, TDS in, TDS out ×10, pressure mbar,
  temperature ×100, volume ×10, flags, XOR checksum); read characteristic
  `…0003…` returns `AQ-A1B2C3;fw=2.0.0`. 20 bytes fit the default BLE MTU, so
  no MTU negotiation is needed.

## 7. Known limits

* Phone notifications arrive while the app is open or in the background with its
  connection alive; push when the app is closed needs Firebase Cloud Messaging.
* Traffic on the local network is plain HTTP / WS. Use HTTPS and per-device keys
  (`AQUASENSE_DEVICE_KEY`) before putting the server on the internet.
* Turbidity is reported in volts (lower = cloudier) until it is calibrated to NTU.
