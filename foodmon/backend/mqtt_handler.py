"""MQTT handler for receiving sensor data and sending control commands."""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

import config


class MQTTHandler:
    def __init__(self, callback: Optional[Callable[[str, dict], None]] = None):
        self.callback = callback
        self.connected = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self.client = mqtt.Client(client_id=config.MQTT_CLIENT_ID)
        if config.MQTT_USERNAME:
            self.client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        self.connected = rc == 0
        if self.connected:
            for topic, qos in config.MQTT_SENSOR_TOPICS:
                client.subscribe(topic, qos=qos)
                print(f"Subscribed to: {topic} (QoS {qos})")
        else:
            print(f"MQTT connect failed with code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        print(f"Disconnected from MQTT broker. rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if self.callback:
                self.callback(msg.topic, payload)
        except Exception as exc:
            print(f"MQTT message error: {exc}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def runner():
            while not self._stop_event.is_set():
                try:
                    self.client.connect(config.MQTT_BROKER, config.MQTT_PORT, keepalive=config.MQTT_KEEPALIVE)
                    self.client.loop_forever()
                except Exception as exc:
                    print(f"MQTT connection error: {exc}")
                    time.sleep(5)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

    def publish(self, topic: str, payload: dict, qos: int = 1):
        if not self.connected:
            print("MQTT publish skipped: not connected")
            return
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def stop(self):
        self._stop_event.set()
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
