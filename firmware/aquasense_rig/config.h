// =====================================================================
//  AquaSense RO test rig - configuration (edit this file, not the .ino)
// =====================================================================
#pragma once

// ---------- identity and network ----------
#define DEVICE_ID      "AQ-RIG-01"
#define WIFI_SSID      "your-phone-hotspot"
#define WIFI_PASS      "hotspot-password"
// Laptop's IP on the hotspot (give it a fixed IP in the hotspot settings)
#define SERVER_URL     "http://192.168.43.100:5000/ingest"
#define API_KEY        ""            // same as AQUASENSE_API_KEY on the server, or "" for none
#define TZ_INFO        "IST-5:30"    // used only to set the RTC from NTP

// ---------- timing (build plan 5.1 / 5.2 / 5.4) ----------
#define PUMP_ON_S            90
#define PUMP_OFF_S           30
#define LOG_INTERVAL_S       5     // normal mode
#define LOG_INTERVAL_TRAIN_S 2     // training mode
#define SYNC_INTERVAL_S      10
#define SYNC_BATCH_LINES     40    // lines per HTTP POST
#define SYNC_MAX_BATCHES     5     // per sync tick, so sampling is never starved

// ---------- pins (build plan 4.5, ADC1 only) ----------
#define PIN_TDS_IN      34
#define PIN_TDS_OUT     35
#define PIN_TDS_POWER   25   // probes powered only while sampling
#define PIN_PRESSURE    32   // via 10k/20k divider
#define PIN_CURRENT     33   // ACS712 via 10k/20k divider
#define PIN_FLOW        27   // YF-S401 pulses via divider, interrupt
#define PIN_ONEWIRE      4   // DS18B20, 4.7k pull-up to 3.3V
#define PIN_SD_CS        5   // SCK 18, MISO 19, MOSI 23
#define PIN_SDA         21   // RTC + OLED share I2C
#define PIN_SCL         22
#define PIN_RELAY       26
#define RELAY_ACTIVE_HIGH 1  // set 0 for a low-trigger relay module
#define PIN_LED_GREEN   13
#define PIN_LED_YELLOW  14
#define PIN_LED_RED     16
#define PIN_LABEL_BTN   17   // to GND, internal pull-up
#define PIN_UV_LED       2   // on-board LED = "UV lamp on" indicator (no real UV-C!)
#define PIN_FREEZE_SW   15   // fault panel toggle: freezes the pressure reading. -1 to disable

// ---------- calibration (build plan 4.6; do this before recording data) ----------
#define DIVIDER_GAIN       1.5f    // (10k + 20k) / 20k
// Pressure transducer 0.5-4.5 V = 0-1.0 MPa (0-10 bar)
#define P_V_ZERO           0.5f
#define P_V_SPAN           4.0f
#define P_BAR_FULL         10.0f
#define P_OFFSET_BAR       0.0f    // adjust against an analog gauge
#define P_SCALE            1.0f
// ACS712-5A: 185 mV/A, mid-point ~2.5 V (auto-zeroed at boot with the pump off)
#define ACS_MV_PER_A       185.0f
#define ACS_SAMPLES        200
#define I_SCALE            1.0f    // adjust against a multimeter in series
// YF-S401: frequency (Hz) ~= 98 x flow (L/min)
#define FLOW_HZ_PER_LPM    98.0f
// TDS: K factors from two salt solutions measured with a TDS pen
#define TDS_K_IN           1.0f
#define TDS_K_OUT          1.0f
#define TDS_SAMPLES        30
// Healthy baseline measured once during calibration (used for build-up time and LEDs)
#define BASE_PRESSURE_BAR  5.5f
#define BASE_FLOW_LPM      0.60f

// ---------- local LED rules (build plan 5.5, unit stays "alive" without cloud) ----------
#define WARN_REJECTION_PCT     85.0f
#define REPLACE_REJECTION_PCT  70.0f
#define WARN_FLOW_RATIO        0.70f
#define REPLACE_FLOW_RATIO     0.40f
#define WARN_P_HIGH_RATIO      1.30f
#define WARN_P_LOW_RATIO       0.75f
#define REPLACE_P_LOW_RATIO    0.55f
