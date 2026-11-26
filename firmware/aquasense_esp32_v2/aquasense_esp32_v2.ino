/*
  AQUASENSE - ESP32 SENSOR FIRMWARE V2

  Built on firmware V1 (same pins, same calibration, same JSON field names).
  New in V2:
    * Device ID from the ESP32's MAC address (AQ-A1B2C3), sent with every reading
    * Wi-Fi never blocks: the sensors keep running while it (re)connects
    * Readings are averaged in millivolts (V1 converted a single sample)
    * "Virtual sensors": TDS in/out, pressure and water temperature are
      fabricated on the device until those sensors are fitted
    * Fault overlay: the company server returns {"overlay":{...}} in every reply.
      The ESP32 applies it to its real and virtual readings, so a fault triggered
      on the dashboard appears on Wi-Fi AND on Bluetooth as if it really happened.
    * Bluetooth LE: the technician's phone connects to "AquaSense-A1B2C3" and gets a
      20-byte notification every 2 s (layout in server/protocol.py)

  Sensors:
  1. ZJ-S201 Water Flow Sensor  -> GPIO35  (external 10k pull-up to 3.3V if open-collector)
  2. ACS712 Current Sensor      -> GPIO33  (10k/15k divider, x0.60)
  3. Turbidity Sensor           -> GPIO32  (keep the output under ~3.1 V: add a divider if needed)

  Libraries (Arduino Library Manager):
    - NimBLE-Arduino (h2zero) 2.x
    - ArduinoJson (Benoit Blanchon) 7.x
  Board: ESP32 Dev Module. Partition scheme: Default is fine (NimBLE is small).
*/

// =====================================================
// LIBRARIES
// =====================================================

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <NimBLEDevice.h>

// =====================================================
// WIFI / SERVER CONFIGURATION
// =====================================================

const char* WIFI_SSID = "itsme";
const char* WIFI_PASSWORD = "net12345";

// Company server. Hosted on Render (HTTPS). For a laptop on the same Wi-Fi use
// e.g. "http://192.168.1.100:5000/sensor-data" ("python -m server" prints it).
const char* SERVER_URL = "https://aquasense-2wjz.onrender.com/sensor-data";
const char* DEVICE_KEY = "";          // set if the server uses AQUASENSE_DEVICE_KEY
const char* FIRMWARE_VERSION = "2.0.0";

// =====================================================
// PIN CONFIGURATION
// =====================================================

const int FLOW_SENSOR_PIN = 35;
const int CURRENT_SENSOR_PIN = 33;
const int TURBIDITY_SENSOR_PIN = 32;

// =====================================================
// SENSOR TIMING
// =====================================================

const unsigned long READ_INTERVAL = 2000;  // 2 seconds
const unsigned long WIFI_RETRY_MS = 15000;

unsigned long lastReadTime = 0;
unsigned long lastWifiAttempt = 0;

// =====================================================
// FLOW SENSOR VARIABLES
// =====================================================

// Initial nominal calibration.
// We will calibrate this using actual collected water volume.
const float FLOW_PULSES_PER_LITRE = 450.0;

volatile unsigned long flowPulseCount = 0;

float flowRate_Lmin = 0.0;
float totalVolume_L = 0.0;

// =====================================================
// ACS712 CURRENT SENSOR VARIABLES
// =====================================================

// ACS712-5A  = 0.185 V/A, ACS712-20A = 0.100 V/A, ACS712-30A = 0.066 V/A
const float ACS712_SENSITIVITY = 0.185;

// 10k from sensor OUT to ADC junction, 15k from junction to GND -> x0.60
const float VOLTAGE_DIVIDER_RATIO = 0.60;

float currentZeroVoltage = 2.50;
float pumpCurrent_A = 0.0;
int currentADC = 0;

// =====================================================
// TURBIDITY SENSOR VARIABLES
// =====================================================

// 1.0 = sensor wired straight to GPIO32. Set 0.60 if you add the same 10k/15k
// divider as the current sensor (needed if the module outputs more than ~3.1 V).
const float TURBIDITY_DIVIDER_RATIO = 1.0;

int turbidityADC = 0;
float turbidityVoltage = 0.0;

// =====================================================
// VIRTUAL SENSORS (fabricated until fitted)
// =====================================================

float tdsIn_ppm = 300.0;
float tdsOut_ppm = 13.5;
float pressure_bar = 0.2;
float waterTemp_C = 26.0;
bool  waterRunning = false;
float frozenPressure = -1.0;

// =====================================================
// FAULT OVERLAY (set from the company dashboard)
// =====================================================

struct Overlay {
  int rev = 0;
  int clog = 0;         // filter / line clogging   0-3
  int pump = 0;         // pump weakness            0-3
  int membrane = 0;     // RO membrane wear         0-3
  int turbidity = 0;    // pre-filter exhausted     0-3
  String sensor = "none";  // none | flow_stuck | tds_out_dead | turbidity_disconnected | pressure_frozen
  int tdsSource = 300;  // source water TDS (ppm)
} overlay;

bool serverReachable = false;
void printConnectionStatus();   // defined further down

// Connection diagnostics shown on the Serial Monitor
int lastHttpCode = 0;                 // last HTTP result (negative = connection error)
String lastHttpProblem = "not tried yet";
unsigned long lastHttpOkMs = 0;       // millis() of the last 200 OK
unsigned long lastHttpMs = 0;         // duration of the last request
uint32_t httpOk = 0, httpFail = 0, httpFailStreak = 0;
int lastWifiReason = 0;               // last Wi-Fi disconnect reason from the ESP32 driver
String lastWifiProblem = "";
bool bleSubscribed = false;
String blePhone = "";

// Raw sensor values before the server's fault overlay (for the Serial log)
float rawFlow = 0, rawCurrent = 0, rawTurb = 0;
unsigned long rawPulses = 0;

// =====================================================
// DEVICE IDENTITY + BLUETOOTH
// =====================================================

String deviceId;      // AQ-A1B2C3
String bleName;       // AquaSense-A1B2C3

#define BLE_SERVICE_UUID "8f2a0001-5a1c-4c2e-9b6e-61717561736e"
#define BLE_DATA_UUID    "8f2a0002-5a1c-4c2e-9b6e-61717561736e"
#define BLE_INFO_UUID    "8f2a0003-5a1c-4c2e-9b6e-61717561736e"

NimBLEServer* bleServer = nullptr;
NimBLECharacteristic* bleData = nullptr;
NimBLECharacteristic* bleInfo = nullptr;
uint8_t bleSeq = 0;

// =====================================================
// SMALL HELPERS
// =====================================================

// Approximately normal noise (sum of uniforms), mean 0, standard deviation sd
float noise(float sd) {
  float s = 0;
  for (int i = 0; i < 4; i++) s += (float)esp_random() / 4294967295.0f;
  return (s - 2.0f) * 1.732f * sd;
}

float averageMilliVolts(int pin, int samples, int gapMicros) {
  long sum = 0;
  for (int i = 0; i < samples; i++) {
    sum += analogReadMilliVolts(pin);
    delayMicroseconds(gapMicros);
  }
  return sum / (float)samples;
}

// =====================================================
// FLOW SENSOR INTERRUPT
// =====================================================

void IRAM_ATTR flowSensorISR() {
  flowPulseCount++;
}

// =====================================================
// FLOW SENSOR READING FUNCTION
// =====================================================

void readFlowSensor(unsigned long elapsedMs) {
  unsigned long pulses;

  // Safely read and reset pulse counter
  noInterrupts();
  pulses = flowPulseCount;
  flowPulseCount = 0;
  interrupts();

  rawPulses = pulses;
  float volumeDuringInterval = pulses / FLOW_PULSES_PER_LITRE;
  flowRate_Lmin = volumeDuringInterval * (60000.0 / (elapsedMs ? elapsedMs : READ_INTERVAL));
}

// =====================================================
// CURRENT SENSOR CALIBRATION (pump OFF at power-up)
// =====================================================

void calibrateCurrentSensor() {
  Serial.println();
  Serial.println("Calibrating ACS712... ensure the pump is OFF!");
  delay(2000);
  float mv = averageMilliVolts(CURRENT_SENSOR_PIN, 500, 2000);
  currentZeroVoltage = (mv / VOLTAGE_DIVIDER_RATIO) / 1000.0;
  Serial.print("ACS712 zero-current voltage: ");
  Serial.print(currentZeroVoltage, 3);
  Serial.println(" V");
}

// =====================================================
// CURRENT SENSOR READING FUNCTION
// =====================================================

void readCurrentSensor() {
  long adcSum = 0;
  long mvSum = 0;
  const int samples = 100;
  for (int i = 0; i < samples; i++) {
    adcSum += analogRead(CURRENT_SENSOR_PIN);
    mvSum += analogReadMilliVolts(CURRENT_SENSOR_PIN);
    delayMicroseconds(200);
  }
  currentADC = adcSum / samples;
  float sensorVoltage = (mvSum / (float)samples / 1000.0) / VOLTAGE_DIVIDER_RATIO;
  pumpCurrent_A = fabs((sensorVoltage - currentZeroVoltage) / ACS712_SENSITIVITY);
  if (pumpCurrent_A < 0.05) pumpCurrent_A = 0.0;   // suppress noise near zero
}

// =====================================================
// TURBIDITY SENSOR READING FUNCTION
// =====================================================

void readTurbiditySensor() {
  long adcSum = 0;
  long mvSum = 0;
  const int samples = 20;
  for (int i = 0; i < samples; i++) {
    adcSum += analogRead(TURBIDITY_SENSOR_PIN);
    mvSum += analogReadMilliVolts(TURBIDITY_SENSOR_PIN);
    delayMicroseconds(500);
  }
  turbidityADC = adcSum / samples;
  turbidityVoltage = (mvSum / (float)samples / 1000.0) / TURBIDITY_DIVIDER_RATIO;
  // Higher voltage = clearer water. NTU calibration can be added later.
}

// =====================================================
// FAULT OVERLAY + VIRTUAL SENSORS
// (same maths as server/device_sim.py -> Esp32Model.read)
// =====================================================

void applyOverlayAndFabricate(float dtSeconds) {
  float sClog = overlay.clog / 3.0, sPump = overlay.pump / 3.0;
  float sMem = overlay.membrane / 3.0, sTurb = overlay.turbidity / 3.0;

  // Real channels, as the fault would change them
  flowRate_Lmin *= (1 - 0.7 * sClog) * (1 - 0.45 * sPump);
  pumpCurrent_A *= (1 + 0.15 * sClog) * (1 - 0.35 * sPump);
  turbidityVoltage *= (1 - 0.35 * sTurb);

  // Virtual channels
  waterRunning = pumpCurrent_A > 0.15 || flowRate_Lmin > 0.05;
  tdsIn_ppm = overlay.tdsSource * (1 + noise(0.01));
  float rejection = 0.955 - 0.45 * sMem;
  tdsOut_ppm = tdsIn_ppm * (1 - rejection) * (1 + noise(0.02)) * (waterRunning ? 1.0 : 1.05);
  if (waterRunning) {
    pressure_bar = 5.5 * (1 + 0.45 * sClog) * (1 - 0.55 * sPump) * (1 + noise(0.012));
  } else {
    pressure_bar = max(0.1f, 0.2f + noise(0.02));
  }
  waterTemp_C += noise(0.02);

  // Sensor faults: one sensor misbehaves
  if (overlay.sensor == "flow_stuck") {
    flowRate_Lmin = 0;
  } else if (overlay.sensor == "tds_out_dead") {
    tdsOut_ppm = fabs(0.4 + noise(0.3));
  } else if (overlay.sensor == "turbidity_disconnected") {
    turbidityVoltage = fabs(0.01 + noise(0.005));
    turbidityADC = 5;
  }
  if (overlay.sensor == "pressure_frozen") {
    if (frozenPressure < 0) frozenPressure = pressure_bar;
    pressure_bar = frozenPressure;
  } else {
    frozenPressure = -1;
  }

  totalVolume_L += flowRate_Lmin * dtSeconds / 60.0;
}

// =====================================================
// WIFI (non-blocking)
// =====================================================

String wifiReasonText(int r) {
  switch (r) {
    case 201: return "hotspot NOT FOUND: check the name, keep it on 2.4 GHz, phone nearby, hotspot screen open (iPhone)";
    case 202: return "authentication FAILED: wrong password, or hotspot set to WPA3-only (use WPA2)";
    case 15: case 204: return "WRONG PASSWORD (4-way handshake timeout)";
    case 203: return "association failed: hotspot refused the ESP32 (device limit reached or MAC filter)";
    case 200: return "signal lost (beacon timeout): move the ESP32 closer to the hotspot";
    case 8: return "disconnected by the ESP32 (normal during a retry)";
    case 2: case 6: case 7: return "hotspot dropped the connection: the phone may have turned the hotspot off";
    case 3: case 4: return "hotspot dropped the ESP32 (inactivity or too many devices)";
    default: return "Wi-Fi driver reason " + String(r);
  }
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.printf("[WIFI] Joined \"%s\", waiting for an IP address...\n", WIFI_SSID);
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      lastWifiProblem = "";
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      lastWifiReason = info.wifi_sta_disconnected.reason;
      lastWifiProblem = wifiReasonText(lastWifiReason);
      Serial.printf("[WIFI] PROBLEM: %s (reason %d)\n", lastWifiProblem.c_str(), lastWifiReason);
      break;
    default:
      break;
  }
}

const char* wifiStatusText(wl_status_t st) {
  switch (st) {
    case WL_CONNECTED: return "connected";
    case WL_NO_SSID_AVAIL: return "hotspot NOT FOUND (name wrong, or hotspot on 5 GHz)";
    case WL_CONNECT_FAILED: return "connection FAILED (wrong password, or WPA3-only)";
    case WL_CONNECTION_LOST: return "connection lost";
    case WL_DISCONNECTED: return "disconnected / still trying";
    case WL_IDLE_STATUS: return "idle";
    default: return "other";
  }
}

void startWiFi() {
  Serial.printf("[WIFI] Connecting to \"%s\" (2.4 GHz only)\n", WIFI_SSID);
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);   // the ESP32 retries by itself; begin() is not called again while connecting
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttempt = millis();
}

void maintainWiFi() {
  static bool wasConnected = false;
  wl_status_t st = WiFi.status();
  bool up = st == WL_CONNECTED;
  if (up && !wasConnected) {
    Serial.printf("[WIFI] Connected. IP %s  RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  }
  if (!up && wasConnected) Serial.println("[WIFI] Lost connection, retrying automatically");
  wasConnected = up;
  // Full restart only if it has really given up, at most every 30 s
  if (!up && millis() - lastWifiAttempt > 30000) {
    Serial.printf("[WIFI] Not connected: status %d = %s. Restarting Wi-Fi.\n", (int)st, wifiStatusText(st));
    WiFi.disconnect(true);
    delay(200);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWifiAttempt = millis();
  }
}

// =====================================================
// WIFI DATA TRANSMISSION FUNCTION
// =====================================================

void parseServerReply(const String& body) {
  JsonDocument doc;
  if (deserializeJson(doc, body)) return;
  JsonVariant o = doc["overlay"];
  if (o.isNull()) return;
  int rev = o["rev"] | overlay.rev;
  if (rev == overlay.rev) return;
  overlay.rev = rev;
  overlay.clog = o["clog"] | 0;
  overlay.pump = o["pump"] | 0;
  overlay.membrane = o["membrane"] | 0;
  overlay.turbidity = o["turbidity"] | 0;
  overlay.sensor = String((const char*)(o["sensor"] | "none"));
  overlay.tdsSource = o["tds_source"] | 300;
  Serial.printf("[SERVER] New condition rev %d: clog=%d pump=%d membrane=%d turbidity=%d sensor=%s tds=%d\n",
                overlay.rev, overlay.clog, overlay.pump, overlay.membrane, overlay.turbidity,
                overlay.sensor.c_str(), overlay.tdsSource);
}

String httpProblemText(int code, HTTPClient& http) {
  if (code == 404) return "404: wrong URL. SERVER_URL must end with /sensor-data";
  if (code == 401) return "401: device key rejected. DEVICE_KEY must match AQUASENSE_DEVICE_KEY on the server";
  if (code == 400) return "400: server rejected the data (" + http.getString().substring(0, 80) + ")";
  if (code == 502 || code == 503) return String(code) + ": server is starting up or asleep (Render free tier wakes in ~1 min)";
  if (code >= 500) return String(code) + ": server error. Check the server logs";
  if (code > 0) return "unexpected HTTP " + String(code);
  if (code == HTTPC_ERROR_CONNECTION_REFUSED) return "connection refused: server not running, or wrong address/port in SERVER_URL";
  if (code == HTTPC_ERROR_CONNECTION_LOST) return "connection lost while sending: weak Wi-Fi or server restarted";
  if (code == HTTPC_ERROR_READ_TIMEOUT) return "timeout: server too slow (Render waking up?) or no internet on the hotspot";
  if (code == HTTPC_ERROR_SEND_HEADER_FAILED || code == HTTPC_ERROR_SEND_PAYLOAD_FAILED) return "send failed: connection dropped (Wi-Fi or TLS)";
  return http.errorToString(code) + " (" + String(code) + "): if HTTPS, check internet on the hotspot and that the URL starts with https://";
}

void sendDataWiFi(uint32_t seq) {
  if (WiFi.status() != WL_CONNECTED) {
    serverReachable = false;
    Serial.println("WiFi disconnected: readings available over Bluetooth only");
    return;
  }

  HTTPClient http;
  http.setTimeout(8000);            // a sleeping free Render service needs a while to wake
  WiFiClientSecure secure;          // must live until http.end()
  WiFiClient plain;
  if (strncmp(SERVER_URL, "https", 5) == 0) {
    secure.setInsecure();           // no certificate check: fine for a prototype
    http.begin(secure, SERVER_URL);
  } else {
    http.begin(plain, SERVER_URL);
  }
  http.addHeader("Content-Type", "application/json");
  if (strlen(DEVICE_KEY)) http.addHeader("X-Device-Key", DEVICE_KEY);

  JsonDocument doc;
  doc["device_id"] = deviceId;
  doc["fw"] = FIRMWARE_VERSION;
  doc["seq"] = seq;
  doc["uptime_s"] = millis() / 1000;
  doc["overlay_rev"] = overlay.rev;
  doc["flow_rate_Lmin"] = serialized(String(flowRate_Lmin, 3));
  doc["total_volume_L"] = serialized(String(totalVolume_L, 3));
  doc["pump_current_A"] = serialized(String(pumpCurrent_A, 3));
  doc["current_ADC"] = currentADC;
  doc["turbidity_ADC"] = turbidityADC;
  doc["turbidity_voltage_V"] = serialized(String(turbidityVoltage, 3));
  doc["tds_in_ppm"] = serialized(String(tdsIn_ppm, 1));
  doc["tds_out_ppm"] = serialized(String(tdsOut_ppm, 1));
  doc["pressure_bar"] = serialized(String(pressure_bar, 3));
  doc["water_temp_C"] = serialized(String(waterTemp_C, 2));
  doc["running"] = waterRunning ? 1 : 0;
  String jsonData;
  serializeJson(doc, jsonData);

  unsigned long t0 = millis();
  int responseCode = http.POST(jsonData);
  lastHttpMs = millis() - t0;
  lastHttpCode = responseCode;
  if (responseCode == 200) {
    serverReachable = true;
    httpOk++;
    httpFailStreak = 0;
    lastHttpOkMs = millis();
    lastHttpProblem = "";
    Serial.printf("[HTTP] #%lu sent to server: 200 OK in %lu ms\n", (unsigned long)seq, lastHttpMs);
    parseServerReply(http.getString());
  } else {
    serverReachable = false;
    httpFail++;
    httpFailStreak++;
    lastHttpProblem = httpProblemText(responseCode, http);
    Serial.printf("[HTTP] #%lu FAILED after %lu ms: %s\n", (unsigned long)seq, lastHttpMs, lastHttpProblem.c_str());
  }
  http.end();
}

// =====================================================
// BLUETOOTH LE (technician phone)
// =====================================================

class BleServerLog : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s, NimBLEConnInfo& info) override {
    blePhone = String(info.getAddress().toString().c_str());
    Serial.printf("[BLE] Phone CONNECTED: %s\n", blePhone.c_str());
  }
  void onDisconnect(NimBLEServer* s, NimBLEConnInfo& info, int reason) override {
    bleSubscribed = false;
    blePhone = "";
    Serial.printf("[BLE] Phone DISCONNECTED (reason %d%s). Advertising again.\n", reason,
                  reason == 0x208 ? ": phone out of range / timeout" : reason == 0x213 ? ": closed by the phone" : "");
  }
};

class BleDataLog : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic* c, NimBLEConnInfo& info, uint16_t subValue) override {
    bleSubscribed = subValue != 0;
    Serial.printf("[BLE] Phone %s live data (subscription %u)\n", subValue ? "SUBSCRIBED to" : "UNSUBSCRIBED from", subValue);
  }
};

void setupBLE() {
  NimBLEDevice::init(bleName.c_str());
  bleServer = NimBLEDevice::createServer();
  bleServer->setCallbacks(new BleServerLog());
  NimBLEService* svc = bleServer->createService(BLE_SERVICE_UUID);
  bleData = svc->createCharacteristic(BLE_DATA_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  bleData->setCallbacks(new BleDataLog());
  bleInfo = svc->createCharacteristic(BLE_INFO_UUID, NIMBLE_PROPERTY::READ);
  String info = deviceId + ";fw=" + FIRMWARE_VERSION;
  bleInfo->setValue((uint8_t*)info.c_str(), info.length());
  svc->start();
  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->setName(bleName.c_str());
  adv->addServiceUUID(BLE_SERVICE_UUID);
  adv->enableScanResponse(true);
  adv->start();
  Serial.print("Bluetooth advertising as ");
  Serial.println(bleName);
}

static inline void put16(uint8_t* b, int i, int32_t v, bool isSigned) {
  if (isSigned) v = constrain(v, -32768, 32767);
  else v = constrain(v, 0, 65535);
  b[i] = (uint8_t)(v & 0xFF);
  b[i + 1] = (uint8_t)((v >> 8) & 0xFF);
}

// 20-byte packet, little-endian (see server/protocol.py)
void notifyBLE() {
  if (!bleData) return;
  uint8_t p[20];
  p[0] = 2;
  p[1] = bleSeq++;
  put16(p, 2, lroundf(flowRate_Lmin * 1000), false);
  put16(p, 4, lroundf(pumpCurrent_A * 1000), true);
  put16(p, 6, lroundf(turbidityVoltage * 1000), false);
  put16(p, 8, lroundf(tdsIn_ppm), false);
  put16(p, 10, lroundf(tdsOut_ppm * 10), false);
  put16(p, 12, lroundf(pressure_bar * 1000), false);
  put16(p, 14, lroundf(waterTemp_C * 100), true);
  put16(p, 16, (lroundf(totalVolume_L * 10)) % 65536, false);
  p[18] = (waterRunning ? 1 : 0) | (WiFi.status() == WL_CONNECTED ? 2 : 0) | (serverReachable ? 4 : 0);
  uint8_t x = 0;
  for (int i = 0; i < 19; i++) x ^= p[i];
  p[19] = x;
  bleData->setValue(p, sizeof(p));
  int clients = bleServer->getConnectedCount();
  if (clients > 0) bleData->notify();

  // Print the packet and the values the phone will decode from it, to cross-check the app
  char hex[61];
  for (int i = 0; i < 20; i++) snprintf(hex + i * 3, 4, "%02X ", p[i]);
  Serial.printf("[BLE] seq %3u %s -> %d phone(s)\n", p[1], clients ? "SENT" : "ready, no phone connected", clients);
  Serial.printf("[BLE] bytes: %s\n", hex);
  Serial.printf("[BLE] phone sees: flow %.3f L/min | pump %.3f A | turbidity %.3f V | TDS in %u | TDS out %.1f | pressure %.3f bar | temp %.2f C | flags run=%d wifi=%d server=%d\n",
                (p[2] | p[3] << 8) / 1000.0, (int16_t)(p[4] | p[5] << 8) / 1000.0, (p[6] | p[7] << 8) / 1000.0,
                (unsigned)(p[8] | p[9] << 8), (p[10] | p[11] << 8) / 10.0, (p[12] | p[13] << 8) / 1000.0,
                (int16_t)(p[14] | p[15] << 8) / 100.0, p[18] & 1, (p[18] >> 1) & 1, (p[18] >> 2) & 1);
}

// =====================================================
// SERIAL MONITOR DISPLAY FUNCTION
// =====================================================

void displaySensorData(uint32_t seq) {
  Serial.println();
  Serial.printf("==================== reading #%lu  (uptime %lus) ====================\n", (unsigned long)seq, millis() / 1000);
  Serial.printf("Device %s | WiFi %s | server %s | BLE phones %d\n", deviceId.c_str(),
                WiFi.status() == WL_CONNECTED ? "up" : "DOWN", serverReachable ? "ok" : "not reached",
                bleServer ? bleServer->getConnectedCount() : 0);
  Serial.println("REAL SENSORS (measured)");
  Serial.printf("  Flow      %.3f L/min  (%lu pulses in last read)   total %.3f L\n", rawFlow, rawPulses, totalVolume_L);
  Serial.printf("  Current   %.3f A      (ADC %d, zero %.3f V)\n", rawCurrent, currentADC, currentZeroVoltage);
  Serial.printf("  Turbidity %.3f V      (ADC %d)\n", rawTurb, turbidityADC);
  Serial.println("VIRTUAL SENSORS (fabricated)");
  Serial.printf("  TDS in %.0f ppm | TDS out %.1f ppm | rejection %.1f%% | pressure %.2f bar | temp %.1f C\n",
                tdsIn_ppm, tdsOut_ppm, 100.0 * (1 - tdsOut_ppm / max(tdsIn_ppm, 1.0f)), pressure_bar, waterTemp_C);
  bool active = overlay.clog || overlay.pump || overlay.membrane || overlay.turbidity || overlay.sensor != "none";
  if (active) {
    Serial.printf("SERVER CONDITION rev %d: clog %d, pump %d, membrane %d, turbidity %d, sensor %s, source %d ppm\n",
                  overlay.rev, overlay.clog, overlay.pump, overlay.membrane, overlay.turbidity, overlay.sensor.c_str(), overlay.tdsSource);
    Serial.printf("  after condition: flow %.3f L/min | current %.3f A | turbidity %.3f V\n", flowRate_Lmin, pumpCurrent_A, turbidityVoltage);
  } else {
    Serial.printf("SERVER CONDITION: none (source water %d ppm)\n", overlay.tdsSource);
  }
  Serial.printf("Water %s\n", waterRunning ? "FLOWING" : "not flowing (idle)");
  printConnectionStatus();
}

// ---- one block per reading: what is connected, and the exact problem if not
void printConnectionStatus() {
  Serial.println("CONNECTION STATUS");
  wl_status_t st = WiFi.status();
  if (st == WL_CONNECTED) {
    int rssi = WiFi.RSSI();
    Serial.printf("  Wi-Fi     OK   \"%s\" IP %s, signal %d dBm%s\n", WIFI_SSID, WiFi.localIP().toString().c_str(), rssi,
                  rssi < -80 ? "  (WEAK: move closer)" : "");
  } else {
    Serial.printf("  Wi-Fi     DOWN status %d = %s\n", (int)st, wifiStatusText(st));
    if (lastWifiProblem.length()) Serial.printf("            problem: %s\n", lastWifiProblem.c_str());
  }
  if (st != WL_CONNECTED) {
    Serial.println("  Server    not tried (no Wi-Fi)");
  } else if (serverReachable) {
    Serial.printf("  Server    OK   %s  (%lu ms, %lu ok / %lu failed)\n", SERVER_URL, lastHttpMs, (unsigned long)httpOk, (unsigned long)httpFail);
  } else {
    Serial.printf("  Server    FAIL %s\n", SERVER_URL);
    Serial.printf("            problem: %s\n", lastHttpProblem.c_str());
    Serial.printf("            %lu failures in a row; last success %s\n", (unsigned long)httpFailStreak,
                  lastHttpOkMs ? (String((millis() - lastHttpOkMs) / 1000) + " s ago").c_str() : "never");
  }
  int phones = bleServer ? bleServer->getConnectedCount() : 0;
  if (phones == 0) {
    Serial.printf("  Bluetooth advertising as %s, no phone connected\n", bleName.c_str());
  } else if (!bleSubscribed) {
    Serial.printf("  Bluetooth phone %s CONNECTED but NOT subscribed: the app is not receiving data (reopen the on-site check)\n", blePhone.c_str());
  } else {
    Serial.printf("  Bluetooth OK   phone %s receiving live data\n", blePhone.c_str());
  }
  Serial.printf("  Memory    %u bytes free\n", (unsigned)ESP.getFreeHeap());
}

// =====================================================
// SETUP
// =====================================================

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("================================");
  Serial.println("       AQUASENSE SYSTEM V2");
  Serial.println("================================");

  // Identity from the MAC address: AQ-A1B2C3
  uint8_t mac[6];
  WiFi.mode(WIFI_STA);
  WiFi.macAddress(mac);
  char suffix[7];
  snprintf(suffix, sizeof(suffix), "%02X%02X%02X", mac[3], mac[4], mac[5]);
  deviceId = String("AQ-") + suffix;
  bleName = String("AquaSense-") + suffix;
  Serial.print("Device ID: ");
  Serial.println(deviceId);

  pinMode(FLOW_SENSOR_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), flowSensorISR, FALLING);

  analogReadResolution(12);
  analogSetPinAttenuation(CURRENT_SENSOR_PIN, ADC_11db);
  analogSetPinAttenuation(TURBIDITY_SENSOR_PIN, ADC_11db);

  calibrateCurrentSensor();   // pump must be OFF here
  setupBLE();
  startWiFi();                // does not block

  Serial.println("AquaSense is ready!");
  lastReadTime = millis();
}

// =====================================================
// MAIN LOOP
// =====================================================

void loop() {
  static uint32_t seq = 0;
  unsigned long currentTime = millis();
  maintainWiFi();

  if (currentTime - lastReadTime >= READ_INTERVAL) {
    unsigned long elapsed = currentTime - lastReadTime;
    lastReadTime = currentTime;

    readFlowSensor(elapsed);
    readCurrentSensor();
    readTurbiditySensor();
    rawFlow = flowRate_Lmin; rawCurrent = pumpCurrent_A; rawTurb = turbidityVoltage;
    applyOverlayAndFabricate(elapsed / 1000.0);

    seq++;
    displaySensorData(seq);
    notifyBLE();          // phone on site gets it even with no Wi-Fi
    sendDataWiFi(seq);    // company server
  }
}
