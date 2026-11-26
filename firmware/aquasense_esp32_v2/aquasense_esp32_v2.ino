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
#include <ArduinoJson.h>
#include <NimBLEDevice.h>

// =====================================================
// WIFI / SERVER CONFIGURATION
// =====================================================

const char* WIFI_SSID = "itsme";
const char* WIFI_PASSWORD = "net12345";

// Your laptop's IP address on the same Wi-Fi. "python -m server" prints the
// exact line to paste here when it starts.
const char* SERVER_URL = "http://192.168.1.100:5000/sensor-data";
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

void startWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttempt = millis();
}

void maintainWiFi() {
  static bool wasConnected = false;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wasConnected) {
    Serial.print("WiFi connected! ESP32 IP Address: ");
    Serial.println(WiFi.localIP());
  }
  wasConnected = up;
  if (!up && millis() - lastWifiAttempt > WIFI_RETRY_MS) {
    WiFi.disconnect();
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
  Serial.printf("Server set condition rev %d: clog=%d pump=%d membrane=%d turbidity=%d sensor=%s tds=%d\n",
                overlay.rev, overlay.clog, overlay.pump, overlay.membrane, overlay.turbidity,
                overlay.sensor.c_str(), overlay.tdsSource);
}

void sendDataWiFi(uint32_t seq) {
  if (WiFi.status() != WL_CONNECTED) {
    serverReachable = false;
    Serial.println("WiFi disconnected: readings available over Bluetooth only");
    return;
  }

  HTTPClient http;
  http.setTimeout(3000);
  http.begin(SERVER_URL);
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

  int responseCode = http.POST(jsonData);
  if (responseCode == 200) {
    serverReachable = true;
    parseServerReply(http.getString());
  } else {
    serverReachable = false;
    Serial.print("HTTP transmission failed: ");
    Serial.println(responseCode > 0 ? String(responseCode) : http.errorToString(responseCode));
  }
  http.end();
}

// =====================================================
// BLUETOOTH LE (technician phone)
// =====================================================

void setupBLE() {
  NimBLEDevice::init(bleName.c_str());
  bleServer = NimBLEDevice::createServer();
  NimBLEService* svc = bleServer->createService(BLE_SERVICE_UUID);
  bleData = svc->createCharacteristic(BLE_DATA_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
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
  if (bleServer->getConnectedCount() > 0) bleData->notify();
}

// =====================================================
// SERIAL MONITOR DISPLAY FUNCTION
// =====================================================

void displaySensorData() {
  Serial.println();
  Serial.println("================================");
  Serial.printf("Device: %s   WiFi: %s   BLE clients: %d\n", deviceId.c_str(),
                WiFi.status() == WL_CONNECTED ? "up" : "down", bleServer ? bleServer->getConnectedCount() : 0);
  Serial.printf("Flow Rate: %.3f L/min   Total Volume: %.3f L\n", flowRate_Lmin, totalVolume_L);
  Serial.printf("Pump Current: %.3f A (ADC %d)\n", pumpCurrent_A, currentADC);
  Serial.printf("Turbidity: %.3f V (ADC %d)\n", turbidityVoltage, turbidityADC);
  Serial.printf("TDS in/out: %.0f / %.1f ppm   Pressure: %.2f bar   Temp: %.1f C\n", tdsIn_ppm, tdsOut_ppm, pressure_bar, waterTemp_C);
  if (overlay.clog || overlay.pump || overlay.membrane || overlay.turbidity || overlay.sensor != "none")
    Serial.printf("Active condition (rev %d) from server\n", overlay.rev);
  Serial.println("================================");
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
    applyOverlayAndFabricate(elapsed / 1000.0);

    displaySensorData();
    notifyBLE();          // phone on site gets it even with no Wi-Fi
    sendDataWiFi(++seq);  // company server
  }
}
