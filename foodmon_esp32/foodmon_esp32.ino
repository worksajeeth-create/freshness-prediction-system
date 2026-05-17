/*
 * =====================================================================
 * FoodMon ESP32 Firmware  v5  — light on dedicated topic, no timer
 * Intelligent Food Freshness Monitoring & Control System
 * =====================================================================
 *
 * KEY CHANGE vs v4:
 *   Light is controlled via a DEDICATED MQTT topic:
 *       foodmon/control/light
 *   Payload: {"light": true}  or  {"light": false}
 *
 *   This topic handler does ONE thing only:
 *       drive PIN_RELAY and update lightState.
 *   No timer. No guards. No interaction with other actuators.
 *
 *   foodmon/control/actuators still handles cooler / ventilation /
 *   humidifier / buzzer with the 60-second timer as before.
 *   Light keys in that topic are IGNORED so there is zero interference.
 *
 * =====================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <HardwareSerial.h>

// ─────────────────────────────────────────────────────────────────────
//  USER CONFIG
// ─────────────────────────────────────────────────────────────────────
#define WIFI_SSID        "Dialog 4G 706"
#define WIFI_PASSWORD    "C77B5962"
#define MQTT_BROKER      "192.168.8.200"
#define MQTT_PORT         1883
#define MQTT_CLIENT_ID    "FoodMon_ESP32"
#define MQTT_USER         ""
#define MQTT_PASS         ""
#define DEVICE_ID         "foodmon_01"

// ─── Timing ──────────────────────────────────────────────────────────
#define SENSOR_PUBLISH_MS     2000UL
#define STATUS_PUBLISH_MS    10000UL
#define MQTT_RETRY_MS         5000UL
#define ACTUATOR_TIMEOUT_MS  60000UL
#define PELTIER_FAN_PRE_MS    1500UL
#define ADC_SAMPLES              10

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
#define PIN_BLOWER    26
#define PIN_MIST      25
#define PIN_BUZZER    23
#define PIN_PELTIER   19
#define PIN_COOL_FAN  18
#define PIN_RELAY      5   // active-LOW: LOW = relay ON = light ON

#define LEDC_FREQ_HZ   1000
#define LEDC_BITS      8

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
//  STATE
// ─────────────────────────────────────────────────────────────────────

// Timed actuators (cooler / ventilation / humidifier / buzzer)
struct ActuatorState {
  bool   cooler      = false;
  String ventilation = "OFF";
  bool   humidifier  = false;
  bool   buzzer      = false;
};
ActuatorState currentAct;

// Light — completely independent, no timer
bool lightState = false;

// 60-second timer (timed actuators only)
unsigned long actuatorStartedAt   = 0;
bool          actuatorTimerActive = false;

// Peltier sequencer
bool          peltierWanted   = false;
unsigned long fanPreStartedAt = 0;

bool          sessionRunning  = false;
unsigned long lastSensorPub   = 0;
unsigned long lastStatusPub   = 0;
unsigned long lastMqttAttempt = 0;

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
void safeOffTimedActuators();
void hardwareSetCooler(bool on);
void hardwareSetVentilation(const String& level);
void hardwareSetHumidifier(bool on);
void hardwareSetLight(bool on);
void hardwareSetBuzzer(bool on);
float readMQVoltage(int pin);
float mq2_to_ppm(float v);   float mq3_to_ppm(float v);
float mq4_to_ppm(float v);   float mq135_to_ppm(float v);
float mq136_to_ppm(float v); float mq137_to_ppm(float v);
int   readCO2();

// ─────────────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println(F("\n[FoodMon] Booting v5..."));

  // Timed actuator pins
  const int timedPins[] = {PIN_MIST, PIN_BUZZER, PIN_PELTIER, PIN_COOL_FAN};
  for (int p : timedPins) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }

  // Relay — HIGH on boot = light OFF
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, HIGH);
  lightState = false;

  // Blower PWM
  ledcAttach(PIN_BLOWER, LEDC_FREQ_HZ, LEDC_BITS);
  ledcWrite(PIN_BLOWER, 0);

  dhtStorage.begin();
  dhtSensor.begin();
  co2Serial.begin(9600, SERIAL_8N1, PIN_CO2_RX, PIN_CO2_TX);
  analogReadResolution(12);

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

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[WiFi] Lost — reconnecting..."));
    connectWifi();
  }

  if (!mqtt.connected()) {
    if (now - lastMqttAttempt >= MQTT_RETRY_MS) {
      lastMqttAttempt = now;
      connectMqtt();
    }
  }
  mqtt.loop();

  if (now - lastSensorPub >= SENSOR_PUBLISH_MS) {
    lastSensorPub = now;
    publishSensors();
  }

  if (now - lastStatusPub >= STATUS_PUBLISH_MS) {
    lastStatusPub = now;
    publishDeviceStatus("online");
  }

  // 60-second auto-off for timed actuators only — light not affected
  if (actuatorTimerActive) {
    unsigned long fresh   = millis();
    unsigned long elapsed = fresh - actuatorStartedAt;
    if (elapsed >= ACTUATOR_TIMEOUT_MS && elapsed >= 5000UL) {
      Serial.println(F("[ACT] 60s elapsed — timed actuators OFF. Light unchanged."));
      safeOffTimedActuators();
      publishActuatorStatus();
    }
  }

  // Peltier delayed-start
  if (peltierWanted) {
    if (millis() - fanPreStartedAt >= PELTIER_FAN_PRE_MS) {
      digitalWrite(PIN_PELTIER, HIGH);
      currentAct.cooler = true;
      peltierWanted     = false;
      Serial.println(F("[ACT] Peltier ON"));
      publishActuatorStatus();
    }
  }
}

// ─────────────────────────────────────────────────────────────────────
//  WIFI / MQTT
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
    mqtt.subscribe("foodmon/control/actuators");  // timed actuators
    mqtt.subscribe("foodmon/control/light");      // light — no timer
    Serial.println(F("[MQTT] Subscribed to all topics incl. foodmon/control/light"));
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
    publishDeviceStatus("online");
    return;
  }

  // ── Session stop — timed actuators off, light unchanged ───────────
  if (strcmp(topic, "foodmon/control/stop") == 0) {
    sessionRunning = false;
    safeOffTimedActuators();
    actuatorTimerActive = false;
    publishActuatorStatus();
    publishDeviceStatus("online");
    return;
  }

  // ─────────────────────────────────────────────────────────────────
  //  LIGHT — dedicated topic, plain on/off, zero timer logic
  // ─────────────────────────────────────────────────────────────────
  if (strcmp(topic, "foodmon/control/light") == 0) {
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, buf)) {
      Serial.println(F("[LIGHT] Bad JSON — ignored."));
      return;
    }
    if (!doc.containsKey("light")) {
      Serial.println(F("[LIGHT] No 'light' key — ignored."));
      return;
    }
    bool on = doc["light"].as<bool>();
    hardwareSetLight(on);      // drives relay immediately
    publishActuatorStatus();   // report back to Pi
    return;
  }

  // ─────────────────────────────────────────────────────────────────
  //  TIMED ACTUATORS — cooler / ventilation / humidifier / buzzer
  //  Light keys in this payload are silently ignored.
  // ─────────────────────────────────────────────────────────────────
  if (strcmp(topic, "foodmon/control/actuators") == 0) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, buf)) {
      Serial.println(F("[ACT] Bad JSON — ignored."));
      return;
    }

    bool isManual = doc.containsKey("manual") && doc["manual"].as<bool>();

    if (actuatorTimerActive && !isManual) {
      unsigned long remaining = (ACTUATOR_TIMEOUT_MS - (millis() - actuatorStartedAt)) / 1000UL;
      Serial.printf("[ACT] ML command ignored — manual timer active (%lu s left)\n", remaining);
      return;
    }

    ActuatorState desired = currentAct;
    if (doc.containsKey("cooler"))      desired.cooler      = doc["cooler"].as<bool>();
    if (doc.containsKey("humidifier"))  desired.humidifier  = doc["humidifier"].as<bool>();
    if (doc.containsKey("buzzer"))      desired.buzzer      = doc["buzzer"].as<bool>();
    if (doc.containsKey("ventilation")) desired.ventilation = doc["ventilation"].as<String>();
    // "light" key intentionally not read here — use foodmon/control/light instead

    bool anyActive = desired.cooler
                  || desired.humidifier
                  || desired.buzzer
                  || (desired.ventilation != "OFF");

    applyActuatorState(desired);

    if (anyActive) {
      actuatorStartedAt   = millis();
      actuatorTimerActive = true;
      Serial.printf("[ACT] Timer started — auto-off in %lu s\n", ACTUATOR_TIMEOUT_MS / 1000UL);
    } else {
      actuatorTimerActive = false;
      Serial.println(F("[ACT] All timed actuators OFF — timer cancelled."));
    }

    publishActuatorStatus();
    return;
  }
}

// ─────────────────────────────────────────────────────────────────────
//  ACTUATOR HELPERS
// ─────────────────────────────────────────────────────────────────────
void applyActuatorState(const ActuatorState& desired) {
  if (desired.cooler      != currentAct.cooler)      hardwareSetCooler(desired.cooler);
  if (desired.ventilation != currentAct.ventilation) hardwareSetVentilation(desired.ventilation);
  if (desired.humidifier  != currentAct.humidifier)  hardwareSetHumidifier(desired.humidifier);
  if (desired.buzzer      != currentAct.buzzer)      hardwareSetBuzzer(desired.buzzer);
}

// Shuts down only the timed actuators. PIN_RELAY is never touched here.
void safeOffTimedActuators() {
  peltierWanted = false;
  digitalWrite(PIN_PELTIER,  LOW);
  digitalWrite(PIN_COOL_FAN, LOW);
  ledcWrite(PIN_BLOWER, 0);
  digitalWrite(PIN_MIST,   LOW);
  digitalWrite(PIN_BUZZER, LOW);
  currentAct          = ActuatorState();
  actuatorTimerActive = false;
}

void hardwareSetCooler(bool on) {
  if (on) {
    digitalWrite(PIN_COOL_FAN, HIGH);
    fanPreStartedAt = millis();
    peltierWanted   = true;
    Serial.println(F("[ACT] Cooler: fan pre-delay started."));
  } else {
    peltierWanted = false;
    digitalWrite(PIN_PELTIER,  LOW);
    digitalWrite(PIN_COOL_FAN, LOW);
    currentAct.cooler = false;
    Serial.println(F("[ACT] Cooler OFF."));
  }
}

void hardwareSetVentilation(const String& level) {
  uint32_t duty = 0;
  if      (level == "LOW")    duty = 76;
  else if (level == "MEDIUM") duty = 165;
  else if (level == "HIGH")   duty = 255;
  ledcWrite(PIN_BLOWER, duty);
  currentAct.ventilation = level;
  Serial.printf("[ACT] Ventilation %s\n", level.c_str());
}

void hardwareSetHumidifier(bool on) {
  digitalWrite(PIN_MIST, on ? HIGH : LOW);
  currentAct.humidifier = on;
  Serial.printf("[ACT] Humidifier %s\n", on ? "ON" : "OFF");
}

// This is the entire light control logic — just flip the relay.
void hardwareSetLight(bool on) {
  digitalWrite(PIN_RELAY, on ? LOW : HIGH);  // active-LOW relay
  lightState = on;
  Serial.printf("[LIGHT] %s\n", on ? "ON" : "OFF");
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
}

// ─────────────────────────────────────────────────────────────────────
//  PUBLISHING
// ─────────────────────────────────────────────────────────────────────
void publishSensors() {
  if (!mqtt.connected()) return;
  unsigned long ts = millis() / 1000;

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
    d["value"]     = roundf(s.fn(v)*10.f)/10.f;
    d["unit"]      = "ppm";
    d["voltage"]   = roundf(v*1000.f)/1000.f;
    d["timestamp"] = ts;
    publishJson(s.topic, d);
  }

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
  d["buzzer"]               = currentAct.buzzer;
  d["light"]                = lightState;   // always the true relay state
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
//  ADC / MQ
// ─────────────────────────────────────────────────────────────────────
float readMQVoltage(int pin) {
  long sum = 0;
  for (int i = 0; i < ADC_SAMPLES; i++) { sum += analogRead(pin); delayMicroseconds(200); }
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
  while (co2Serial.available()) co2Serial.read();
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
