"""Main Flask application for the rebuilt Food Freshness Monitoring System."""
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

app = Flask(
    __name__,
    template_folder=str(config.FRONTEND_DIR),
    static_folder=str(config.FRONTEND_DIR),
)
app.config["SECRET_KEY"] = config.SECRET_KEY
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

mqtt_handler: MQTTHandler | None = None
ml_engine: MLEngine | None = None
actuator_controller: ActuatorController | None = None
cloud_client = CloudClient()
session_manager = SessionManager()

sensor_data: Dict[str, Any] = {
    "temperature": None,
    "humidity": None,
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
}

state_lock = threading.Lock()


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


def extract_flat_sensor_values() -> Dict[str, float]:
    flat = {
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
        **flat,
    }
    try:
        cloud_client.append_reading(session["session_id"], payload)
    except Exception as exc:
        print(f"Cloud append_reading failed: {exc}")


def run_ml_and_control() -> None:
    session = session_manager.get()
    if session.get("status") != "running" or not session.get("food_name"):
        return
    if sensor_data["temperature"] is None or sensor_data["humidity"] is None:
        return
    if not ml_engine or not actuator_controller:
        return

    flat = extract_flat_sensor_values()
    prediction = ml_engine.predict(session["food_name"], flat, session.get("selected_sensors", []))
    if not prediction:
        return

    ml_results["freshness_index"] = prediction["freshness_index"]
    ml_results["status"] = prediction["status"]
    ml_results["status_color"] = prediction["status_color"]
    ml_results["remaining_days"] = prediction["remaining_days"]
    ml_results["history"].append({
        "timestamp": sensor_data["timestamp"],
        "freshness": prediction["freshness_index"],
    })
    ml_results["history"] = ml_results["history"][-config.DASHBOARD_HISTORY_LIMIT :]

    new_status = actuator_controller.update(
        session["food_name"],
        flat,
        prediction,
        session.get("selected_sensors", []),
    )
    actuator_status.update(new_status)


def sensor_callback(topic: str, data: Dict[str, Any]) -> None:
    with state_lock:
        try:
            if topic == "foodmon/device/status":
                sensor_data["device_status"] = data.get("status", "online")
                sensor_data["timestamp"] = data.get("timestamp", int(time.time()))
            elif "temperature" in topic:
                sensor_data["temperature"] = data.get("value")
                sensor_data["timestamp"] = data.get("timestamp", int(time.time()))
            elif "humidity" in topic:
                sensor_data["humidity"] = data.get("value")
                sensor_data["timestamp"] = data.get("timestamp", int(time.time()))
            elif "gas" in topic:
                sensor_name = topic.split("/")[-1]
                if sensor_name in config.SELECTABLE_GAS_SENSORS:
                    sensor_data["gases"][sensor_name] = {
                        "value": data.get("value", 0),
                        "unit": data.get("unit", "ppm"),
                        "target_gas": config.SENSOR_METADATA[sensor_name].get("target", ""),
                    }
                    sensor_data["timestamp"] = data.get("timestamp", int(time.time()))

            session = session_manager.get()
            if session.get("status") == "running":
                append_cloud_reading()
                run_ml_and_control()

            emit_full_state()
            save_latest_data()
        except Exception as exc:
            print(f"sensor_callback error: {exc}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/system-control")
def system_control_page():
    return render_template("system_control.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


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
        {"id": sid, **config.SENSOR_METADATA[sid]} for sid in config.SELECTABLE_GAS_SENSORS
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
            print(f"Cloud session start failed: {exc}")

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
            print(f"Cloud session stop failed: {exc}")

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
        "sensor_data": sensor_data,
        "ml_results": ml_results,
        "actuator_status": actuator_status,
        "session": session_manager.get(),
    })


@socketio.on("connect")
def handle_connect():
    emit("connection_response", {"status": "connected"})
    emit("sensor_update", sensor_data)
    emit("ml_update", ml_results)
    emit("actuator_update", actuator_status)
    emit("session_update", session_manager.get())


@socketio.on("request_update")
def handle_request_update():
    emit("sensor_update", sensor_data)
    emit("ml_update", ml_results)
    emit("actuator_update", actuator_status)
    emit("session_update", session_manager.get())


def watchdog_loop():
    while True:
        time.sleep(5)
        if actuator_controller:
            actuator_controller.safe_shutdown_if_stale()


def init_system():
    global mqtt_handler, ml_engine, actuator_controller
    mqtt_handler = MQTTHandler(callback=sensor_callback)
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
