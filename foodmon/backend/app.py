"""Main Flask application for the Food Freshness Monitoring System."""
from __future__ import annotations

import json
import subprocess
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
from firestore_logger import init_firestore, log_training_reading
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

# ─── Shared singletons ────────────────────────────────────────────────
mqtt_handler: MQTTHandler | None = None
ml_engine: MLEngine | None = None
actuator_controller: ActuatorController | None = None
cloud_client = CloudClient()
session_manager = SessionManager()

# ─────────────────────────────────────────────────────────────────────
#  SHARED STATE
# ─────────────────────────────────────────────────────────────────────
sensor_data: Dict[str, Any] = {
    "temperature": None,
    "humidity": None,
    "sensor_chamber_temperature": None,
    "sensor_chamber_humidity": None,
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

# Note: "buzzer" intentionally excluded — the buzzer is no longer a
# persisted/timed actuator. It only ever fires fire-and-forget pulses
# on ESP_BUZZER_COMMAND_TOPIC (see buzzer_beep() and the spoilage
# check in run_ml_and_control()).
actuator_status: Dict[str, Any] = {
    "cooler": False,
    "ventilation": "OFF",
    "humidifier": False,
    "light": False,
    "actuator_timer_on": False,
    "actuator_remaining_s": 0,
}

state_lock = threading.Lock()

# ─── Light state ──────────────────────────────────────────────────────
# Single source of truth for the relay/light. Completely independent of
# the timed-vs-manual actuator system. Published to the dedicated
# foodmon/control/light topic so the ESP never routes it through any
# other actuator logic at all.
_light_on: bool = False

# ─── Spoilage-alert edge detection ────────────────────────────────────
# Tracks the previous ML status so the long beep fires exactly once on
# the transition INTO "Spoiled", rather than on every ML tick while the
# food remains spoiled (which would buzz continuously).
_last_freshness_status: str | None = None


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
    socketio.emit("sensor_update", sensor_data, namespace="/")
    socketio.emit("ml_update", ml_results, namespace="/")
    socketio.emit("actuator_update", actuator_status, namespace="/")
    socketio.emit("session_update", session_manager.get(), namespace="/")
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
    flat: Dict[str, float] = {
        "temperature": float(sensor_data["temperature"] or 0.0),
        "humidity": float(sensor_data["humidity"] or 0.0),
    }
    for gas in config.SELECTABLE_GAS_SENSORS:
        raw = sensor_data["gases"].get(gas, {})
        flat[gas] = float(raw.get("value", 0.0) if isinstance(raw, dict) else raw or 0.0)
    return flat


def extract_full_sensor_snapshot() -> Dict[str, float]:
    """Like extract_flat_sensor_values but also includes sensor chamber climate
    (DHT22 temperature/humidity), for Firestore ML training logs."""
    flat = extract_flat_sensor_values()
    flat["sensor_chamber_temperature"] = float(sensor_data["sensor_chamber_temperature"] or 0.0)
    flat["sensor_chamber_humidity"] = float(sensor_data["sensor_chamber_humidity"] or 0.0)
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


def trigger_buzzer(beep_type: str) -> None:
    """Publish a fire-and-forget buzzer pulse to the ESP.

    beep_type: "short" (UI press, ~100ms) or "long" (spoilage alert, ~800ms).
    Safe to call even if MQTT isn't connected — it just logs and skips.
    """
    if not mqtt_handler or not mqtt_handler.connected:
        print(f"[buzzer] Skipped ({beep_type}) — MQTT not connected")
        return
    mqtt_handler.publish(config.ESP_BUZZER_COMMAND_TOPIC, {
        "type": beep_type,
        "device_id": config.DEVICE_ID,
        "timestamp": int(time.time()),
    })


def run_ml_and_control() -> None:
    """ML inference + automatic actuator control + spoilage alert.

    Manual actuator toggles on the System Control page are locked out
    entirely while a session is running (see updateActuatorLock in
    system_control.js), so there is no need for any manual-override
    window here — whenever this runs, ML/rules are the sole source of
    actuator commands.

    Also fires the long buzzer alert exactly once whenever the
    predicted status transitions into "Spoiled".
    """
    global _last_freshness_status

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

    # Long beep — fires once on the transition into "Spoiled".
    if prediction["status"] == "Spoiled" and _last_freshness_status != "Spoiled":
        trigger_buzzer("long")
        print("[buzzer] Long beep — spoiled food detected")
    _last_freshness_status = prediction["status"]

    new_status = actuator_controller.update(
        session["food_name"],
        flat,
        prediction,
        session.get("selected_sensors", []),
        suppress_dispatch=False,
    )
    actuator_status.update({
        k: v for k, v in new_status.items()
        if k not in ("actuator_timer_on", "actuator_remaining_s")
    })


def log_training_data_if_enabled(session: Dict[str, Any]) -> None:
    """Log a labeled sensor snapshot to Firestore for ML training,
    only when a freshness_label is set on the current session."""
    label = session.get("freshness_label")
    if not label:
        return
    snapshot = extract_full_sensor_snapshot()
    log_training_reading(session.get("food_name"), label, snapshot)


# ─────────────────────────────────────────────────────────────────────
#  MQTT SENSOR CALLBACK
# ─────────────────────────────────────────────────────────────────────
def sensor_callback(topic: str, data: Dict[str, Any]) -> None:
    with state_lock:
        try:
            ts = data.get("timestamp", int(time.time()))

            if topic == "foodmon/device/status":
                sensor_data["device_status"] = data.get("status", "online")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/storage/temperature":
                sensor_data["temperature"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/storage/humidity":
                sensor_data["humidity"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/sensor_chamber/temperature":
                sensor_data["sensor_chamber_temperature"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic == "foodmon/sensors/environmental/sensor_chamber/humidity":
                sensor_data["sensor_chamber_humidity"] = data.get("value")
                sensor_data["timestamp"] = ts

            elif topic.startswith("foodmon/sensors/gas/"):
                sensor_name = topic.split("/")[-1]
                if sensor_name in config.SELECTABLE_GAS_SENSORS:
                    sensor_data["gases"][sensor_name] = {
                        "value": data.get("value", 0),
                        "unit": data.get("unit", "ppm"),
                        "target_gas": config.SENSOR_METADATA[sensor_name].get("target", ""),
                    }
                    sensor_data["timestamp"] = ts

            elif topic == "foodmon/actuators/status":
                # Update actuator fields from ESP report.
                # "buzzer" is intentionally not read here — it is no
                # longer a persisted/reported actuator state, only
                # instantaneous pulses (see trigger_buzzer()).
                actuator_status["cooler"]      = data.get("cooler", False)
                actuator_status["ventilation"] = data.get("ventilation", "OFF")
                actuator_status["humidifier"]  = data.get("humidifier", False)
                # No timer system anymore — actuators stay as commanded.
                actuator_status["actuator_timer_on"]    = False
                actuator_status["actuator_remaining_s"] = 0
                # Light: the ESP now reports lightState correctly via the
                # dedicated topic path, so we can trust it directly.
                # But we also keep _light_on as the Pi-side authority so
                # reboots and reconnects stay consistent.
                actuator_status["light"] = _light_on


            session = session_manager.get()
            if session.get("status") == "running":
                if session.get("freshness_label"):
                    # Training/data-collection session — log only, no actuator control
                    log_training_data_if_enabled(session)
                else:
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
    global _last_freshness_status

    data = request.json or {}
    food_name = str(data.get("food_name", "")).lower().strip()
    selected_sensors = data.get("selected_sensors", [])
    freshness_label = str(data.get("freshness_label", "fresh")).lower().strip()

    valid_labels = {"fresh", "half_spoiled", "spoiled"}
    if freshness_label not in valid_labels:
        return jsonify({
            "success": False,
            "message": f"freshness_label must be one of {sorted(valid_labels)}",
        }), 400

    if food_name not in config.SUPPORTED_FOODS:
        return jsonify({"success": False, "message": "Unsupported food"}), 400

    valid_sensors = [s for s in selected_sensors if s in config.SELECTABLE_GAS_SENSORS]
    if not valid_sensors:
        return jsonify({"success": False, "message": "Select at least one gas sensor"}), 400

    with state_lock:
        session = session_manager.start(food_name, valid_sensors, freshness_label)
        ml_results["history"] = []
        # Reset spoilage edge-detection so a new session always starts
        # from a clean "not yet spoiled" state.
        _last_freshness_status = None

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
            "temperature": sensor_data["temperature"],
            "humidity": sensor_data["humidity"],
            "sensor_chamber_temperature": sensor_data["sensor_chamber_temperature"],
            "sensor_chamber_humidity": sensor_data["sensor_chamber_humidity"],
            "gases": sensor_data["gases"],
            "timestamp": sensor_data["timestamp"],
            "device_status": sensor_data["device_status"],
        },
        "ml_results": ml_results,
        "actuator_status": actuator_status,
        "session": session_manager.get(),
    })


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — Light (home page button)
# ─────────────────────────────────────────────────────────────────────
@app.route("/api/light_status", methods=["GET"])
def light_status():
    """Return the current light ON/OFF state."""
    return jsonify({"success": True, "light": _light_on})


@app.route("/api/toggle_light", methods=["POST"])
def toggle_light():
    """
    Turn the relay-controlled light ON or OFF.

    Accepts optional JSON body:  { "light": true | false }
    If omitted, the current state is toggled.

    Publishes to the DEDICATED topic  foodmon/control/light
    which the ESP handles with a plain relay flip — no timer,
    no guards, no interaction with any other actuator.
    """
    global _light_on

    data = request.json or {}
    if "light" in data:
        _light_on = bool(data["light"])
    else:
        _light_on = not _light_on

    # Publish to the dedicated light topic — NOT to foodmon/control/actuators
    light_command = {
        "light":     _light_on,
        "device_id": config.DEVICE_ID,
        "timestamp": int(time.time()),
    }

    with state_lock:
        if mqtt_handler and mqtt_handler.connected:
            mqtt_handler.publish(config.ESP_LIGHT_COMMAND_TOPIC, light_command)
            print(f"[light] Published to {config.ESP_LIGHT_COMMAND_TOPIC}: light={_light_on}")
        else:
            print(f"[light] MQTT not connected — state saved locally: light={_light_on}")

        actuator_status["light"] = _light_on
        emit_full_state()
        save_latest_data()

    print(f"[light] Light {'ON' if _light_on else 'OFF'}")
    return jsonify({"success": True, "light": _light_on})


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — Buzzer
# ─────────────────────────────────────────────────────────────────────
@app.route("/api/buzzer_beep", methods=["POST"])
def buzzer_beep():
    """
    Fire a single short beep (~100ms) for touchscreen UI feedback.

    Called whenever the user presses any button, tab, or toggle switch
    on the frontend (see the global click listener in app.js /
    system_control.js). Stateless — no session lock, no actuator
    interaction, fires immediately on the dedicated buzzer topic.
    """
    if not mqtt_handler:
        return jsonify({"success": False, "message": "MQTT handler not initialised"}), 503
    if not mqtt_handler.connected:
        return jsonify({"success": False, "message": "MQTT broker not connected"}), 503

    trigger_buzzer("short")
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — System power
# ─────────────────────────────────────────────────────────────────────
@app.route("/api/system/poweroff", methods=["POST"])
def system_poweroff():
    """Initiate a clean system shutdown of the Raspberry Pi."""
    print("[system] Shutdown requested via dashboard.")
    try:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
    except Exception as exc:
        print(f"[system] Shutdown error: {exc}")
        return jsonify({"success": False, "message": str(exc)}), 500
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────
#  ROUTES — Manual actuator (System Control page)
# ─────────────────────────────────────────────────────────────────────
@app.route("/api/manual_actuator", methods=["POST"])
def manual_actuator():
    """
    Directly set the state of one or more TIMED-formerly actuators:
    cooler, ventilation, humidifier.

    There is no timer and no separate "send" step. Whatever state is
    posted here is applied immediately and stays in effect until the
    next call changes it — i.e. the dashboard switch IS the actuator
    state. This endpoint is only reachable from the UI while no
    monitoring session is running (manual controls are locked out
    during a session).

    The light and buzzer are intentionally excluded — use
    /api/toggle_light and /api/buzzer_beep instead. The buzzer in
    particular is no longer a settable actuator state at all; the
    long alert beep is fired automatically and only by
    run_ml_and_control() on a spoilage detection.
    """
    data = request.json or {}

    valid_vent_levels = {"OFF", "LOW", "MEDIUM", "HIGH"}
    if "ventilation" in data and data["ventilation"] not in valid_vent_levels:
        return jsonify({
            "success": False,
            "message": f"ventilation must be one of {sorted(valid_vent_levels)}",
        }), 400

    # Light and buzzer are excluded from this endpoint — they have
    # their own dedicated routes/topics.
    allowed_keys = {"cooler", "ventilation", "humidifier"}
    command = {k: v for k, v in data.items() if k in allowed_keys}

    if not command:
        return jsonify({"success": False, "message": "No valid actuator keys provided"}), 400

    command["manual"]    = True
    command["command"]   = "set_actuators"
    command["device_id"] = config.DEVICE_ID
    command["timestamp"] = int(time.time())

    with state_lock:
        if not mqtt_handler:
            return jsonify({"success": False, "message": "MQTT handler not initialised"}), 503
        if not mqtt_handler.connected:
            return jsonify({"success": False, "message": "MQTT broker not connected"}), 503

        mqtt_handler.publish(config.ESP_ACTUATOR_COMMAND_TOPIC, command)

        for key in ("cooler", "ventilation", "humidifier"):
            if key in data:
                actuator_status[key] = data[key]

        # No timer: the actuator simply stays in whatever state was just sent.
        actuator_status["actuator_timer_on"]    = False
        actuator_status["actuator_remaining_s"] = 0

        emit_full_state()
        save_latest_data()

    print(f"[manual_actuator] Command sent to ESP32: {command}")
    return jsonify({
        "success": True,
        "command_sent": command,
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
    """Safe-off actuators if no sensor data has arrived for >60 s WHILE a
    monitoring session is running.

    This guards the automatic ML/rules-driven actuators against getting
    stuck on if the ESP stops reporting sensor data mid-session — that is
    the only scenario in which `actuator_controller`'s internal state is
    being actively driven, so it's the only scenario where "staleness" is
    meaningful.

    It must NOT run while no session is active: manual toggles on the
    System Control page bypass `actuator_controller` entirely (they
    publish straight to MQTT and update `actuator_status` directly), so
    `actuator_controller.last_update_time` is never refreshed by manual
    control. Without this guard, the watchdog would see manual mode as
    permanently "stale" 60 s after Flask starts and would force every
    manually-toggled actuator off every 5 s thereafter — exactly the bug
    where the ON duration kept shrinking on each attempt.
    """
    while True:
        time.sleep(5)
        if actuator_controller and session_manager.is_running():
            actuator_controller.safe_shutdown_if_stale()


# ─────────────────────────────────────────────────────────────────────
#  SYSTEM INITIALISATION
# ─────────────────────────────────────────────────────────────────────
def init_system() -> None:
    global mqtt_handler, ml_engine, actuator_controller, _light_on

    init_firestore(str(config.BASE_DIR / "foodmon-add60-firebase-adminsdk-fbsvc-37b5fc0160.json"))

    # Restore light state from last saved data so the button shows the
    # correct state after a Flask restart or Pi reboot
    try:
        if config.LAST_DATA_FILE.exists():
            saved = json.loads(config.LAST_DATA_FILE.read_text())
            _light_on = bool(saved.get("actuator_status", {}).get("light", False))
            actuator_status["light"] = _light_on
            print(f"[init] Restored light state: {'ON' if _light_on else 'OFF'}")
    except Exception as exc:
        print(f"[init] Could not restore light state: {exc}")

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
