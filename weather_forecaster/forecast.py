"""
On-device weather inference for Zaragoza, Spain.

Fetches live data from Open-Meteo, runs the TFLite model,
and displays current conditions plus 6 h / 24 h predictions.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import joblib
import numpy as np
import requests
from ai_edge_litert.interpreter import Interpreter

# Ensure config is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    WEATHER_TFLITE,
    WEATHER_SCALER,
    WEATHER_LAT,
    WEATHER_LON,
    WEATHER_FEATURES,
    WEATHER_LOOK_BACK,
    FORECAST_CACHE_SECONDS,
    TFLITE_NUM_THREADS,
)

# ---------------------------------------------------------------------------
# TFLite model singleton – load once, reuse across calls.
# ---------------------------------------------------------------------------
_interpreter: Optional[Interpreter] = None
_last_load: float = 0


def _get_interpreter() -> Interpreter:
    global _interpreter, _last_load
    now = time.time()
    if _interpreter is None or (now - _last_load) > 3600:  # re-load hourly
        _interpreter = Interpreter(
            model_path=str(WEATHER_TFLITE),
            num_threads=TFLITE_NUM_THREADS,
        )
        _interpreter.allocate_tensors()
        _last_load = now
    return _interpreter


# ---------------------------------------------------------------------------
# Fetch live data
# ---------------------------------------------------------------------------

def fetch_recent() -> np.ndarray:
    print("Fetching latest weather data…")
    end = datetime.now()
    start = end - timedelta(hours=WEATHER_LOOK_BACK + 2)

    url = "https://api.open-meteo.com/v1/forecast"
    params: dict = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "hourly": ",".join(WEATHER_FEATURES),
        "timezone": "Europe/Madrid",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()["hourly"]

    rows = []
    for i in range(len(data["time"])):
        row = [data[feat][i] for feat in WEATHER_FEATURES]
        if None not in row:
            rows.append(row)

    if len(rows) < WEATHER_LOOK_BACK:
        raise ValueError(
            f"Not enough data: got {len(rows)} rows, need {WEATHER_LOOK_BACK}"
        )

    return np.array(rows[-WEATHER_LOOK_BACK:], dtype=np.float32)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def run_inference(input_data: np.ndarray, scaler) -> np.ndarray:
    normalized = scaler.transform(input_data)
    input_tensor = normalized.reshape(1, WEATHER_LOOK_BACK, len(WEATHER_FEATURES))

    interpreter = _get_interpreter()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    interpreter.set_tensor(input_details[0]["index"], input_tensor)
    interpreter.invoke()

    return interpreter.get_tensor(output_details[0]["index"])[0]


def denormalize(value: float, scaler, feature_name: str) -> float:
    idx = WEATHER_FEATURES.index(feature_name)
    min_val = scaler.data_min_[idx]
    max_val = scaler.data_max_[idx]
    return value * (max_val - min_val) + min_val


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_forecast(output: np.ndarray, scaler, raw_input: np.ndarray) -> None:
    f_idx = {name: i for i, name in enumerate(WEATHER_FEATURES)}

    current_temp = raw_input[-1][f_idx["temperature_2m"]]
    current_humidity = raw_input[-1][f_idx["relative_humidity_2m"]]
    current_wind = raw_input[-1][f_idx["wind_speed_10m"]]
    current_precip = raw_input[-1][f_idx["precipitation"]]
    current_pressure = raw_input[-1][f_idx["surface_pressure"]]
    current_clouds = raw_input[-1][f_idx["cloud_cover"]]
    current_apparent = raw_input[-1][f_idx["apparent_temperature"]]

    now_temp = denormalize(output[0], scaler, "temperature_2m")
    now_apparent = denormalize(output[1], scaler, "apparent_temperature")
    now_precip = denormalize(output[2], scaler, "precipitation")
    h6_temp = denormalize(output[3], scaler, "temperature_2m")
    h6_apparent = denormalize(output[4], scaler, "apparent_temperature")
    h6_precip = denormalize(output[5], scaler, "precipitation")
    h24_temp = denormalize(output[6], scaler, "temperature_2m")
    h24_apparent = denormalize(output[7], scaler, "apparent_temperature")
    h24_precip = denormalize(output[8], scaler, "precipitation")

    now = datetime.now().strftime("%d-%m-%Y %H:%M")
    print(f"\n{'=' * 45}")
    print(f"  🌤  Zaragoza Weather Forecast")
    print(f"  {now}")
    print(f"{'=' * 45}")
    print(f"\n📍 Current Conditions")
    print(f"  Temperature:  {current_temp:.1f}°C")
    print(f"  Feels like:   {current_apparent:.1f}°C")
    print(f"  Humidity:     {current_humidity:.0f}%")
    print(f"  Wind:         {current_wind:.1f} km/h")
    print(f"  Pressure:     {current_pressure:.1f} hPa")
    print(f"  Cloud cover:  {current_clouds:.0f}%")
    print(f"  Precipitation:{current_precip:.1f} mm")
    print(f"\n🔮 Model Predictions")
    print(f"  Now:   {now_temp:.1f}°C (feels {now_apparent:.1f}°C)  |  {now_precip:.2f}mm")
    print(f"  +6h:   {h6_temp:.1f}°C (feels {h6_apparent:.1f}°C)  |  {h6_precip:.2f}mm")
    print(f"  +24h:  {h24_temp:.1f}°C (feels {h24_apparent:.1f}°C)  |  {h24_precip:.2f}mm")

    trend = (
        "↑ warming" if h24_temp > current_temp + 1
        else "↓ cooling" if h24_temp < current_temp - 1
        else "→ stable"
    )
    print(f"\n  24h trend: {trend}")
    print(f"{'=' * 45}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    scaler = joblib.load(str(WEATHER_SCALER))
    raw_input = fetch_recent()
    output = run_inference(raw_input, scaler)
    display_forecast(output, scaler, raw_input)


if __name__ == "__main__":
    main()
