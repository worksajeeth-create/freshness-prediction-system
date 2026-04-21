"""Actuator control logic.
Uses ESP-side actuator GPIO by default and can optionally use Raspberry Pi GPIO.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import config


def _load_gpio():
    try:
        import RPi.GPIO as GPIO  # type: ignore
        try:
            GPIO.setmode(GPIO.BCM)
            return True, GPIO
        except Exception as exc:
            print(f"GPIO present but unavailable: {exc}")
    except Exception:
        pass

    class MockGPIO:
        BCM = "BCM"
        OUT = "OUT"
        HIGH = 1
        LOW = 0

        @staticmethod
        def setmode(mode):
            return None

        @staticmethod
        def setup(pin, mode):
            return None

        @staticmethod
        def output(pin, state):
            return None

        @staticmethod
        def cleanup():
            return None

    return False, MockGPIO()


USE_REAL_GPIO, GPIO = _load_gpio()


class ActuatorController:
    def __init__(self, mqtt_handler=None):
        self.mode = config.ACTUATOR_CONTROL_MODE
        self.mqtt_handler = mqtt_handler
        self.pins = config.ACTUATOR_PINS
        self.cooler_on = False
        self.ventilation_level = "OFF"
        self.humidifier_on = False
        self.last_update_time = time.time()
        self.last_cooler_change = 0.0
        self.gas_exceed_count: Dict[str, int] = {}
        self.gas_exceed_threshold = 3

        self.use_pi_gpio = self.mode == "pi" and USE_REAL_GPIO
        if self.use_pi_gpio:
            GPIO.setmode(GPIO.BCM)
            for pin in self.pins.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            print("ActuatorController: Raspberry Pi GPIO mode")
        else:
            print("ActuatorController: ESP actuator mode (Pi GPIO disabled)")

    def update(self, food_name: str, sensor_data: Dict[str, float], ml_prediction: Dict[str, object], selected_sensors: List[str]):
        self.last_update_time = time.time()
        temperature = float(sensor_data.get("temperature", 20.0) or 20.0)
        humidity = float(sensor_data.get("humidity", 50.0) or 50.0)
        freshness = float(ml_prediction.get("freshness_index", 50.0) or 50.0)

        temp_optimal = config.TEMPERATURE_OPTIMAL.get(food_name, 4.0)
        humidity_optimal = config.HUMIDITY_OPTIMAL.get(food_name, 90.0)
        gases_exceeding = self._check_gas_thresholds(sensor_data, selected_sensors)

        self._control_cooler(temperature, temp_optimal, freshness, gases_exceeding)
        self._control_ventilation(gases_exceeding, freshness)
        self._control_humidifier(humidity, humidity_optimal)
        self._dispatch_state()
        return self.get_status()

    def _check_gas_thresholds(self, sensor_data: Dict[str, float], selected_sensors: List[str]) -> List[str]:
        exceeding = []
        for gas_name in selected_sensors:
            threshold = config.GAS_THRESHOLDS.get(gas_name)
            if threshold is None:
                continue
            value = float(sensor_data.get(gas_name, 0.0) or 0.0)
            if value > threshold:
                self.gas_exceed_count[gas_name] = self.gas_exceed_count.get(gas_name, 0) + 1
            else:
                self.gas_exceed_count[gas_name] = 0
            if self.gas_exceed_count[gas_name] >= self.gas_exceed_threshold:
                exceeding.append(gas_name)
        return exceeding

    def _control_cooler(self, current_temp: float, optimal_temp: float, freshness: float, gases_exceeding: List[str]):
        should_cool = self.cooler_on
        now = time.time()

        if current_temp > optimal_temp + 2:
            should_cool = True
        elif current_temp < optimal_temp - 1:
            should_cool = False

        if len(gases_exceeding) >= 2 and freshness < 70:
            should_cool = True

        if should_cool != self.cooler_on and (now - self.last_cooler_change) > 10:
            self.cooler_on = should_cool
            self.last_cooler_change = now
            if self.use_pi_gpio:
                GPIO.output(self.pins["cooler"], GPIO.HIGH if should_cool else GPIO.LOW)

    def _control_ventilation(self, gases_exceeding: List[str], freshness: float):
        num = len(gases_exceeding)
        if freshness < 30:
            level = "HIGH"
        elif num == 0:
            level = "OFF"
        elif num <= 2:
            level = "LOW"
        elif num <= 4:
            level = "MEDIUM"
        else:
            level = "HIGH"

        if level != self.ventilation_level:
            if self.use_pi_gpio:
                GPIO.output(self.pins["ventilation_low"], GPIO.LOW)
                GPIO.output(self.pins["ventilation_med"], GPIO.LOW)
                GPIO.output(self.pins["ventilation_high"], GPIO.LOW)
                if level == "LOW":
                    GPIO.output(self.pins["ventilation_low"], GPIO.HIGH)
                elif level == "MEDIUM":
                    GPIO.output(self.pins["ventilation_med"], GPIO.HIGH)
                elif level == "HIGH":
                    GPIO.output(self.pins["ventilation_high"], GPIO.HIGH)
            self.ventilation_level = level

    def _control_humidifier(self, current_humidity: float, optimal_humidity: float):
        should = self.humidifier_on
        if current_humidity < optimal_humidity - 5:
            should = True
        elif current_humidity >= optimal_humidity:
            should = False

        if should != self.humidifier_on:
            self.humidifier_on = should
            if self.use_pi_gpio:
                GPIO.output(self.pins["humidifier"], GPIO.HIGH if should else GPIO.LOW)

    def _dispatch_state(self):
        if self.mode == "esp" and self.mqtt_handler:
            payload = {
                "command": "set_actuators",
                "cooler": self.cooler_on,
                "ventilation": self.ventilation_level,
                "humidifier": self.humidifier_on,
                "timestamp": int(time.time()),
            }
            self.mqtt_handler.publish(config.ESP_ACTUATOR_COMMAND_TOPIC, payload)

    def get_status(self):
        return {
            "cooler": self.cooler_on,
            "ventilation": self.ventilation_level,
            "humidifier": self.humidifier_on,
            "mode": self.mode,
        }

    def safe_shutdown_if_stale(self):
        if time.time() - self.last_update_time > 60:
            self.cooler_on = False
            self.ventilation_level = "OFF"
            self.humidifier_on = False
            if self.use_pi_gpio:
                GPIO.output(self.pins["cooler"], GPIO.LOW)
                GPIO.output(self.pins["ventilation_low"], GPIO.LOW)
                GPIO.output(self.pins["ventilation_med"], GPIO.LOW)
                GPIO.output(self.pins["ventilation_high"], GPIO.LOW)
                GPIO.output(self.pins["humidifier"], GPIO.LOW)
            self._dispatch_state()

    def cleanup(self):
        if self.use_pi_gpio:
            GPIO.cleanup()
