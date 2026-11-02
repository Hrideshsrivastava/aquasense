/*
  AquaSense RO test rig firmware (Unit A) - ESP32 DevKit, Arduino IDE.

  What it does (build plan section 5):
    * runs the booster pump 90 s ON / 30 s OFF and counts pump starts
    * after every pump start samples pressure at 10 Hz to measure the
      build-up time (to 80 % of the healthy baseline) and start-up current
    * reads all sensors at 1 Hz and averages them into one log line every
      5 s (2 s in training mode). A line never mixes pump ON and OFF samples.
    * writes one CSV per day to the SD card (2026-11-03.csv) with RTC time,
      closing the file after every line so pulling the card never loses data
    * every 10 s sends all unsent lines to the cloud (/ingest). If Wi-Fi or
      the server is down it keeps logging; the position of the last line that
      was sent is kept in /sync.txt and the backlog goes out in batches later
    * OLED shows live values; green / yellow / red LEDs follow simple local
      threshold rules so the unit looks alive without the cloud
    * label button (GPIO17): short press = next fault label (training mode),
      hold 2 s = training mode on/off, hold 8 s = reset filter hours
    * freeze toggle (GPIO15, fault panel): holds the pressure reading to
      create a "sensor fault" on demand

  Libraries (Library Manager): OneWire, DallasTemperature, RTClib (Adafruit),
  Adafruit SSD1306, Adafruit GFX. Built in: WiFi, HTTPClient, SD, SPI, Wire,
  Preferences. Board: "ESP32 Dev Module".

  Edit config.h. Compiled against the ESP32 Arduino core 3.x API, but NOT yet
  tested on the hardware: bench-test each sensor on its own first (plan days 4-7).
*/

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <Preferences.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <RTClib.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>
#include "config.h"

static const char* CSV_HEADER =
  "timestamp,device_id,tds_in,tds_out,temp_c,pressure_bar,flow_lpm,pump_current_a,pump_on,"
  "buildup_s,pump_runtime_s,pump_cycles,uv_hours,filter_hours,label";
static const char* LABEL_NAMES[5] = {"normal", "clog", "pump", "membrane", "sensor"};

// ------------------------------------------------------------- devices
OneWire oneWire(PIN_ONEWIRE);
DallasTemperature tempSensor(&oneWire);
RTC_DS3231 rtc;
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
Preferences prefs;
bool sdOk = false, rtcOk = false, oledOk = false;

// ------------------------------------------------------------- state
volatile uint32_t flowPulses = 0;
void IRAM_ATTR onFlowPulse() { flowPulses++; }

bool pumpOn = false;
uint32_t pumpPhaseStartMs = 0;
uint32_t pumpStartMs = 0;

// persistent counters
double runtimeS = 0, uvHours = 0, filterHours = 0;
uint32_t pumpCycles = 0;

// build-up / start-up current measurement
float buildupS = PUMP_ON_S;      // last completed measurement (logged)
bool buildupMeasuring = false;
float startupCurrentSum = 0;
int startupCurrentN = 0;
float lastStartupCurrent = 0;

// 1 Hz accumulator for the current log line
struct Acc {
  double p = 0, q = 0, i = 0;
  int n = 0;
  bool on = false;
} acc;
uint32_t lineStartMs = 0;

float acsZeroV = 2.5f;
float tempC = 25.0f;
float frozenPressure = -1;
bool trainingMode = false;
int label = 0;

// latest values for the OLED / LEDs
float lastP = 0, lastQ = 0, lastI = 0, lastTdsIn = 0, lastTdsOut = 0;
bool lastLineOn = false;
String lastSyncMsg = "boot";
int backlogHint = 0;

uint32_t lastSecondMs = 0, lastFastMs = 0, lastSyncMs = 0, lastOledMs = 0, lastSaveMs = 0, lastWifiTryMs = 0;
uint32_t lastFlowCountMs = 0;
uint32_t lastFlowCount = 0;

// ------------------------------------------------------------- helpers
float adcVolts(int pin, int samples) {
  uint32_t mv = 0;
  for (int k = 0; k < samples; k++) mv += analogReadMilliVolts(pin);
  return (mv / (float)samples) / 1000.0f;
}

float readPressureBar(int samples = 32) {
  float v = adcVolts(PIN_PRESSURE, samples) * DIVIDER_GAIN;
  float bar = ((v - P_V_ZERO) / P_V_SPAN) * P_BAR_FULL;
  bar = bar * P_SCALE + P_OFFSET_BAR;
  if (bar < 0) bar = 0;
#if PIN_FREEZE_SW >= 0
  if (digitalRead(PIN_FREEZE_SW) == LOW) {          // fault panel: freeze the reading
    if (frozenPressure < 0) frozenPressure = bar;
    return frozenPressure;
  }
#endif
  frozenPressure = -1;
  return bar;
}

float readCurrentA(int samples = ACS_SAMPLES) {
  float v = adcVolts(PIN_CURRENT, samples) * DIVIDER_GAIN;
  float a = fabsf((v - acsZeroV) * 1000.0f / ACS_MV_PER_A) * I_SCALE;
  return a;
}

int cmpFloat(const void* a, const void* b) {
  float fa = *(const float*)a, fb = *(const float*)b;
  return (fa > fb) - (fa < fb);
}

// Standard analog TDS module formula (build plan 5.2), median of 30 readings,
// probes powered only while sampling to slow electrode degradation.
void readTds(float& tdsIn, float& tdsOut) {
  digitalWrite(PIN_TDS_POWER, HIGH);
  delay(60);
  float a[TDS_SAMPLES], b[TDS_SAMPLES];
  for (int k = 0; k < TDS_SAMPLES; k++) {
    a[k] = analogReadMilliVolts(PIN_TDS_IN) / 1000.0f;
    b[k] = analogReadMilliVolts(PIN_TDS_OUT) / 1000.0f;
    delay(4);
  }
  digitalWrite(PIN_TDS_POWER, LOW);
  qsort(a, TDS_SAMPLES, sizeof(float), cmpFloat);
  qsort(b, TDS_SAMPLES, sizeof(float), cmpFloat);
  float coef = 1.0f + 0.02f * (tempC - 25.0f);
  auto conv = [&](float raw, float k) {
    float v = raw / coef;
    float t = (133.42f * v * v * v - 255.86f * v * v + 857.39f * v) * 0.5f * k;
    return t < 0 ? 0.0f : t;
  };
  tdsIn = conv(a[TDS_SAMPLES / 2], TDS_K_IN);
  tdsOut = conv(b[TDS_SAMPLES / 2], TDS_K_OUT);
}

void setRelay(bool on) {
  digitalWrite(PIN_RELAY, (on == (bool)RELAY_ACTIVE_HIGH) ? HIGH : LOW);
  digitalWrite(PIN_UV_LED, on ? HIGH : LOW);  // "UV lamp" runs while water flows
}

DateTime nowTime() {
  if (rtcOk) return rtc.now();
  return DateTime(2026, 1, 1, 0, 0, 0) + TimeSpan(millis() / 1000);  // no RTC: still monotonic
}

String dayFileName(const DateTime& t) {
  char b[20];
  snprintf(b, sizeof(b), "/%04d-%02d-%02d.csv", t.year(), t.month(), t.day());
  return String(b);
}

void saveCounters() {
  prefs.putDouble("runtime", runtimeS);
  prefs.putDouble("uv", uvHours);
  prefs.putDouble("filter", filterHours);
  prefs.putUInt("cycles", pumpCycles);
}

// ------------------------------------------------------------- SD log
void writeLine(float tdsIn, float tdsOut) {
  if (acc.n == 0) return;
  DateTime t = nowTime();
  float p = acc.p / acc.n, q = acc.q / acc.n, i = acc.i / acc.n;
  char ts[24];
  snprintf(ts, sizeof(ts), "%04d-%02d-%02dT%02d:%02d:%02d", t.year(), t.month(), t.day(), t.hour(), t.minute(), t.second());
  char lbl[4] = "";
  if (trainingMode) snprintf(lbl, sizeof(lbl), "%d", label);
  char line[220];
  snprintf(line, sizeof(line), "%s,%s,%.1f,%.1f,%.2f,%.3f,%.3f,%.3f,%d,%.1f,%lu,%lu,%.3f,%.3f,%s",
           ts, DEVICE_ID, tdsIn, tdsOut, tempC, p, q, i, acc.on ? 1 : 0, buildupS,
           (unsigned long)runtimeS, (unsigned long)pumpCycles, uvHours, filterHours, lbl);
  Serial.println(line);
  lastP = p; lastQ = q; lastI = i; lastTdsIn = tdsIn; lastTdsOut = tdsOut; lastLineOn = acc.on;
  if (sdOk) {
    String fn = dayFileName(t);
    bool isNew = !SD.exists(fn);
    File f = SD.open(fn, FILE_APPEND);
    if (f) {
      if (isNew) f.println(CSV_HEADER);
      f.println(line);
      f.close();  // close = flush: removing the card never loses data
    } else {
      sdOk = false;
      Serial.println("SD write failed");
    }
  }
  acc = Acc();
  acc.on = pumpOn;
  lineStartMs = millis();
}

void flushLine() {
  float tin, tout;
  readTds(tin, tout);
  tempSensor.requestTemperatures();  // non-blocking; read next time
  float t = tempSensor.getTempCByIndex(0);
  if (t > -50 && t < 85) tempC = t;
  writeLine(tin, tout);
}

// ------------------------------------------------------------- Wi-Fi sync with backlog
bool readSync(String& name, uint32_t& offset) {
  File f = SD.open("/sync.txt", FILE_READ);
  if (!f) return false;
  String s = f.readStringUntil('\n');
  f.close();
  int c = s.indexOf(',');
  if (c < 0) return false;
  name = "/" + s.substring(0, c);
  offset = s.substring(c + 1).toInt();
  return true;
}

void writeSync(const String& name, uint32_t offset) {
  SD.remove("/sync.tmp");
  File f = SD.open("/sync.tmp", FILE_WRITE);
  if (!f) return;
  f.printf("%s,%lu\n", name.substring(1).c_str(), (unsigned long)offset);
  f.close();
  SD.remove("/sync.txt");
  SD.rename("/sync.tmp", "/sync.txt");
}

bool isDayFile(const char* n) {  // "2026-11-03.csv"
  if (strlen(n) != 14) return false;
  return n[4] == '-' && n[7] == '-' && strcmp(n + 10, ".csv") == 0;
}

// Oldest day file after `after` ("" = oldest of all). Returns "" if none.
String nextDayFile(const String& after) {
  File root = SD.open("/");
  String best = "";
  if (!root) return best;
  for (File e = root.openNextFile(); e; e = root.openNextFile()) {
    String n = String("/") + e.name();
    if (n.startsWith("//")) n = n.substring(1);
    const char* base = n.c_str() + 1;
    if (!e.isDirectory() && isDayFile(base) && n > after && (best == "" || n < best)) best = n;
    e.close();
  }
  root.close();
  return best;
}

int postLines(const String& body) {
  HTTPClient http;
  http.setTimeout(4000);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "text/csv");
  if (strlen(API_KEY)) http.addHeader("X-Api-Key", API_KEY);
  int code = http.POST(body);
  http.end();
  return code;
}

void syncToCloud() {
  if (!sdOk) return;
  if (WiFi.status() != WL_CONNECTED) {
    lastSyncMsg = "WiFi down";
    return;
  }
  String name; uint32_t off = 0;
  if (!readSync(name, off)) { name = nextDayFile(""); off = 0; if (name == "") return; }
  String today = dayFileName(nowTime());
  for (int batch = 0; batch < SYNC_MAX_BATCHES; batch++) {
    File f = SD.open(name, FILE_READ);
    if (!f) {  // file vanished: move on
      name = nextDayFile(name); off = 0;
      if (name == "") return;
      continue;
    }
    f.seek(off);
    String body;
    uint32_t pos = off;
    int lines = 0;
    while (f.available() && lines < SYNC_BATCH_LINES) {
      String l = f.readStringUntil('\n');  // lines are written whole (file closed after each)
      pos += l.length() + 1;
      l.trim();
      if (l.length() == 0 || l.startsWith("timestamp,")) continue;
      body += l; body += '\n';
      lines++;
    }
    bool atEnd = !f.available();
    f.close();
    if (lines == 0) {
      if (atEnd && name < today) {          // that day is fully sent: next file
        String nx = nextDayFile(name);
        if (nx == "") return;
        name = nx; off = 0;
        writeSync(name, 0);
        continue;
      }
      lastSyncMsg = "in sync";
      return;
    }
    int code = postLines(body);
    if (code != 200) {
      lastSyncMsg = "srv " + String(code);
      return;  // keep the backlog, try again next tick
    }
    off = pos;
    writeSync(name, off);
    lastSyncMsg = "sent " + String(lines);
  }
}

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiTryMs < 30000 && lastWifiTryMs != 0) return;
  lastWifiTryMs = millis();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void syncRtcFromNtp() {
  if (!rtcOk || WiFi.status() != WL_CONNECTED) return;
  static bool done = false;
  if (done) return;
  configTzTime(TZ_INFO, "pool.ntp.org", "time.google.com");
  struct tm tmv;
  if (getLocalTime(&tmv, 3000)) {
    if (rtc.lostPower() || abs((long)(rtc.now().unixtime() - (uint32_t)(mktime(&tmv)))) > 5) {
      rtc.adjust(DateTime(tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday, tmv.tm_hour, tmv.tm_min, tmv.tm_sec));
      Serial.println("RTC set from NTP");
    }
    done = true;
  }
}

// ------------------------------------------------------------- LEDs + OLED
void updateLeds() {
  int level = 0;  // 0 normal, 1 warning, 2 replace
  if (lastLineOn && lastTdsIn > 20) {
    float rej = (1.0f - lastTdsOut / lastTdsIn) * 100.0f;
    float qr = lastQ / BASE_FLOW_LPM, pr = lastP / BASE_PRESSURE_BAR;
    if (rej < WARN_REJECTION_PCT || qr < WARN_FLOW_RATIO || pr > WARN_P_HIGH_RATIO || pr < WARN_P_LOW_RATIO) level = 1;
    if (rej < REPLACE_REJECTION_PCT || qr < REPLACE_FLOW_RATIO || pr < REPLACE_P_LOW_RATIO) level = 2;
  }
  static int shown = 0;
  if (lastLineOn) shown = level;  // keep the last ON verdict while the pump rests
  digitalWrite(PIN_LED_GREEN, shown == 0);
  digitalWrite(PIN_LED_YELLOW, shown == 1);
  digitalWrite(PIN_LED_RED, shown == 2);
}

void updateOled() {
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 0);
  oled.printf("AquaSense %s\n", WiFi.status() == WL_CONNECTED ? "WiFi" : "noWiFi");
  oled.printf("P %.2fbar Q %.2fL/m\n", lastP, lastQ);
  oled.printf("I %.2fA  pump %s\n", lastI, pumpOn ? "ON" : "off");
  oled.printf("TDS %4.0f -> %3.0f\n", lastTdsIn, lastTdsOut);
  oled.printf("build %.1fs st %.2fA\n", buildupS, lastStartupCurrent);
  if (trainingMode) oled.printf("TRAIN label %d %s\n", label, LABEL_NAMES[label]);
  else oled.printf("normal mode\n");
  oled.printf("%s%s\n", sdOk ? "SD ok " : "SD ERR ", lastSyncMsg.c_str());
  oled.display();
}

// ------------------------------------------------------------- button
void handleButton() {
  static bool wasDown = false;
  static uint32_t downMs = 0;
  bool down = digitalRead(PIN_LABEL_BTN) == LOW;
  if (down && !wasDown) downMs = millis();
  if (!down && wasDown) {
    uint32_t held = millis() - downMs;
    if (held > 8000) {
      filterHours = 0; saveCounters();
      Serial.println("Filter hours reset");
    } else if (held > 2000) {
      trainingMode = !trainingMode;
      Serial.printf("Training mode %s\n", trainingMode ? "ON" : "OFF");
    } else if (held > 40 && trainingMode) {
      label = (label + 1) % 5;
      Serial.printf("Label %d (%s)\n", label, LABEL_NAMES[label]);
    }
  }
  wasDown = down;
}

// ------------------------------------------------------------- pump schedule
void startPump() {
  pumpOn = true;
  pumpPhaseStartMs = pumpStartMs = millis();
  pumpCycles++;
  buildupMeasuring = true;
  startupCurrentSum = 0;
  startupCurrentN = 0;
  setRelay(true);
}

void stopPump() {
  pumpOn = false;
  pumpPhaseStartMs = millis();
  if (buildupMeasuring) { buildupS = PUMP_ON_S; buildupMeasuring = false; }  // never reached: cap
  setRelay(false);
  saveCounters();
}

// 10 Hz for the first 10 s after a start: build-up time and start-up current.
// After 10 s the 1 Hz loop keeps checking build-up until the ON period ends.
void fastSample() {
  float since = (millis() - pumpStartMs) / 1000.0f;
  if (since <= 10.0f) {
    float p = readPressureBar(8);
    startupCurrentSum += readCurrentA(40);
    startupCurrentN++;
    if (buildupMeasuring && p >= 0.8f * BASE_PRESSURE_BAR) { buildupS = since; buildupMeasuring = false; }
    if (since > 9.9f && startupCurrentN) lastStartupCurrent = startupCurrentSum / startupCurrentN;
  }
}

// ------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_UV_LED, OUTPUT);
  pinMode(PIN_TDS_POWER, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LABEL_BTN, INPUT_PULLUP);
#if PIN_FREEZE_SW >= 0
  pinMode(PIN_FREEZE_SW, INPUT_PULLUP);
#endif
  setRelay(false);
  digitalWrite(PIN_TDS_POWER, LOW);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);  // full 0-3.3 V range

  Wire.begin(PIN_SDA, PIN_SCL);
  rtcOk = rtc.begin();
  if (!rtcOk) Serial.println("RTC not found: timestamps will be wrong");
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  sdOk = SD.begin(PIN_SD_CS);
  if (!sdOk) Serial.println("SD card not found: offline log disabled");
  tempSensor.begin();
  tempSensor.setWaitForConversion(false);
  tempSensor.requestTemperatures();

  prefs.begin("aquasense", false);
  runtimeS = prefs.getDouble("runtime", 0);
  uvHours = prefs.getDouble("uv", 0);
  filterHours = prefs.getDouble("filter", 0);
  pumpCycles = prefs.getUInt("cycles", 0);

  acsZeroV = adcVolts(PIN_CURRENT, 400) * DIVIDER_GAIN;  // pump is off: auto-zero the ACS712
  Serial.printf("ACS712 zero %.3f V\n", acsZeroV);

  pinMode(PIN_FLOW, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_FLOW), onFlowPulse, FALLING);

  WiFi.mode(WIFI_STA);
  ensureWifi();
  lastSecondMs = lastFlowCountMs = lineStartMs = millis();
  startPump();
  acc.on = true;
}

void loop() {
  uint32_t now = millis();
  handleButton();

  // pump schedule: 90 s ON / 30 s OFF
  if (pumpOn && now - pumpPhaseStartMs >= PUMP_ON_S * 1000UL) stopPump();
  else if (!pumpOn && now - pumpPhaseStartMs >= PUMP_OFF_S * 1000UL) startPump();

  if (pumpOn && now - lastFastMs >= 100) { lastFastMs = now; fastSample(); }

  // 1 Hz internal reading
  if (now - lastSecondMs >= 1000) {
    float dt = (now - lastSecondMs) / 1000.0f;
    lastSecondMs = now;
    noInterrupts();
    uint32_t pulses = flowPulses;
    interrupts();
    float dtFlow = (now - lastFlowCountMs) / 1000.0f;
    float hz = (pulses - lastFlowCount) / (dtFlow > 0 ? dtFlow : 1);
    lastFlowCount = pulses; lastFlowCountMs = now;
    float q = hz / FLOW_HZ_PER_LPM;
    float p = readPressureBar();
    float i = readCurrentA();

    if (pumpOn) {
      runtimeS += dt;
      uvHours += dt / 3600.0;
      filterHours += dt / 3600.0;
      if (buildupMeasuring && (now - pumpStartMs) > 10000 && p >= 0.8f * BASE_PRESSURE_BAR)
        { buildupS = (now - pumpStartMs) / 1000.0f; buildupMeasuring = false; }
    }
    // a line never mixes pump ON and OFF samples: flush the partial line first
    if (acc.n > 0 && acc.on != pumpOn) flushLine();
    acc.on = pumpOn;
    acc.p += p; acc.q += q; acc.i += i; acc.n++;

    uint32_t interval = (trainingMode ? LOG_INTERVAL_TRAIN_S : LOG_INTERVAL_S) * 1000UL;
    if (now - lineStartMs >= interval) flushLine();
    updateLeds();
  }

  if (now - lastSyncMs >= SYNC_INTERVAL_S * 1000UL) {
    lastSyncMs = now;
    ensureWifi();
    syncRtcFromNtp();
    syncToCloud();
  }
  if (now - lastOledMs >= 1000) { lastOledMs = now; updateOled(); }
  if (now - lastSaveMs >= 60000) { lastSaveMs = now; saveCounters(); }
  static uint32_t lastSdTryMs = 0;
  if (!sdOk && now - lastSdTryMs >= 30000) { lastSdTryMs = now; SD.end(); sdOk = SD.begin(PIN_SD_CS); }  // card re-inserted
}
