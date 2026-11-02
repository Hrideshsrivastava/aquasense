/*
  AquaSense ESP32 firmware sketch (UNTESTED on hardware: calibrate and test before relying on it).
  Reads sensors, logs every reading to an SD card (works offline), and posts to the AquaSense API when Wi-Fi is up.
  Board: ESP32 Dev Module. Libraries: built-in WiFi, HTTPClient, SD, SPI. No extra installs.
  Fill in the CONFIG block. For production use HTTPS with a proper CA certificate, not plain HTTP.
*/
#include <WiFi.h>
#include <HTTPClient.h>
#include <SD.h>
#include <SPI.h>

// ---------- CONFIG ----------
const char* WIFI_SSID = "your-wifi";
const char* WIFI_PASS = "your-password";
const char* API_URL   = "http://192.168.1.10:8000/api/telemetry";  // your server's LAN address
const char* DEVICE_ID = "P-xxxxxxxx";                              // from the portal
const char* DEVICE_KEY = "paste-device-key";                       // shown once at registration
const int   SD_CS = 5;
const unsigned long SEND_EVERY_MS = 60000;
// Analog pins (ADC1 only, so they work with Wi-Fi on): change to match your board
const int PIN_TDS_IN = 34, PIN_TDS_OUT = 35, PIN_FLOW = 32, PIN_PRESSURE = 33, PIN_CURRENT = 36;
// ----------------------------

unsigned long lastSend = 0, uvSeconds = 0, lastTick = 0;
float buildSeconds = 45;  // time for the tank/pressure to reach set point; measure from your pump control logic

float readTds(int pin)   { return analogRead(pin) * (3.3 / 4095.0) * 1000.0 * 0.5; }   // placeholder calibration (ppm)
float readFlow()         { return analogRead(PIN_FLOW) * (3.3 / 4095.0) * 0.8; }       // placeholder (L/min)
float readPressure()     { return 1.0 + analogRead(PIN_PRESSURE) * (3.3 / 4095.0) * 2.0; } // placeholder (bar)
float readPumpCurrent()  { return analogRead(PIN_CURRENT) * (3.3 / 4095.0) * 0.6; }    // placeholder (A)

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  if (!SD.begin(SD_CS)) Serial.println("SD card not found: offline log disabled");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  unsigned long now = millis();
  if (now - lastTick >= 1000) { lastTick = now; /* count UV on-time here when the lamp is on */ uvSeconds++; }
  if (now - lastSend < SEND_EVERY_MS) return;
  lastSend = now;

  char body[220];
  snprintf(body, sizeof(body),
    "{\"tds_in\":%.1f,\"tds_out\":%.1f,\"flow\":%.2f,\"pressure\":%.2f,\"current\":%.2f,\"build\":%.0f,\"uv\":%.1f,\"temp\":%.1f}",
    readTds(PIN_TDS_IN), readTds(PIN_TDS_OUT), readFlow(), readPressure(), readPumpCurrent(),
    buildSeconds, uvSeconds / 3600.0, 28.0 /* replace with a temperature sensor */);

  File f = SD.open("/aquasense.log", FILE_APPEND);              // always log locally first
  if (f) { f.println(body); f.close(); }

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(API_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Id", DEVICE_ID);
    http.addHeader("X-Device-Key", DEVICE_KEY);
    int code = http.POST((uint8_t*)body, strlen(body));
    Serial.printf("POST %d\n", code);
    http.end();
  } else {
    Serial.println("Wi-Fi down: reading kept on SD");
  }
}
