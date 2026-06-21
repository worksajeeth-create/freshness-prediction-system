"""Configuration for the Food Freshness Monitoring System."""
from __future__ import annotations

import os
from pathlib import Path

# ─── Project paths ────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR     = PROJECT_ROOT / "data"
MODEL_DIR    = PROJECT_ROOT / "models"

# ─── Flask ────────────────────────────────────────────────────────────
FLASK_HOST  = os.getenv("FOODMON_FLASK_HOST",  "0.0.0.0")
FLASK_PORT  = int(os.getenv("FOODMON_FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FOODMON_FLASK_DEBUG", "false").lower() == "true"
SECRET_KEY  = os.getenv("FOODMON_SECRET_KEY",  "foodmon_change_me")

# ─── MQTT ─────────────────────────────────────────────────────────────
MQTT_BROKER    = os.getenv("FOODMON_MQTT_BROKER",    "localhost")
MQTT_PORT_NUM  = int(os.getenv("FOODMON_MQTT_PORT",  "1883"))
MQTT_PORT      = MQTT_PORT_NUM
MQTT_KEEPALIVE = 60
MQTT_CLIENT_ID = os.getenv("FOODMON_MQTT_CLIENT_ID", "RaspberryPi_FoodMon")
MQTT_USERNAME  = os.getenv("FOODMON_MQTT_USERNAME",  "")
MQTT_PASSWORD  = os.getenv("FOODMON_MQTT_PASSWORD",  "")

MQTT_SENSOR_TOPICS = [
    ("foodmon/sensors/environmental/#", 1),
    ("foodmon/sensors/gas/#",           1),
    ("foodmon/device/status",           1),
]
MQTT_CONTROL_TOPICS = {
    "start": "foodmon/control/start",
    "stop":  "foodmon/control/stop",
    "ping":  "foodmon/control/ping",
}

# ─── Session / cloud ──────────────────────────────────────────────────
DEVICE_ID            = os.getenv("FOODMON_DEVICE_ID",          "foodmon_01")
CLOUD_PROVIDER       = os.getenv("FOODMON_CLOUD_PROVIDER",     "firebase")
FIREBASE_DB_URL      = os.getenv("FOODMON_FIREBASE_DB_URL",    "")
FIREBASE_AUTH        = os.getenv("FOODMON_FIREBASE_AUTH",      "")
GENERIC_CLOUD_BASE_URL = os.getenv("FOODMON_CLOUD_BASE_URL",   "")
GENERIC_CLOUD_API_KEY  = os.getenv("FOODMON_CLOUD_API_KEY",    "")
REQUEST_TIMEOUT      = 8
ENABLE_CLOUD_SYNC    = os.getenv("FOODMON_ENABLE_CLOUD_SYNC",  "true").lower() == "true"

# ─── Actuators ────────────────────────────────────────────────────────
ACTUATOR_CONTROL_MODE      = os.getenv("FOODMON_ACTUATOR_CONTROL_MODE", "esp").lower()

# Topic for cooler / ventilation / humidifier (60-second timer)
ESP_ACTUATOR_COMMAND_TOPIC = os.getenv(
    "FOODMON_ESP_ACTUATOR_COMMAND_TOPIC", "foodmon/control/actuators"
)

# Dedicated topic for the light relay — no timer, plain ON/OFF
ESP_LIGHT_COMMAND_TOPIC    = os.getenv(
    "FOODMON_ESP_LIGHT_COMMAND_TOPIC", "foodmon/control/light"
)

# Dedicated topic for the buzzer — fire-and-forget pulses, no timer,
# no persisted state. Two pulse types are sent on this topic:
#   {"type": "short"} -> single ~100ms beep, fired on any touchscreen
#                        button / tab / toggle press
#   {"type": "long"}  -> single ~800ms beep, fired once when the ML
#                        engine detects spoiled food
ESP_BUZZER_COMMAND_TOPIC = os.getenv(
    "FOODMON_ESP_BUZZER_COMMAND_TOPIC", "foodmon/control/buzzer"
)

# How long (seconds) timed actuators run after each command.
ACTUATOR_RUN_SECONDS = int(os.getenv("FOODMON_ACTUATOR_RUN_SECONDS", "60"))

ACTUATOR_PINS = {
    "cooler":    19,
    "cool_fan":  18,
    "ventilation": 26,
    "humidifier": 25,
    "buzzer":    23,
    "light":      5,
}

# ─── Sensors ──────────────────────────────────────────────────────────
CLIMATE_SENSORS        = ["temperature", "humidity"]
SELECTABLE_GAS_SENSORS = ["mq2", "mq3", "mq4", "mq135", "mq136", "mq137", "co2"]
ALL_SENSORS            = CLIMATE_SENSORS + SELECTABLE_GAS_SENSORS

SENSOR_METADATA = {
    "temperature": {"label": "Temperature",  "unit": "°C",  "category": "climate"},
    "humidity":    {"label": "Humidity",      "unit": "%",   "category": "climate"},
    "mq2":   {"label": "MQ-2",   "unit": "ppm", "category": "gas", "target": "LPG/Smoke"},
    "mq3":   {"label": "MQ-3",   "unit": "ppm", "category": "gas", "target": "Alcohol"},
    "mq4":   {"label": "MQ-4",   "unit": "ppm", "category": "gas", "target": "Methane"},
    "mq135": {"label": "MQ-135", "unit": "ppm", "category": "gas", "target": "VOCs/NH3"},
    "mq136": {"label": "MQ-136", "unit": "ppm", "category": "gas", "target": "H2S"},
    "mq137": {"label": "MQ-137", "unit": "ppm", "category": "gas", "target": "NH3"},
    "co2":   {"label": "CO₂",    "unit": "ppm", "category": "gas", "target": "Carbon Dioxide"},
}

SUPPORTED_FOODS = [
    "apple", "banana", "orange", "grapes", "strawberry",
    "mango", "peach", "pear", "tomato", "carrot",
    "broccoli", "corn", "lettuce", "blueberry", "kiwi",
]

DEFAULT_GAS_SENSORS_BY_FOOD = {
    "apple":      ["mq3", "mq135", "co2"],
    "banana":     ["mq3", "mq135", "co2"],
    "orange":     ["mq3", "mq135", "co2"],
    "grapes":     ["mq3", "mq135", "co2"],
    "strawberry": ["mq3", "mq135", "co2"],
    "mango":      ["mq3", "mq135", "co2"],
    "peach":      ["mq3", "mq135", "co2"],
    "pear":       ["mq3", "mq135", "co2"],
    "tomato":     ["mq2", "mq135", "co2"],
    "carrot":     ["mq135", "co2"],
    "broccoli":   ["mq135", "co2"],
    "corn":       ["mq135", "co2"],
    "lettuce":    ["mq135", "co2"],
    "blueberry":  ["mq3", "mq135", "co2"],
    "kiwi":       ["mq3", "mq135", "co2"],
}

TEMPERATURE_OPTIMAL = {
    "apple": 4.0, "banana": 13.0, "orange": 8.0,  "grapes": 0.0,  "strawberry": 1.0,
    "mango": 13.0,"peach":  1.0,  "pear":   1.0,  "tomato": 12.0, "carrot":     0.0,
    "broccoli": 0.0, "corn": 1.0, "lettuce": 0.0, "blueberry": 0.0, "kiwi": 0.0,
}

HUMIDITY_OPTIMAL = {
    "apple": 90.0, "banana": 85.0, "orange": 90.0, "grapes": 90.0, "strawberry": 90.0,
    "mango": 85.0, "peach":  90.0, "pear":   90.0, "tomato": 85.0, "carrot":     95.0,
    "broccoli": 95.0, "corn": 95.0, "lettuce": 95.0, "blueberry": 90.0, "kiwi": 90.0,
}

GAS_THRESHOLDS = {
    "mq2":   1000,
    "mq3":   200,
    "mq4":   5000,
    "mq135": 500,
    "mq136": 50,
    "mq137": 100,
    "co2":   2000,
}

FRESHNESS_THRESHOLDS = {
    "fresh":        70,
    "half_spoiled": 40,
}

# ─── Buffers / files ──────────────────────────────────────────────────
BUFFER_SIZE             = 1000
DASHBOARD_HISTORY_LIMIT = 50
DATA_STALE_SECONDS      = 20
SESSION_FILE            = DATA_DIR / "session_state.json"
LAST_DATA_FILE          = DATA_DIR / "latest_data.json"
LOG_DIR                 = DATA_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
