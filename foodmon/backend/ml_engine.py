"""Machine Learning engine for freshness inference.

This version supports the current hardware: temperature, humidity, and the 7 selected gas sensors.
If no trained model exists for a given food, a safe heuristic dummy model is used.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, Optional

import joblib
import numpy as np

import config


class DummyModel:
    def predict(self, X):
        temp = X[0][0]
        humidity = X[0][1]
        gas_values = X[0][2:]
        gas_avg = float(np.mean(gas_values)) if len(gas_values) else 0.0
        freshness = max(0.0, min(100.0, 100.0 - (gas_avg / 50.0)))
        if 0 <= temp <= 5 and 80 <= humidity <= 95:
            freshness = min(100.0, freshness + 10.0)
        return [freshness]


class MLEngine:
    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self.models: Dict[str, object] = {}
        self.feature_names = ["temperature", "humidity"] + config.SELECTABLE_GAS_SENSORS

    def load_models(self):
        for food in config.SUPPORTED_FOODS:
            path = os.path.join(self.model_dir, f"{food}.pkl")
            if os.path.exists(path):
                try:
                    self.models[food] = joblib.load(path)
                    print(f"Loaded model for {food}")
                except Exception as exc:
                    print(f"Model load failed for {food}: {exc}")
                    self.models[food] = DummyModel()
            else:
                self.models[food] = DummyModel()

    def predict(self, food_name: str, sensor_data: Dict[str, float], selected_sensors=None) -> Optional[Dict[str, object]]:
        model = self.models.get(food_name)
        if not model:
            return None

        features = []
        active_gases = set(selected_sensors or config.SELECTABLE_GAS_SENSORS)
        for feature_name in self.feature_names:
            if feature_name in config.SELECTABLE_GAS_SENSORS and feature_name not in active_gases:
                features.append(0.0)
                continue
            features.append(float(sensor_data.get(feature_name, 0.0) or 0.0))

        X = np.array([features], dtype=float)
        try:
            freshness_index = float(model.predict(X)[0])
        except Exception as exc:
            print(f"Prediction error: {exc}")
            freshness_index = 50.0

        if freshness_index >= config.FRESHNESS_THRESHOLDS["fresh"]:
            status = "Fresh"
            color = "#4CAF50"
        elif freshness_index >= config.FRESHNESS_THRESHOLDS["half_spoiled"]:
            status = "Half-Spoiled"
            color = "#FF9800"
        else:
            status = "Spoiled"
            color = "#F44336"

        remaining_days = max(0.0, round((freshness_index - 40.0) * 0.1, 1))

        return {
            "freshness_index": round(freshness_index, 1),
            "status": status,
            "status_color": color,
            "remaining_days": remaining_days,
            "timestamp": datetime.now().isoformat(),
        }
