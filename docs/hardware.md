# Unit A: RO test rig hardware

Taken from the build plan (sections 4 and 12) and matched to
`firmware/aquasense_rig/config.h`.

## Water loop

```
Bucket (raw water + salt to simulate borewell/tanker)
  -> Sediment/carbon cartridge (optional, for realism)
  -> Booster pump (24 V diaphragm, current sensed)
  -> [Pressure sensor]
  -> CLOG valve (ball valve, fault injection)
  -> RO membrane
       |-> Permeate (clean water) -> [TDS out] -> collection bottle
       |-> Reject water -> [Flow sensor] -> back to bucket

TDS-in probe and DS18B20 sit in the bucket.
BYPASS needle valve joins the pressurised line to the permeate line (membrane wear).
```

Put the pressure sensor **after the pump and before the CLOG valve**: closing the
valve then raises pressure and cuts flow, which is the clogging signature.

## Sensors

| Signal | Sensor | Why it matters |
|---|---|---|
| TDS in / TDS out | 2x analog TDS module | rejection % = membrane health |
| Water temperature | DS18B20 | TDS temperature compensation |
| Reject flow | YF-S401 | falls when the line clogs |
| Pressure | 0–1.0 MPa transducer, 0.5–4.5 V | low with a weak pump, high with a clog |
| Pump current | ACS712-5A | rises or falls with pump wear |
| Runtime, pump starts, UV hours, filter hours | firmware counters (saved in flash) | usage-based ageing |
| Build-up time | firmware, pressure at 10 Hz after pump start | slow with a weak pump |

We count pump start cycles instead of valve cycles. The "UV lamp" is the on-board
LED (GPIO2) plus an hour counter. **Never fit a real UV-C lamp.**

## Bill of materials (about ₹8,000–8,500)

ESP32 DevKit (30-pin) ₹450 · 2x analog TDS module ₹900 · YF-S401 ₹300 · pressure
transducer 0–1.0 MPa G1/4 + push-fit adapter ₹700 · ACS712-5A ₹150 · DS18B20
waterproof ₹150 · microSD module + 8/16 GB card ₹300 · DS3231 RTC ₹200 · 5 V relay
₹100 · 24 V diaphragm booster pump ₹900 · 24 V 3 A adapter ₹450 · PWM motor speed
controller 10–50 V ₹250 · 75 GPD membrane + housing ₹1,000 · sediment/carbon
cartridge + housing ₹400 (optional) · ball valve + needle valve ₹300 · 1/4" tubing,
push-fit fittings, T-joints, check valve, Teflon tape ₹700 · 0.96" I2C OLED ₹250
(optional) · resistors (10k, 20k, 4.7k, 330 Ω), 3 LEDs, 2 buttons, toggle switch,
jumpers, perfboard ₹250 · bucket, bottle, board, zip ties, drip tray ₹500.

To cut cost drop the cartridge and the OLED first, then the temperature probe.
Never drop the RTC or the pressure sensor.

## ESP32 pin map (ADC1 only: ADC2 stops working when Wi-Fi is on)

| Function | Pin | Notes |
|---|---|---|
| TDS in / TDS out | GPIO34 / GPIO35 | modules powered from 3.3 V |
| TDS probe power | GPIO25 | probes powered only while sampling |
| Pressure | GPIO32 | 10k/20k divider |
| Pump current | GPIO33 | 10k/20k divider |
| Flow pulses | GPIO27 | divider, interrupt |
| DS18B20 | GPIO4 | 4.7k pull-up to 3.3 V |
| SD card (SPI) | CS 5, SCK 18, MISO 19, MOSI 23 | SD module from 5 V |
| RTC + OLED (I2C) | SDA 21, SCL 22 | shared bus |
| Pump relay | GPIO26 | switches the 24 V pump line |
| LEDs | GPIO13 green, GPIO14 yellow, GPIO16 red | 330 Ω each |
| Label button | GPIO17 | to GND (internal pull-up) |
| Freeze toggle (fault panel) | GPIO15 | to GND, freezes the pressure reading |
| "UV lamp" indicator | GPIO2 | on-board LED |

Power: ESP32 from USB (charger or power bank). Pump from the separate 24 V adapter
through the relay and the PWM controller. ACS712 powered from the ESP32 5 V pin.

## Voltage dividers (ESP32 inputs take 3.3 V max)

* Pressure (0.5–4.5 V): R1 = 10k to the signal, R2 = 20k to ground → ×0.667.
  bar = ((V_adc × 1.5) − 0.5) / 4.0 × 10
* ACS712 (185 mV/A, mid-point 2.5 V): same divider.
  A = ((V_adc × 1.5) − 2.5) / 0.185, 200 samples per reading. The firmware
  auto-zeroes the mid-point at boot (pump off).
* Flow pulse line: same divider. YF-S401: Hz ≈ 98 × L/min.
* TDS modules: 0–2.3 V output, fine at 3.3 V.

**Check every divider with a multimeter before connecting it to the ESP32.**

## Calibration (before recording any training data)

1. Pressure: compare with an analog gauge on the same line → `P_OFFSET_BAR`, `P_SCALE`.
2. Current: multimeter in series with the pump → `I_SCALE`.
3. TDS: TDS pen as reference, at least two salt solutions → `TDS_K_IN`, `TDS_K_OUT`.
4. Flow: collect reject water for 60 s in a measuring jug → `FLOW_HZ_PER_LPM`.
5. With the rig healthy, note the steady pressure and flow → `BASE_PRESSURE_BAR`,
   `BASE_FLOW_LPM` (used for build-up time and the local LEDs).

## Fault-injection panel

Mark every position with a sticker (level 1, 2, 3) so recordings are repeatable.

| Failure | How | Expected signature |
|---|---|---|
| Filter/line clogging | close the CLOG ball valve partly (3 levels) | flow falls, pressure rises |
| Pump weakness | turn the PWM controller down (3 levels) | pressure low, current abnormal, build-up slow |
| Membrane wear | open the BYPASS needle valve slightly (3 levels) | TDS out rises, rejection falls |
| Sensor fault | unplug the TDS-out probe, or flip the freeze toggle | one signal abnormal, others normal |
| High-TDS source (not a fault) | salt in the bucket: 300 / 800 / 1500 ppm | TDS in rises; must NOT be flagged |

## Safety

* Water side at **24 V DC or lower**. Enclose the 24 V adapter and its mains lead,
  away from water. Use an RCD/ELCB socket when possible.
* Electronics above the water line, drip tray underneath.
* Leak-check every fitting before powering the pump; dry the board after any spill.
* No real UV-C lamp. Do not drink the permeate from the rig.

## Firmware

Arduino IDE + ESP32 board package. Libraries: OneWire, DallasTemperature, RTClib,
Adafruit SSD1306 (+ Adafruit GFX). Edit `firmware/aquasense_rig/config.h`
(Wi-Fi, laptop IP, calibration) and upload `aquasense_rig.ino`.

The sketch has been syntax-checked but **not yet run on hardware**. Bring it up one
sensor at a time (Serial Monitor at 115200 prints every log line).

Button: short press = next label (training mode), hold 2 s = training mode on/off,
hold 8 s = reset filter hours after a filter change.
