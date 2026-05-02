"""MQTT handler for receiving sensor data and sending control commands."""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, List, Optional, Tuple

import paho.mqtt.client as mqtt

import config


class MQTTHandler:
    def __init__(
        self,
        callback: Optional[Callable[[str, dict], None]] = None,
        extra_topics: Optional[List[Tuple[str, int]]] = None,
    ):
        """
        Parameters
        ----------
        callback     : called with (topic, parsed_payload_dict) on every message.
        extra_topics : additional (topic, qos) tuples to subscribe to beyond
                       the defaults in config.MQTT_SENSOR_TOPICS.
                       Example: [("foodmon/actuators/status", 1)]
        """
        self.callback = callback
        self.extra_topics: List[Tuple[str, int]] = extra_topics or []
        self.connected = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self.client = mqtt.Client(client_id=config.MQTT_CLIENT_ID)
        if config.MQTT_USERNAME:
            self.client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
        self.client.on_connect    = self._on_connect
        self.client.on_message    = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        self.connected = rc == 0
        if self.connected:
            # Subscribe to default sensor topics
            for topic, qos in config.MQTT_SENSOR_TOPICS:
                client.subscribe(topic, qos=qos)
                print(f"[MQTT] Subscribed: {topic} (QoS {qos})")
            # Subscribe to any extra topics (e.g. foodmon/actuators/status)
            for topic, qos in self.extra_topics:
                client.subscribe(topic, qos=qos)
                print(f"[MQTT] Subscribed (extra): {topic} (QoS {qos})")
        else:
            print(f"[MQTT] Connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        print(f"[MQTT] Disconnected rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if self.callback:
                self.callback(msg.topic, payload)
        except Exception as exc:
            print(f"[MQTT] Message error on {msg.topic}: {exc}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def runner():
            while not self._stop_event.is_set():
                try:
                    self.client.connect(
                        config.MQTT_BROKER,
                        config.MQTT_PORT,
                        keepalive=config.MQTT_KEEPALIVE,
                    )
                    self.client.loop_forever()
                except Exception as exc:
                    print(f"[MQTT] Connection error: {exc} — retrying in 5 s")
                    time.sleep(5)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

    def publish(self, topic: str, payload: dict, qos: int = 1):
        if not self.connected:
            print(f"[MQTT] Publish skipped (not connected): {topic}")
            return
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def stop(self):
        self._stop_event.set()
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
