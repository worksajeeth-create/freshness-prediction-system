"""Main Flask application for the Food Freshness Monitoring System.

Changes in this version:
  - sensor_data now tracks BOTH climate sources:
      storage chamber       → temperature / humidity       (AM2301, GPIO27)
      MQ sensor chamber     → sensor_chamber_temperature / sensor_chamber_humidity (DHT22, GPIO14)
  - sensor_callback routes the new MQTT topic paths emitted by the ESP32.
  - emit_full_state() emits a dedicated 'climate_update' event carrying both chambers.
  - /api/current_data includes sensor_chamber fields.
  - Actuator commands carry an explicit 'duration_s' field (default 60 s) that
    the Pi respects when dispatching to the ESP; the ESP32 independently enforces
    its own 60-second hardware timer for safety.
  - MQTT subscription list includes foodmon/actuators/status so the Pi can
    mirror the ESP32's reported actuator state (useful after an ESP reboot).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

import config
from actuator_control import ActuatorController
from cloud_client import CloudClient
from ml_engine import MLEngine
from mqtt_handler import MQTTHandler
from session_manager import SessionManager

# ─── Flask + SocketIO setup ───────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=str(config.FRONTEND_DIR),
    static_folder=str(config.FRONTEND_DIR),
)
app.config["SECRET_KEY"] = config.SECRET_KEY
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ─── Shared singletons (initialised in init_system) ──────────────────
mqtt_handler: MQTTHandler | None = None
ml_engine: MLEngine | None = None
actuator_controller: ActuatorController | None = None
cloud_client = CloudClient()
session_manager = SessionManager()

# ─────────────────────────────────────────────────────────────────────
#  SHARED STATE DICTS
# ─────────────────────────────────────────────────────────────────────
sensor_data: Dict[str, Any] = {
    # Food storage chamber (AM2301 – GPIO27)
    "temperature": None,
    "humidity": None,
    # MQ sensor chamber (DHT22 – GPIO14)
    "sensor_chamber_temperature": None,
    "sensor_chamber_humidity": None,
    # Gas sensors
    "gases": {},
    "timestamp": None,
    "device_status": "offline",
}

ml_results: Dict[str, Any] = {
    "freshness_index": None,
    "status": None,
    "remaining_days": None,
    "history": [],
}

actuator_status: Dict[str, Any] = {
    "cooler": False,
    "ventilation": "OFF",
    "humidifier": False,
    "light": False,
    "buzzer": False,
    # Timer info mirrored from ESP32 reports
    "actuator_timer_on": False,
    "actuator_remaining_s": 0,
}

state_lock = threading.Lock()

# ─── Manual override tracking ─────────────────────────────────────────
# Instead of maintaining a separate Pi-side clock, we read directly from
# the ESP32's reported actuator_timer_on field (populated by the
# foodmon/actuators/status topic).  This is always accurate because the
# ESP reports its real hardware timer state after every change.
# The local fallback clock is kept only for the brief window between
# sending a manual command and receiving the first ESP status report.
_manual_override_until: float = 0.0


def _manual_override_active() -> bool:
    """Return True if the ESP's manual timer is running OR our local
    fallback clock hasn't expired yet."""
    # Primary: trust the ESP's own reported state
    if actuator_status.get("actuator_timer_on", False):
        return True
    # Fallback: local clock covers the gap before first ESP report arrives
    return time.time() < _manual_override_until


def _set_manual_override() -> None:
    global _manual_override_until
    # Give a short local window (10 s) — just enough for the first ESP
    # status report to arrive and take over tracking.
    _manual_override_until = time.time() + 10
    print("[override] Manual command sent — ML dispatch suppressed until ESP confirms timer.")


# ─────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────
def save_latest_data() -> None:
    payload = {
        "sensor_data": sensor_data,
        "ml_results": ml_results,
        "actuator_status": actuator_status,
        "session": session_manager.get(),
    }
    config.LAST_DATA_FILE.write_text(json.dumps(payload, indent=2))


def emit_full_state() -> None:
    """Push every state slice to connected dashboard clients."""
    socketio.emit("sensor_update", sensor_data, namespace="/")
    socketio.emit("ml_update", ml_results, namespace="/")
    socketio.emit("actuator_update", actuator_status, namespace="/")
    socketio.emit("session_update", session_manager.get(), namespace="/")
    # Dedicated climate event carrying both chambers
    socketio.emit(
        "climate_update",
        {
            "storage": {
                "temperature": sensor_data["temperature"],
                "humidity": sensor_data["humidity"],
            },
            "sensor_chamber": {
                "temperature": sensor_data["sensor_chamber_temperature"],
                "humidity": sensor_data["sensor_chamber_humidity"],
            },
        },
        namespace="/",
    )


def extract_flat_sensor_values() -> Dict[str, float]:
    """Flatten sensor_data into a single dict for ML / cloud payloads."""
    flat: Dict[str, float] = {
        "temperature": float(sensor_data["temperature"] or 0.0),
        "humidity": float(sensor_data["humidity"] or 0.0),
    }
    for gas in config.SELECTABLE_GAS_SENSORS:
        raw = sensor_data["gases"].get(gas, {})
        flat[gas] = float(raw.get("value", 0.0) if isinstance(raw, dict) else raw or 0.0)
    return flat


def append_cloud_reading() -> None:
    session = session_manager.get()
    if not session.get("session_id"):
        return
    flat = extract_flat_sensor_values()
    payload = {
        "device_id": config.DEVICE_ID,
        "session_id": session["session_id"],
        "food_name": session.get("food_name"),
        "selected_sensors": session.get("selected_sensors", []),
        "timestamp": sensor_data.get("timestamp"),
        "sensor_chamber_temperature": sensor_data.get("sensor_chamber_temperature"),
        "sensor_chamber_humidity": sensor_data.get("sensor_chamber_humidity"),
        **flat,
    }
    try:
        cloud_client.append_reading(session["session_id"], payload)
    except Exception as exc:
        print(f"[Cloud] append_reading failed: {exc}")


def run_ml_and_control() -> None:
    session = session_manager.get()
    if session.get("status") != "running" or not session.get("food_name"):
        return
    if sensor_data["temperature"] is None or sensor_data["humidity"] is None:
        return
    if not ml_engine or not actuator_controller:
        return

    flat = extract_flat_sensor_values()
    prediction = ml_engine.predict(
        session["food_name"], flat, session.get("selected_sensors", [])
    )
    if not prediction:
        return

    ml_results["freshness_index"] = prediction["freshness_index"]
    ml_results["status"] = prediction["status"]
    ml_results["status_color"] = prediction["status_color"]
    ml_results["remaining_days"] = prediction["remaining_days"]
    ml_results["history"].append(
        {"timestamp": sensor_data["timestamp"], "freshness": prediction["freshness_index"]}
    )
    ml_results["history"] = ml_results["history"][-config.DASHBOARD_HISTORY_LIMIT:]

    # ── Skip actuator dispatch while a manual override is active ─────
    # The manual command already told the ESP what to do; if the ML
    # controller also publishes a command every 2 s it will either reset
    # the 60-second timer or turn actuators off — both are wrong.
    if _manual_override_active():
        return

    new_status = actuator_controller.update(
        session["food_name"],
        flat,
        prediction,
        session.get("selected_sensors", []),
        suppress_dispatch=False,   # ML is in control — dispatch normally
    )
    # Preserve timer fields that come from ESP reports
    actuator_status.update({
        k: v for k, v in new_status.items()
        if k not in ("actuator_timer_on", "actuator_remaining_s")
    })


# ─────────────────────────────────────────────────────────────────────
#  MQTT SENSOR CALLBACK
# ─────────────────────────────────────────────────────────────────────
def sensor_callback(topic: str, data: Dict[str, Any]) -> None:
    """Route every MQTT message from the ESP32 into the shared state."""
    with state_lock:
        try:
            ts = data.get("timestamp", int(time.time()))

            # ── Device heartbeat ──────────────────────────────────────
            if topic == "foodmon/device/status":
                sensor_data["device_status"] = data.get("status", "online")
                sensor_data["timestamp"] = ts

            # ── Food storage chamber — AM2301 (GPIO27) ────────────────
            elif topic == "foodmon/sensors/environmental/storage/temperature":
                sensor_data["temperature"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/storage/humidity":
                sensor_data["humidity"] = data.get("value")
                sensor_data["timestamp"] = ts

            # ── MQ sensor chamber — DHT22 (GPIO14) ────────────────────
            elif topic == "foodmon/sensors/environmental/sensor_chamber/temperature":
                sensor_data["sensor_chamber_temperature"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/sensor_chamber/humidity":
                sensor_data["sensor_chamber_humidity"] = data.get("value")
                sensor_data["timestamp"] = ts

            # ── Gas sensors ───────────────────────────────────────────
            elif topic.startswith("foodmon/sensors/gas/"):
                sensor_name = topic.split("/")[-1]
                if sensor_name in config.SELECTABLE_GAS_SENSORS:
                    sensor_data["gases"][sensor_name] = {
                        "value": data.get("value", 0),
                        "unit": data.get("unit", "ppm"),
                        "target_gas": config.SENSOR_METADATA[sensor_name].get("target", ""),
                    }
                    sensor_data["timestamp"] = ts

            # ── Actuator status report from ESP32 ─────────────────────
            # The ESP32 publishes its live actuator state (including timer
            # countdown) to foodmon/actuators/status after every change.
            elif topic == "foodmon/actuators/status":
                actuator_status["cooler"]               = data.get("cooler", False)
                actuator_status["ventilation"]          = data.get("ventilation", "OFF")
                actuator_status["humidifier"]           = data.get("humidifier", False)
                actuator_status["light"]                = data.get("light", False)
                actuator_status["buzzer"]               = data.get("buzzer", False)
                actuator_status["actuator_timer_on"]    = data.get("actuator_timer_on", False)
                actuator_status["actuator_remaining_s"] = data.get("actuator_remaining_s", 0)

            # ── Run ML + control logic when session is active ─────────
            session = session_manager.get()
            if session.get("status") == "running":
                append_cloud_reading()
                run_ml_and_control()

            emit_full_state()
            save_latest_data()

        except Exception as exc:
            print(f"[sensor_callback] Error: {exc}")


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — Pages
# ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/system-control")
def system_control_page():
    return render_template("system_control.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — API
# ─────────────────────────────────────────────────────────────────────
@app.route("/api/foods", methods=["GET"])
def get_foods():
    return jsonify({
        "success": True,
        "foods": config.SUPPORTED_FOODS,
        "default_sensor_map": config.DEFAULT_GAS_SENSORS_BY_FOOD,
    })


@app.route("/api/sensors", methods=["GET"])
def get_sensors():
    sensor_list = [
        {"id": sid, **config.SENSOR_METADATA[sid]}
        for sid in config.SELECTABLE_GAS_SENSORS
    ]
    return jsonify({"success": True, "sensors": sensor_list})


@app.route("/api/start_session", methods=["POST"])
def start_session():
    data = request.json or {}
    food_name = str(data.get("food_name", "")).lower().strip()
    selected_sensors = data.get("selected_sensors", [])

    if food_name not in config.SUPPORTED_FOODS:
        return jsonify({"success": False, "message": "Unsupported food"}), 400

    valid_sensors = [s for s in selected_sensors if s in config.SELECTABLE_GAS_SENSORS]
    if not valid_sensors:
        return jsonify({"success": False, "message": "Select at least one gas sensor"}), 400

    with state_lock:
        session = session_manager.start(food_name, valid_sensors)
        ml_results["history"] = []

        try:
            cloud_client.upsert_session(session)
            cloud_client.append_event(session["session_id"], {
                "type": "session_started",
                "timestamp": int(time.time()),
                "food_name": food_name,
                "selected_sensors": valid_sensors,
            })
        except Exception as exc:
            print(f"[Cloud] session start failed: {exc}")

        if mqtt_handler:
            mqtt_handler.publish(config.MQTT_CONTROL_TOPICS["start"], {
                "command": "start",
                "device_id": config.DEVICE_ID,
                **session,
            })

        emit_full_state()
        save_latest_data()

    return jsonify({"success": True, "session": session})


@app.route("/api/stop_session", methods=["POST"])
def stop_session():
    with state_lock:
        session = session_manager.stop()
        try:
            cloud_client.upsert_session(session)
            if session.get("session_id"):
                cloud_client.append_event(session["session_id"], {
                    "type": "session_stopped",
                    "timestamp": int(time.time()),
                })
        except Exception as exc:
            print(f"[Cloud] session stop failed: {exc}")

        if mqtt_handler:
            mqtt_handler.publish(config.MQTT_CONTROL_TOPICS["stop"], {
                "command": "stop",
                "device_id": config.DEVICE_ID,
                **session,
            })

        emit_full_state()
        save_latest_data()

    return jsonify({"success": True, "session": session})


@app.route("/api/session_status", methods=["GET"])
def session_status():
    return jsonify({"success": True, "session": session_manager.get()})


@app.route("/api/current_data", methods=["GET"])
def current_data():
    return jsonify({
        "success": True,
        "sensor_data": {
            # Storage chamber
            "temperature": sensor_data["temperature"],
            "humidity": sensor_data["humidity"],
            # Sensor chamber
            "sensor_chamber_temperature": sensor_data["sensor_chamber_temperature"],
            "sensor_chamber_humidity": sensor_data["sensor_chamber_humidity"],
            # Gas + meta
            "gases": sensor_data["gases"],
            "timestamp": sensor_data["timestamp"],
            "device_status": sensor_data["device_status"],
        },
        "ml_results": ml_results,
        "actuator_status": actuator_status,
        "session": session_manager.get(),
    })


@app.route("/api/manual_actuator", methods=["POST"])
def manual_actuator():
    """
    Send a manual actuator command to the ESP32 via MQTT.

    Expected JSON body (all keys optional — omit to keep current state):
    {
        "cooler":      true | false,
        "ventilation": "OFF" | "LOW" | "MEDIUM" | "HIGH",
        "humidifier":  true | false,
        "light":       true | false,
        "buzzer":      true | false
    }

    The ESP32 will run the requested actuators for exactly 60 seconds
    then automatically switch them all off (hardware-enforced timer).
    Each call to this endpoint resets the 60-second countdown.
    """
    data = request.json or {}

    # Validate ventilation value if present
    valid_vent_levels = {"OFF", "LOW", "MEDIUM", "HIGH"}
    if "ventilation" in data and data["ventilation"] not in valid_vent_levels:
        return jsonify({
            "success": False,
            "message": f"ventilation must be one of {sorted(valid_vent_levels)}",
        }), 400

    # Build the MQTT payload — only include keys the caller sent
    allowed_keys = {"cooler", "ventilation", "humidifier", "light", "buzzer"}
    command = {k: v for k, v in data.items() if k in allowed_keys}

    if not command:
        return jsonify({"success": False, "message": "No valid actuator keys provided"}), 400

    # Tag the command so ESP32 knows this is a manual (dashboard) command
    # and not an ML-driven one.  The ESP rejects ML commands while the
    # manual timer is running, so this flag is essential.
    command["manual"]     = True
    command["command"]    = "set_actuators"
    command["device_id"]  = config.DEVICE_ID
    command["timestamp"]  = int(time.time())

    with state_lock:
        if not mqtt_handler:
            return jsonify({"success": False, "message": "MQTT handler not initialised"}), 503
        if not mqtt_handler.connected:
            return jsonify({"success": False, "message": "MQTT broker not connected"}), 503

        mqtt_handler.publish(config.ESP_ACTUATOR_COMMAND_TOPIC, command)

        # Block ML actuator control for the full 60-second window so it
        # cannot fight the manual command with its own MQTT publishes.
        _set_manual_override()

        # Mirror the requested state into actuator_status optimistically
        # (will be overwritten by the real ESP32 report within ~1 s)
        for key in ("cooler", "ventilation", "humidifier", "light", "buzzer"):
            if key in data:
                actuator_status[key] = data[key]

        # Mark timer as active from the Pi side too so the dashboard
        # can show the countdown without waiting for the ESP report
        actuator_status["actuator_timer_on"]    = True
        actuator_status["actuator_remaining_s"] = int(config.ACTUATOR_RUN_SECONDS)

        emit_full_state()
        save_latest_data()

    print(f"[manual_actuator] Command sent to ESP32: {command}")
    return jsonify({
        "success": True,
        "command_sent": command,
        "actuator_timeout_s": config.ACTUATOR_RUN_SECONDS,
    })


# ─────────────────────────────────────────────────────────────────────
#  SOCKETIO EVENTS
# ─────────────────────────────────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    emit("connection_response", {"status": "connected"})
    emit("sensor_update", sensor_data)
    emit("ml_update", ml_results)
    emit("actuator_update", actuator_status)
    emit("session_update", session_manager.get())
    emit("climate_update", {
        "storage": {
            "temperature": sensor_data["temperature"],
            "humidity": sensor_data["humidity"],
        },
        "sensor_chamber": {
            "temperature": sensor_data["sensor_chamber_temperature"],
            "humidity": sensor_data["sensor_chamber_humidity"],
        },
    })


@socketio.on("request_update")
def handle_request_update():
    emit("sensor_update", sensor_data)
    emit("ml_update", ml_results)
    emit("actuator_update", actuator_status)
    emit("session_update", session_manager.get())
    emit("climate_update", {
        "storage": {
            "temperature": sensor_data["temperature"],
            "humidity": sensor_data["humidity"],
        },
        "sensor_chamber": {
            "temperature": sensor_data["sensor_chamber_temperature"],
            "humidity": sensor_data["sensor_chamber_humidity"],
        },
    })


# ─────────────────────────────────────────────────────────────────────
#  BACKGROUND THREADS
# ─────────────────────────────────────────────────────────────────────
def watchdog_loop() -> None:
    """Safe-off actuators if no sensor data has arrived for >60 s."""
    while True:
        time.sleep(5)
        if actuator_controller:
            actuator_controller.safe_shutdown_if_stale()


# ─────────────────────────────────────────────────────────────────────
#  SYSTEM INITIALISATION
# ─────────────────────────────────────────────────────────────────────
def init_system() -> None:
    global mqtt_handler, ml_engine, actuator_controller

    # Subscribe to the new actuators/status topic in addition to the
    # default topics defined in config.MQTT_SENSOR_TOPICS
    extra_topics = [("foodmon/actuators/status", 1)]

    mqtt_handler = MQTTHandler(callback=sensor_callback, extra_topics=extra_topics)
    mqtt_handler.start()

    ml_engine = MLEngine(model_dir=str(config.MODEL_DIR))
    ml_engine.load_models()

    actuator_controller = ActuatorController(mqtt_handler=mqtt_handler)

    threading.Thread(target=watchdog_loop, daemon=True).start()


if __name__ == "__main__":
    init_system()
    socketio.run(
        app,
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        allow_unsafe_werkzeug=True,
    )
