/*
 * =====================================================================
 * FoodMon ESP32 Firmware  v3  — fixed timed actuation
 * Intelligent Food Freshness Monitoring & Control System
 * =====================================================================
 *
 * HARDWARE CONNECTIONS:
 *
 * === GAS SENSORS (10kΩ / 20kΩ voltage divider → ESP32 ADC) ===
 *   MQ-2   AOUT → GPIO33
 *   MQ-3   AOUT → GPIO32
 *   MQ-4   AOUT → GPIO35
 *   MQ-135 AOUT → GPIO34
 *   MQ-136 AOUT → GPIO39  (input-only, no pull-up)
 *   MQ-137 AOUT → GPIO36  (input-only, no pull-up)
 *   All MQ VCC → 5V_SENSOR rail,  GND → Common GND
 *
 * === CO2 SENSOR — MH-Z19C (UART2) ===
 *   TX → ESP32 GPIO16 (RX2)
 *   RX → ESP32 GPIO17 (TX2)
 *   VCC → 5V_SENSOR,  GND → Common GND
 *
 * === TEMPERATURE / HUMIDITY ===
 *   AM2301 DATA → GPIO27  (food storage chamber; 3.3V + 10kΩ pull-up)
 *   DHT22  DATA → GPIO14  (MQ sensor chamber;   3.3V + 10kΩ pull-up)
 *
 * === ACTUATORS (low-side N-MOSFET; Gate 220Ω from GPIO, 10kΩ to GND) ===
 *   5V  Blower fan  (ventilation) → GPIO26  — PWM via LEDC channel 0
 *   5V  Mist maker  (humidifier)  → GPIO25
 *   5V  Buzzer      (alert)       → GPIO23
 *   12V Peltier cooler            → GPIO19
 *   12V Cooling fans (×2 par.)    → GPIO18
 *
 * === LIGHT (5V relay module — active-LOW IN) ===
 *   Relay IN → GPIO5
 *   HIGH = relay OFF (light OFF);  LOW = relay ON (light ON)
 *
 * =====================================================================
 * TIMED ACTUATION BEHAVIOUR
 *
 *   Every incoming actuator command starts / resets a 60-second countdown.
 *   When the timer expires with no new command, ALL actuators are forced
 *   safe-off automatically.
 *
 *   A session-stop command or a command with all actuators OFF also
 *   forces immediate safe-off and cancels the timer.
 *
 *   The remaining time is included in every published actuator status
 *   message so the Raspberry Pi dashboard can display a countdown.
 *
 * =====================================================================
 * MQTT TOPICS — publishes
 *   foodmon/sensors/environmental/storage/temperature
 *   foodmon/sensors/environmental/storage/humidity
 *   foodmon/sensors/environmental/sensor_chamber/temperature
 *   foodmon/sensors/environmental/sensor_chamber/humidity
 *   foodmon/sensors/gas/mq2  … mq137  co2
 *   foodmon/device/status
 *   foodmon/actuators/status    <- current state + timer countdown
 *
 * MQTT TOPICS — subscribes
 *   foodmon/control/start
 *   foodmon/control/stop
 *   foodmon/control/ping
 *   foodmon/control/actuators
 *
 * =====================================================================
 * LIBRARY DEPENDENCIES (Arduino Library Manager)
 *   - "DHT sensor library" by Adafruit  (>= 1.4.4)  ← install THIS one
 *     If "DHT_kxn" is also installed, DELETE it from
 *     Documents\Arduino\libraries\DHT_kxn  to avoid the conflict warning.
 *   - Adafruit Unified Sensor  (required by Adafruit DHT lib)
 *   - PubSubClient  by Nick O'Leary  (>= 2.8)
 *   - ArduinoJson   by Benoit Blanchon  (>= 6.21, < 7)
 *   Board: "ESP32 Dev Module"  —  Espressif ESP32 core v3.x
 * =====================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <HardwareSerial.h>

// ─────────────────────────────────────────────────────────────────────
//  USER CONFIG  — edit before flashing
// ─────────────────────────────────────────────────────────────────────
#define WIFI_SSID        "Dialog 4G 247"
#define WIFI_PASSWORD    "5E2761e3"
#define MQTT_BROKER      "192.168.8.200"   // Raspberry Pi IP address
#define MQTT_PORT         1883
#define MQTT_CLIENT_ID    "FoodMon_ESP32"
#define MQTT_USER         ""                // leave blank if unused
#define MQTT_PASS         ""                // leave blank if unused
#define DEVICE_ID         "foodmon_01"

// ─── Timing ──────────────────────────────────────────────────────────
#define SENSOR_PUBLISH_MS     2000UL   // how often to send sensor data
#define STATUS_PUBLISH_MS    10000UL   // device heartbeat
#define MQTT_RETRY_MS         5000UL   // reconnect retry interval
#define ACTUATOR_TIMEOUT_MS  60000UL   // 60-second auto-off window
#define PELTIER_FAN_PRE_MS    1500UL   // fans must spin before Peltier on
#define ADC_SAMPLES              10    // ADC readings averaged per MQ sensor

// ─── Pins ─────────────────────────────────────────────────────────────
#define PIN_MQ2     33
#define PIN_MQ3     32
#define PIN_MQ4     35
#define PIN_MQ135   34
#define PIN_MQ136   39
#define PIN_MQ137   36

#define PIN_AM2301  27
#define PIN_DHT22   14

#define PIN_CO2_RX  16
#define PIN_CO2_TX  17

#define PIN_BLOWER    26   // 5V blower — PWM (LEDC ch 0)
#define PIN_MIST      25   // 5V mist maker
#define PIN_BUZZER    23   // 5V buzzer
#define PIN_PELTIER   19   // 12V Peltier
#define PIN_COOL_FAN  18   // 12V cooling fans
#define PIN_RELAY      5   // light relay (active-LOW)

// ─── LEDC blower PWM ─────────────────────────────────────────────────
// ESP32 Arduino core v3.x uses ledcAttach(pin, freq, bits) — no channel arg.
// ledcWrite(pin, duty) is also pin-based in v3.x.
#define LEDC_FREQ_HZ   1000
#define LEDC_BITS      8        // 0–255 duty

// ─────────────────────────────────────────────────────────────────────
//  OBJECTS
// ─────────────────────────────────────────────────────────────────────
DHT            dhtStorage(PIN_AM2301, AM2301);
DHT            dhtSensor (PIN_DHT22,  DHT22);
HardwareSerial co2Serial (2);
static const byte MHZ19_CMD[9] = {0xFF,0x01,0x86,0x00,0x00,0x00,0x00,0x00,0x79};

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ─────────────────────────────────────────────────────────────────────
//  ACTUATOR STATE
//  currentAct reflects what is physically applied to the hardware.
//  It is updated inside each hardwareSet*() function so the rest of the
//  code always sees the true hardware state.
// ─────────────────────────────────────────────────────────────────────
struct ActuatorState {
  bool   cooler      = false;
  String ventilation = "OFF";  // OFF | LOW | MEDIUM | HIGH
  bool   humidifier  = false;
  bool   light       = false;
  bool   buzzer      = false;
};
ActuatorState currentAct;

// ─── 60-second timer ─────────────────────────────────────────────────
unsigned long actuatorStartedAt   = 0;
bool          actuatorTimerActive = false;

// ─── Peltier safety sequencer ────────────────────────────────────────
bool          peltierWanted    = false;
unsigned long fanPreStartedAt  = 0;

// ─── Misc ─────────────────────────────────────────────────────────────
bool          sessionRunning   = false;
unsigned long lastSensorPub    = 0;
unsigned long lastStatusPub    = 0;
unsigned long lastMqttAttempt  = 0;

// ─────────────────────────────────────────────────────────────────────
//  FORWARD DECLARATIONS
// ─────────────────────────────────────────────────────────────────────
void connectWifi();
void connectMqtt();
void mqttCallback(char* topic, byte* payload, unsigned int len);
void publishSensors();
void publishDeviceStatus(const char* status);
void publishActuatorStatus();
void publishJson(const char* topic, JsonDocument& doc);

void applyActuatorState(const ActuatorState& desired);
void safeOffAll();
void hardwareSetCooler(bool on);
void hardwareSetVentilation(const String& level);
void hardwareSetHumidifier(bool on);
void hardwareSetLight(bool on);
void hardwareSetBuzzer(bool on);

float readMQVoltage(int pin);
float mq2_to_ppm  (float v);
float mq3_to_ppm  (float v);
float mq4_to_ppm  (float v);
float mq135_to_ppm(float v);
float mq136_to_ppm(float v);
float mq137_to_ppm(float v);
int   readCO2();

// ─────────────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println(F("\n[FoodMon] Booting v3..."));

  // All digital actuator pins: safe LOW first
  const int digitalPins[] = {PIN_MIST, PIN_BUZZER, PIN_PELTIER, PIN_COOL_FAN};
  for (int p : digitalPins) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }

  // Relay: HIGH on boot = relay OFF = light OFF
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, HIGH);

  // Blower PWM via LEDC — ESP32 core v3.x API (single call, pin-based)
  ledcAttach(PIN_BLOWER, LEDC_FREQ_HZ, LEDC_BITS);
  ledcWrite(PIN_BLOWER, 0);   // start at 0 duty = blower OFF

  // Sensors
  dhtStorage.begin();
  dhtSensor.begin();
  co2Serial.begin(9600, SERIAL_8N1, PIN_CO2_RX, PIN_CO2_TX);
  analogReadResolution(12);

  // Network
  connectWifi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  connectMqtt();

  Serial.println(F("[FoodMon] Ready."));
  publishDeviceStatus("online");
}

// ─────────────────────────────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[WiFi] Lost — reconnecting..."));
    connectWifi();
  }

  // MQTT watchdog
  if (!mqtt.connected()) {
    if (now - lastMqttAttempt >= MQTT_RETRY_MS) {
      lastMqttAttempt = now;
      connectMqtt();
    }
  }
  mqtt.loop();

  // Sensor publish
  if (now - lastSensorPub >= SENSOR_PUBLISH_MS) {
    lastSensorPub = now;
    publishSensors();
  }

  // Heartbeat
  if (now - lastStatusPub >= STATUS_PUBLISH_MS) {
    lastStatusPub = now;
    publishDeviceStatus("online");
  }

  // ── 60-second actuator auto-off ───────────────────────────────────
  // Use a fresh millis() here — NOT the 'now' captured at the top of
  // loop() — because mqtt.loop() above may have called mqttCallback()
  // which set actuatorStartedAt = millis().  Using the stale 'now'
  // would make the elapsed calculation instantly >= ACTUATOR_TIMEOUT_MS
  // and fire safe-off the moment a new command arrives.
  // The minimum 5 s guard is an extra safety net.
  if (actuatorTimerActive) {
    unsigned long fresh     = millis();
    unsigned long elapsed   = fresh - actuatorStartedAt;
    if (elapsed >= ACTUATOR_TIMEOUT_MS && elapsed >= 5000UL) {
      Serial.println(F("[ACT] *** 60 s elapsed — AUTO SAFE-OFF ***"));
      safeOffAll();              // also sets actuatorTimerActive = false
      publishActuatorStatus();
    }
  }

  // ── Peltier delayed-start sequencer ──────────────────────────────
  // Use fresh millis() — fanPreStartedAt is set inside mqttCallback
  // which runs during mqtt.loop() above, so 'now' captured at the top
  // of loop() is older than fanPreStartedAt and would cause unsigned
  // underflow (wrapping to ~4 billion >= PELTIER_FAN_PRE_MS instantly).
  if (peltierWanted) {
    unsigned long freshNow = millis();
    if (freshNow - fanPreStartedAt >= PELTIER_FAN_PRE_MS) {
      digitalWrite(PIN_PELTIER, HIGH);
      currentAct.cooler = true;
      peltierWanted     = false;
      Serial.println(F("[ACT] Peltier ON (fan pre-delay complete)"));
      publishActuatorStatus();
    }
  }
}

// ─────────────────────────────────────────────────────────────────────
//  WIFI
// ─────────────────────────────────────────────────────────────────────
void connectWifi() {
  Serial.printf("[WiFi] Connecting to '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int t = 0; t < 40 && WiFi.status() != WL_CONNECTED; t++) {
    delay(500); Serial.print('.');
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("\n[WiFi] Connected — %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println(F("\n[WiFi] Failed — will retry."));
}

// ─────────────────────────────────────────────────────────────────────
//  MQTT CONNECT
// ─────────────────────────────────────────────────────────────────────
void connectMqtt() {
  if (mqtt.connected()) return;
  Serial.printf("[MQTT] Connecting to %s:%d ...", MQTT_BROKER, MQTT_PORT);
  bool ok = strlen(MQTT_USER) > 0
            ? mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)
            : mqtt.connect(MQTT_CLIENT_ID);
  if (ok) {
    Serial.println(F(" OK"));
    mqtt.subscribe("foodmon/control/start");
    mqtt.subscribe("foodmon/control/stop");
    mqtt.subscribe("foodmon/control/ping");
    mqtt.subscribe("foodmon/control/actuators");
  } else {
    Serial.printf(" FAILED rc=%d\n", mqtt.state());
  }
}

// ─────────────────────────────────────────────────────────────────────
//  MQTT CALLBACK
// ─────────────────────────────────────────────────────────────────────
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  char buf[512];
  len = min(len, (unsigned int)511);
  memcpy(buf, payload, len);
  buf[len] = '\0';
  Serial.printf("[MQTT] <- %s : %s\n", topic, buf);

  // ── Ping ──────────────────────────────────────────────────────────
  if (strcmp(topic, "foodmon/control/ping") == 0) {
    publishDeviceStatus("online");
    return;
  }

  // ── Session start ─────────────────────────────────────────────────
  if (strcmp(topic, "foodmon/control/start") == 0) {
    sessionRunning = true;
    Serial.println(F("[SESSION] Started."));
    publishDeviceStatus("online");
    return;
  }

  // ── Session stop — immediate safe-off, cancel timer ───────────────
  if (strcmp(topic, "foodmon/control/stop") == 0) {
    sessionRunning = false;
    safeOffAll();
    actuatorTimerActive = false;
    Serial.println(F("[SESSION] Stopped — safe-off done."));
    publishActuatorStatus();
    publishDeviceStatus("online");
    return;
  }

  // ── Actuator command ──────────────────────────────────────────────
  if (strcmp(topic, "foodmon/control/actuators") == 0) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, buf)) {
      Serial.println(F("[ACT] Bad JSON — ignored."));
      return;
    }

    // Distinguish manual (dashboard) commands from ML-driven commands.
    // Manual commands carry  "manual": true  in the payload.
    // ML commands do NOT carry this field.
    bool isManual = doc.containsKey("manual") && doc["manual"].as<bool>();

    // ── Guard: if timer is running AND this is NOT a manual command,
    //    ignore it completely.  This prevents the ML controller on the
    //    Pi from resetting or cancelling the manual 60-second window.
    if (actuatorTimerActive && !isManual) {
      unsigned long remaining = (ACTUATOR_TIMEOUT_MS - (millis() - actuatorStartedAt)) / 1000UL;
      Serial.printf("[ACT] ML command ignored — manual timer active (%lu s left)\n", remaining);
      return;
    }

    // Build desired state from current hardware state + incoming deltas
    ActuatorState desired = currentAct;
    if (doc.containsKey("cooler"))      desired.cooler      = doc["cooler"].as<bool>();
    if (doc.containsKey("humidifier"))  desired.humidifier  = doc["humidifier"].as<bool>();
    if (doc.containsKey("light"))       desired.light       = doc["light"].as<bool>();
    if (doc.containsKey("buzzer"))      desired.buzzer      = doc["buzzer"].as<bool>();
    if (doc.containsKey("ventilation")) desired.ventilation = doc["ventilation"].as<String>();

    bool anyActive = desired.cooler
                  || desired.humidifier
                  || desired.light
                  || desired.buzzer
                  || (desired.ventilation != "OFF");

    // Apply hardware changes
    applyActuatorState(desired);

    // Manage timer — record timestamp AFTER applyActuatorState so that
    // the Peltier sequencer's fanPreStartedAt is also set after this.
    if (anyActive) {
      actuatorStartedAt   = millis();  // fresh timestamp — no stale 'now'
      actuatorTimerActive = true;
      Serial.printf("[ACT] Timer started — auto-off in %lu s\n",
                    ACTUATOR_TIMEOUT_MS / 1000UL);
    } else {
      // Explicit all-off: cancel timer
      actuatorTimerActive = false;
      Serial.println(F("[ACT] All OFF — timer cancelled."));
    }

    publishActuatorStatus();
    return;
  }
}

// ─────────────────────────────────────────────────────────────────────
//  ACTUATOR CONTROL FUNCTIONS
// ─────────────────────────────────────────────────────────────────────

/*
 * applyActuatorState()
 * Diffs desired against currentAct and calls only the hardware setters
 * that need to change.  Each setter updates currentAct internally.
 */
void applyActuatorState(const ActuatorState& desired) {
  if (desired.cooler      != currentAct.cooler)      hardwareSetCooler(desired.cooler);
  if (desired.ventilation != currentAct.ventilation) hardwareSetVentilation(desired.ventilation);
  if (desired.humidifier  != currentAct.humidifier)  hardwareSetHumidifier(desired.humidifier);
  if (desired.light       != currentAct.light)       hardwareSetLight(desired.light);
  if (desired.buzzer      != currentAct.buzzer)      hardwareSetBuzzer(desired.buzzer);
}

/*
 * safeOffAll()
 * Immediately cuts every actuator and resets currentAct.
 * Called on timeout, session stop, or explicit all-off command.
 */
void safeOffAll() {
  peltierWanted = false;         // cancel any pending Peltier start

  digitalWrite(PIN_PELTIER,  LOW);   // Peltier OFF first (thermal safety)
  digitalWrite(PIN_COOL_FAN, LOW);   // then cooling fans
  ledcWrite(PIN_BLOWER, 0);          // blower OFF
  digitalWrite(PIN_MIST,     LOW);
  digitalWrite(PIN_BUZZER,   LOW);
  digitalWrite(PIN_RELAY,   HIGH);   // active-LOW: HIGH = light OFF

  currentAct          = ActuatorState(); // reset struct to all-off defaults
  actuatorTimerActive = false;           // CRITICAL: prevent stale timer re-firing
                                         // in the same loop() pass after a new command
  Serial.println(F("[ACT] Safe-off complete — all actuators OFF."));
}

// ── Individual hardware setters ──────────────────────────────────────
// Each updates currentAct after applying the physical change.

void hardwareSetCooler(bool on) {
  if (on) {
    // Start fan pre-delay sequence; Peltier is turned on by loop()
    // after PELTIER_FAN_PRE_MS has elapsed.
    digitalWrite(PIN_COOL_FAN, HIGH);
    fanPreStartedAt = millis();
    peltierWanted   = true;
    // currentAct.cooler stays false until Peltier is actually energised
    Serial.println(F("[ACT] Cooler ON requested — fan pre-delay started."));
  } else {
    peltierWanted = false;
    digitalWrite(PIN_PELTIER,  LOW);
    digitalWrite(PIN_COOL_FAN, LOW);
    currentAct.cooler = false;
    Serial.println(F("[ACT] Cooler OFF (Peltier + cooling fans)."));
  }
}

void hardwareSetVentilation(const String& level) {
  uint32_t duty = 0;
  if      (level == "LOW")    duty = 76;   // ~30 % of 255
  else if (level == "MEDIUM") duty = 165;  // ~65 %
  else if (level == "HIGH")   duty = 255;  // 100 %

  ledcWrite(PIN_BLOWER, duty);
  currentAct.ventilation = level;
  Serial.printf("[ACT] Ventilation %s (duty=%d/255)\n", level.c_str(), (int)duty);
}

void hardwareSetHumidifier(bool on) {
  digitalWrite(PIN_MIST, on ? HIGH : LOW);
  currentAct.humidifier = on;
  Serial.printf("[ACT] Humidifier %s\n", on ? "ON" : "OFF");
}

void hardwareSetLight(bool on) {
  // Active-LOW relay: LOW energises relay = light ON
  digitalWrite(PIN_RELAY, on ? LOW : HIGH);
  currentAct.light = on;
  Serial.printf("[ACT] Light %s\n", on ? "ON" : "OFF");
}

void hardwareSetBuzzer(bool on) {
  if (on) {
    for (int i = 0; i < 3; i++) {
      digitalWrite(PIN_BUZZER, HIGH); delay(200);
      digitalWrite(PIN_BUZZER, LOW);
      if (i < 2) delay(150);
    }
  } else {
    digitalWrite(PIN_BUZZER, LOW);
  }
  currentAct.buzzer = on;
  Serial.printf("[ACT] Buzzer %s\n", on ? "ON (beeped x3)" : "OFF");
}

// ─────────────────────────────────────────────────────────────────────
//  SENSOR PUBLISHING
// ─────────────────────────────────────────────────────────────────────
void publishSensors() {
  if (!mqtt.connected()) return;
  unsigned long ts = millis() / 1000;

  // Storage chamber — AM2301 (GPIO27)
  {
    float t = dhtStorage.readTemperature();
    float h = dhtStorage.readHumidity();
    if (!isnan(t)) {
      StaticJsonDocument<128> d;
      d["value"] = roundf(t*10.f)/10.f; d["unit"] = "C"; d["timestamp"] = ts;
      publishJson("foodmon/sensors/environmental/storage/temperature", d);
    }
    if (!isnan(h)) {
      StaticJsonDocument<128> d;
      d["value"] = roundf(h*10.f)/10.f; d["unit"] = "%"; d["timestamp"] = ts;
      publishJson("foodmon/sensors/environmental/storage/humidity", d);
    }
  }

  // Sensor chamber — DHT22 (GPIO14)
  {
    float t = dhtSensor.readTemperature();
    float h = dhtSensor.readHumidity();
    if (!isnan(t)) {
      StaticJsonDocument<128> d;
      d["value"] = roundf(t*10.f)/10.f; d["unit"] = "C"; d["timestamp"] = ts;
      publishJson("foodmon/sensors/environmental/sensor_chamber/temperature", d);
    }
    if (!isnan(h)) {
      StaticJsonDocument<128> d;
      d["value"] = roundf(h*10.f)/10.f; d["unit"] = "%"; d["timestamp"] = ts;
      publishJson("foodmon/sensors/environmental/sensor_chamber/humidity", d);
    }
  }

  // MQ gas sensors
  struct GS { const char* topic; int pin; float (*fn)(float); };
  static const GS gs[] = {
    {"foodmon/sensors/gas/mq2",   PIN_MQ2,   mq2_to_ppm  },
    {"foodmon/sensors/gas/mq3",   PIN_MQ3,   mq3_to_ppm  },
    {"foodmon/sensors/gas/mq4",   PIN_MQ4,   mq4_to_ppm  },
    {"foodmon/sensors/gas/mq135", PIN_MQ135, mq135_to_ppm},
    {"foodmon/sensors/gas/mq136", PIN_MQ136, mq136_to_ppm},
    {"foodmon/sensors/gas/mq137", PIN_MQ137, mq137_to_ppm},
  };
  for (const auto& s : gs) {
    float v = readMQVoltage(s.pin);
    StaticJsonDocument<160> d;
    d["value"]   = roundf(s.fn(v)*10.f)/10.f;
    d["unit"]    = "ppm";
    d["voltage"] = roundf(v*1000.f)/1000.f;
    d["timestamp"] = ts;
    publishJson(s.topic, d);
  }

  // CO2
  int co2 = readCO2();
  if (co2 > 0) {
    StaticJsonDocument<128> d;
    d["value"] = co2; d["unit"] = "ppm"; d["timestamp"] = ts;
    publishJson("foodmon/sensors/gas/co2", d);
  }
}

void publishDeviceStatus(const char* status) {
  if (!mqtt.connected()) return;
  unsigned long elapsed   = millis() - actuatorStartedAt;
  unsigned long remaining = (actuatorTimerActive && elapsed < ACTUATOR_TIMEOUT_MS)
                            ? (ACTUATOR_TIMEOUT_MS - elapsed) / 1000UL : 0;
  StaticJsonDocument<256> d;
  d["device_id"]            = DEVICE_ID;
  d["status"]               = status;
  d["session"]              = sessionRunning ? "running" : "idle";
  d["ip"]                   = WiFi.localIP().toString();
  d["actuator_timer_on"]    = actuatorTimerActive;
  d["actuator_remaining_s"] = remaining;
  d["timestamp"]            = millis() / 1000;
  publishJson("foodmon/device/status", d);
}

void publishActuatorStatus() {
  if (!mqtt.connected()) return;
  unsigned long elapsed   = millis() - actuatorStartedAt;
  unsigned long remaining = (actuatorTimerActive && elapsed < ACTUATOR_TIMEOUT_MS)
                            ? (ACTUATOR_TIMEOUT_MS - elapsed) / 1000UL : 0;
  StaticJsonDocument<256> d;
  d["cooler"]               = currentAct.cooler;
  d["ventilation"]          = currentAct.ventilation;
  d["humidifier"]           = currentAct.humidifier;
  d["light"]                = currentAct.light;
  d["buzzer"]               = currentAct.buzzer;
  d["actuator_timer_on"]    = actuatorTimerActive;
  d["actuator_remaining_s"] = remaining;
  d["timestamp"]            = millis() / 1000;
  publishJson("foodmon/actuators/status", d);
}

void publishJson(const char* topic, JsonDocument& doc) {
  char buf[320];
  serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(topic, buf, false);
}

// ─────────────────────────────────────────────────────────────────────
//  ADC / MQ HELPERS
// ─────────────────────────────────────────────────────────────────────
/*
 * Voltage divider: MQ_AOUT → 10kΩ → ADC_NODE → 20kΩ → GND
 *   Vadc = Vmq × 20/30 = Vmq × 0.667
 *   Vmq  = Vadc × 1.5   (reverse to get true MQ output voltage)
 */
float readMQVoltage(int pin) {
  long sum = 0;
  for (int i = 0; i < ADC_SAMPLES; i++) {
    sum += analogRead(pin);
    delayMicroseconds(200);
  }
  return (sum / (float)ADC_SAMPLES) * (3.3f / 4095.0f) * 1.5f;
}

float mq2_to_ppm  (float v){ return max(0.f, v/3.3f*10000.f); }
float mq3_to_ppm  (float v){ return max(0.f, v/3.3f*  500.f); }
float mq4_to_ppm  (float v){ return max(0.f, v/3.3f*10000.f); }
float mq135_to_ppm(float v){ return max(0.f, v/3.3f* 1000.f); }
float mq136_to_ppm(float v){ return max(0.f, v/3.3f*  200.f); }
float mq137_to_ppm(float v){ return max(0.f, v/3.3f*  300.f); }

// ─────────────────────────────────────────────────────────────────────
//  MH-Z19C CO2
// ─────────────────────────────────────────────────────────────────────
int readCO2() {
  while (co2Serial.available()) co2Serial.read();  // flush stale
  co2Serial.write(MHZ19_CMD, 9);
  co2Serial.flush();
  unsigned long t0 = millis();
  while (co2Serial.available() < 9) {
    if (millis() - t0 > 1200) return -1;
    delay(10);
  }
  byte r[9];
  co2Serial.readBytes(r, 9);
  byte csum = 0;
  for (int i = 1; i < 8; i++) csum += r[i];
  csum = 0xFF - csum + 1;
  if (csum != r[8]) return -2;
  return (r[2] << 8) | r[3];
}
