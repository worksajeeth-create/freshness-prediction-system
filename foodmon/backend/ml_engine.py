"""Machine Learning engine for freshness inference.

Two prediction paths are supported, selected automatically per food:

1. GENERIC single-model foods (unchanged from before):
   models/<food>.pkl — one object with .predict(X) -> [freshness_index (0-100)].
   Falls back to a heuristic DummyModel if no trained model file exists.

2. RICE — classifier + trend-extrapolation RSL (no second regressor):
   models/rice/classifier.pkl    — RandomForestClassifier -> status "Fresh"/"Spoiled"
   models/rice/scaler_clf.pkl    — StandardScaler for the classifier's 18 features
   models/rice/label_encoder.pkl — maps the classifier's 0/1 output back to a label

   A separate RandomForestRegressor (sensors + hours_elapsed -> RSL hours) was
   tried and evaluated with proper LOBO cross-validation + a held-out test
   batch. Two versions were tested:
     v1 (hours_elapsed as a feature):  hours_elapsed alone was ~94% of the
        model's feature_importances_ -- it had learned to count down a
        session clock, not read the food.
     v2 (hours_elapsed removed):       forced the model to use only sensor
        patterns, but LOBO CV R^2 = -0.18 and held-out test R^2 = -0.11 --
        worse than predicting the mean RSL every time. 5 training batches is
        not enough data for a point-in-time sensor->hours regression.
   Both results are kept in rice_shelf_life_ml.ipynb as documented, evaluated
   negative findings (Plot 9/10 + LOBO CV cells), not silently dropped.

   Instead, remaining life is estimated the way Remaining Useful Life (RUL)
   is estimated in predictive-maintenance/prognostics: track THIS session's
   own freshness_index trend (from the validated classifier, 82% LOBO CV /
   90% held-out test accuracy) over a rolling time window, fit a straight
   line, and extrapolate to the point where it crosses the classifier's own
   Fresh/Spoiled decision boundary (freshness_index = 50). This uses only
   the model that actually generalises, and adapts per-session to each
   sample's real decay rate instead of a fixed model trained on 4-5 batches.
"""
from __future__ import annotations

import os
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

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


class RiceTrendModel:
    """Rice status classifier + trend-extrapolation remaining-life estimator.

    Call `reset()` whenever a new monitoring session starts (both the
    rolling-mean sensor buffers AND the trend history must not carry over
    from a previous session), then call `predict(sensor_data, hours_elapsed)`
    on every new reading.
    """

    # Order matters — this MUST match the training notebook's SENSOR_COLS.
    SENSOR_COLS = [
        "temperature", "humidity",
        "mq2_ppm", "mq3_ppm", "mq4_ppm",
        "mq135_ppm", "mq136_ppm", "mq137_ppm", "co2",
    ]
    ROLL_WINDOW = 5  # 5-reading rolling mean, same as training

    # ── Trend-extrapolation tuning ────────────────────────────────────────
    TREND_WINDOW_HOURS = 0.5   # fit the line to only the last 30 minutes of readings
    MIN_TREND_POINTS = 5       # need at least this many points before trusting a slope
    SPOILED_THRESHOLD = 50.0   # matches the classifier's own Fresh/Spoiled decision boundary
    MAX_DISPLAY_HOURS = 24.0   # cap so a flat/rising trend doesn't show an absurd/huge number

    # Maps our live sensor_data dict keys -> the column names the model expects
    _KEY_MAP = {
        "temperature": "temperature",
        "humidity":    "humidity",
        "mq2":   "mq2_ppm",
        "mq3":   "mq3_ppm",
        "mq4":   "mq4_ppm",
        "mq135": "mq135_ppm",
        "mq136": "mq136_ppm",
        "mq137": "mq137_ppm",
        "co2":   "co2",
    }

    def __init__(self, model_dir: str):
        self.classifier    = joblib.load(os.path.join(model_dir, "classifier.pkl"))
        self.scaler_clf    = joblib.load(os.path.join(model_dir, "scaler_clf.pkl"))
        self.label_encoder = joblib.load(os.path.join(model_dir, "label_encoder.pkl"))

        classes = list(self.label_encoder.classes_)
        self._fresh_idx = classes.index("Fresh") if "Fresh" in classes else 0

        self.roll_cols    = [f"{c}_rm" for c in self.SENSOR_COLS]
        self.clf_features = self.SENSOR_COLS + self.roll_cols  # 18

        self.reset()

    # ── Spoilage latch ────────────────────────────────────────────────────
    # Spoilage is biologically irreversible, but the classifier only sees
    # CURRENT gas concentration, which actuators (ventilation clearing the
    # chamber, cooling slowing off-gassing) can temporarily reduce — causing
    # the raw per-tick classification to flip back to "Fresh" even though the
    # food itself hasn't un-spoiled. SPOILED_CONFIRM_COUNT consecutive raw
    # "Spoiled" readings (~6s at a 2s publish rate) latches the food as
    # Spoiled for the rest of the session; it can only become "Fresh" again
    # by starting a new session (see reset()).
    SPOILED_CONFIRM_COUNT = 3

    def reset(self) -> None:
        """Clear rolling-mean history AND trend history. Call at session start."""
        self._buffers: Dict[str, Deque[float]] = {
            col: deque(maxlen=self.ROLL_WINDOW) for col in self.SENSOR_COLS
        }
        self._trend_history: Deque[Tuple[float, float]] = deque()  # (hours_elapsed, freshness_index)
        self._last_remaining_hours: Optional[float] = None
        self._last_freshness_index: Optional[float] = None
        self._consecutive_spoiled_count = 0
        self._spoiled_latched = False

    def _build_feature_row(self, sensor_data: Dict[str, float]) -> Dict[str, float]:
        row: Dict[str, float] = {}
        for live_key, model_col in self._KEY_MAP.items():
            value = float(sensor_data.get(live_key, 0.0) or 0.0)
            self._buffers[model_col].append(value)
            row[model_col] = value
            row[f"{model_col}_rm"] = float(np.mean(self._buffers[model_col]))
        return row

    def _extrapolate_remaining_hours(self, hours_elapsed: float, freshness_index: float) -> Optional[float]:
        """Fit a line to this session's recent freshness_index trend and
        extrapolate to when it crosses SPOILED_THRESHOLD. Returns None if
        there isn't enough trend history yet to trust a slope."""
        self._trend_history.append((hours_elapsed, freshness_index))
        cutoff = hours_elapsed - self.TREND_WINDOW_HOURS
        while self._trend_history and self._trend_history[0][0] < cutoff:
            self._trend_history.popleft()

        if len(self._trend_history) < self.MIN_TREND_POINTS:
            return None  # not enough data yet — dashboard shows "Calculating..."

        xs = np.array([p[0] for p in self._trend_history])
        ys = np.array([p[1] for p in self._trend_history])
        slope, intercept = np.polyfit(xs, ys, 1)

        if slope >= -1e-6:
            # Flat or rising confidence over this window — could be genuine
            # (nothing declining yet) or just a brief plateau in the
            # classifier's tree-based output. Rather than snapping the
            # displayed estimate up to MAX_DISPLAY_HOURS (which would look
            # like remaining life jumping around on the dashboard), hold the
            # last computed estimate steady. Only once real trend history
            # exists do we report the full-horizon cap.
            result = self._last_remaining_hours if self._last_remaining_hours is not None else self.MAX_DISPLAY_HOURS
        else:
            hours_at_threshold = (self.SPOILED_THRESHOLD - intercept) / slope
            remaining = hours_at_threshold - hours_elapsed
            result = max(0.0, min(remaining, self.MAX_DISPLAY_HOURS))

        # Monotonicity constraint: remaining life should not visibly increase
        # during an active session — a jump upward is short-window noise from
        # the classifier's tree-based probability output, not genuine
        # improvement in the food. This is the same stabilisation technique
        # used for noisy Remaining-Useful-Life estimates in predictive
        # maintenance. Once a value has been reported, cap any later estimate
        # to be no higher than it.
        if self._last_remaining_hours is not None:
            result = min(result, self._last_remaining_hours)

        self._last_remaining_hours = result
        return result

    def predict(self, sensor_data: Dict[str, float], hours_elapsed: float) -> Dict[str, object]:
        row = self._build_feature_row(sensor_data)

        # Wrapped in a DataFrame (with the same column names/order used when
        # scaler_clf.pkl was fit) purely to silence sklearn's cosmetic
        # "X does not have valid feature names" warning — the values and
        # result are identical to passing a plain ndarray either way.
        X_clf = pd.DataFrame([[row[f] for f in self.clf_features]], columns=self.clf_features)
        X_clf_scaled = self.scaler_clf.transform(X_clf)

        proba = self.classifier.predict_proba(X_clf_scaled)[0]
        freshness_index = float(proba[self._fresh_idx] * 100.0)

        encoded_status = self.classifier.predict(X_clf_scaled)[0]
        raw_status = str(self.label_encoder.inverse_transform([encoded_status])[0])

        # ── Spoilage latch (irreversibility) ──────────────────────────────
        # Debounce: only count SPOILED_CONFIRM_COUNT consecutive raw "Spoiled"
        # readings as a real confirmation, so one noisy borderline tick can't
        # latch prematurely. Once confirmed, status stays "Spoiled" for the
        # rest of the session no matter what the sensors do afterward (e.g.
        # ventilation clearing the chamber) — food doesn't un-spoil.
        if raw_status == "Spoiled":
            self._consecutive_spoiled_count += 1
        else:
            self._consecutive_spoiled_count = 0
        if self._consecutive_spoiled_count >= self.SPOILED_CONFIRM_COUNT:
            self._spoiled_latched = True

        status = "Spoiled" if self._spoiled_latched else raw_status

        # Once latched, also stop the on-screen freshness_index from
        # climbing back up just because the chamber got ventilated — keep it
        # consistent with the "can't un-spoil" story the status/color tell.
        if self._spoiled_latched and self._last_freshness_index is not None:
            freshness_index = min(freshness_index, self._last_freshness_index)
        self._last_freshness_index = freshness_index

        # Already Spoiled => 0 hours remaining, by definition — no need to
        # extrapolate. This also means status and remaining_hours can never
        # contradict each other (freshness_index < 50 <=> status == Spoiled
        # <=> remaining_hours == 0), unlike the old classifier+regressor pair.
        if status == "Spoiled":
            remaining_hours: Optional[float] = 0.0
        else:
            remaining_hours = self._extrapolate_remaining_hours(hours_elapsed, freshness_index)

        color = "#4CAF50" if status == "Fresh" else "#F44336"

        return {
            "freshness_index": round(freshness_index, 1),
            "status": status,
            "status_color": color,
            "remaining_days": round(remaining_hours / 24.0, 2) if remaining_hours is not None else None,
            "remaining_hours": round(remaining_hours, 1) if remaining_hours is not None else None,
            "timestamp": datetime.now().isoformat(),
        }


class MLEngine:
    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self.models: Dict[str, object] = {}
        self.feature_names = ["temperature", "humidity"] + config.SELECTABLE_GAS_SENSORS

    def load_models(self):
        for food in config.SUPPORTED_FOODS:
            if food == "rice":
                rice_dir = os.path.join(self.model_dir, "rice")
                try:
                    self.models["rice"] = RiceTrendModel(rice_dir)
                    print(f"Loaded rice classifier + trend-extrapolation RSL pipeline from {rice_dir}")
                except Exception as exc:
                    print(f"Rice model load failed ({exc}) — falling back to dummy model")
                    self.models["rice"] = DummyModel()
                continue

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

    def reset_food_state(self, food_name: str) -> None:
        """Call this whenever a new session starts (see app.py start_session).

        Needed for stateful models like RiceTrendModel whose rolling-mean
        buffers must not leak data across sessions. No-op for stateless models.
        """
        model = self.models.get(food_name)
        if isinstance(model, RiceTrendModel):
            model.reset()

    def predict(
        self,
        food_name: str,
        sensor_data: Dict[str, float],
        selected_sensors=None,
        hours_elapsed: float = 0.0,
    ) -> Optional[Dict[str, object]]:
        model = self.models.get(food_name)
        if not model:
            return None

        if isinstance(model, RiceTrendModel):
            active_gases = set(selected_sensors or config.SELECTABLE_GAS_SENSORS)
            missing = [g for g in config.SELECTABLE_GAS_SENSORS if g not in active_gases]
            if missing:
                print(
                    f"[ml_engine] Rice model was trained on all 7 gas sensors — "
                    f"missing {missing} will be sent as 0.0 and may hurt accuracy."
                )
            filtered = dict(sensor_data)
            for gas in missing:
                filtered[gas] = 0.0
            return model.predict(filtered, hours_elapsed)

        # ── Generic single-model path (unchanged) ────────────────────────
        features: List[float] = []
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
            "remaining_hours": round(remaining_days * 24.0, 1),
            "timestamp": datetime.now().isoformat(),
        }
