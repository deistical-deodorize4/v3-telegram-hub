"""
Train a Conv1D weather forecaster for Zaragoza and export to TFLite.

Run this ONCE on a desktop / laptop (not on the Pi) to produce the
model artifacts under models/.
"""

from __future__ import annotations

import os
import sys

os.environ["TF_USE_LEGACY_KERAS"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import joblib
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    WEATHER_HISTORICAL,
    WEATHER_SCALER,
    WEATHER_KERAS_MODEL,
    WEATHER_SAVED_MODEL_DIR,
    WEATHER_TFLITE,
    MODEL_DIR,
    WEATHER_LOOK_BACK,
    WEATHER_TARGETS,
)

# Training feature set (subset of WEATHER_FEATURES — excludes apparent_temperature,
# wind_gusts_10m, wind_direction_10m which the original pipeline used)
TRAIN_FEATURES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "cloud_cover",
    "surface_pressure",
]
TARGET_COLS = ["temperature_2m", "precipitation"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_and_clean() -> pd.DataFrame:
    print("Loading data…")
    df = pd.read_csv(WEATHER_HISTORICAL, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df[TRAIN_FEATURES] = df[TRAIN_FEATURES].interpolate(method="linear")
    print(f"Loaded {len(df)} hourly records")
    print(f"Date range: {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def normalize(df: pd.DataFrame) -> tuple[pd.DataFrame, MinMaxScaler]:
    print("Normalizing data…")
    scaler = MinMaxScaler()
    df[TRAIN_FEATURES] = scaler.fit_transform(df[TRAIN_FEATURES])
    joblib.dump(scaler, WEATHER_SCALER)
    print(f"✓ Scaler saved to {WEATHER_SCALER}")
    return df, scaler


# ---------------------------------------------------------------------------
# Sequence creation
# ---------------------------------------------------------------------------

def create_sequences(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    print("Creating sequences…")
    data = df[TRAIN_FEATURES].values
    target_indices = [TRAIN_FEATURES.index(c) for c in TARGET_COLS]
    max_target = max(WEATHER_TARGETS)

    X, y = [], []
    for i in range(WEATHER_LOOK_BACK, len(data) - max_target):
        X.append(data[i - WEATHER_LOOK_BACK : i])
        targets = []
        for t in WEATHER_TARGETS:
            for idx in target_indices:
                targets.append(data[i + t][idx])
        y.append(targets)

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)

    print(f"✓ Created {len(X)} sequences")
    print(f"  Input shape:  {X.shape}")
    print(f"  Output shape: {y.shape}  (6 targets)")
    return X, y


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def build_model(input_shape: tuple, output_size: int) -> tf.keras.Model:
    print("Building Conv1D model…")
    inputs = tf.keras.Input(shape=input_shape)

    x = tf.keras.layers.Conv1D(64, 3, activation="relu", padding="same")(inputs)
    x = tf.keras.layers.Conv1D(64, 3, activation="relu", padding="same")(x)
    x = tf.keras.layers.MaxPooling1D(pool_size=2)(x)
    x = tf.keras.layers.Dropout(0.2)(x)

    x = tf.keras.layers.Conv1D(32, 3, activation="relu", padding="same")(x)
    x = tf.keras.layers.GlobalAveragePooling1D()(x)
    x = tf.keras.layers.Dropout(0.2)(x)

    x = tf.keras.layers.Dense(32, activation="relu")(x)
    outputs = tf.keras.layers.Dense(output_size)(x)

    model = tf.keras.Model(inputs=inputs, outputs=outputs)
    model.compile(optimizer="adam", loss="mse", metrics=["mae"])
    model.summary()
    return model


def train(
    model: tf.keras.Model,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
) -> tf.keras.callbacks.History:
    print("\nTraining…")
    early_stop = tf.keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=10, restore_best_weights=True
    )
    history = model.fit(
        X_train,
        y_train,
        validation_data=(X_val, y_val),
        epochs=100,
        batch_size=32,
        callbacks=[early_stop],
        verbose=1,
    )

    model.save(WEATHER_KERAS_MODEL)
    print(f"✓ Keras model saved to {WEATHER_KERAS_MODEL}")
    return history


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(
    model: tf.keras.Model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    scaler: MinMaxScaler,
) -> None:
    print("\nEvaluating…")
    predictions = model.predict(X_test)

    temp_idx = TRAIN_FEATURES.index("temperature_2m")
    precip_idx = TRAIN_FEATURES.index("precipitation")
    temp_range = scaler.data_max_[temp_idx] - scaler.data_min_[temp_idx]
    precip_range = scaler.data_max_[precip_idx] - scaler.data_min_[precip_idx]

    labels = ["now_temp", "now_precip", "6h_temp", "6h_precip", "24h_temp", "24h_precip"]
    print("\n--- Prediction Errors (real units) ---")
    for i, label in enumerate(labels):
        scale = temp_range if "temp" in label else precip_range
        unit = "°C" if "temp" in label else "mm"
        mae = np.mean(np.abs(predictions[:, i] - y_test[:, i])) * scale
        print(f"  {label:<12}: MAE = {mae:.2f}{unit}")


# ---------------------------------------------------------------------------
# TFLite export
# ---------------------------------------------------------------------------

def export_tflite(model: tf.keras.Model) -> None:
    print("\nExporting to TFLite…")
    WEATHER_SAVED_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tf.saved_model.save(model, str(WEATHER_SAVED_MODEL_DIR))
    print("✓ SavedModel exported")

    converter = tf.lite.TFLiteConverter.from_saved_model(str(WEATHER_SAVED_MODEL_DIR))
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_model = converter.convert()

    WEATHER_TFLITE.parent.mkdir(parents=True, exist_ok=True)
    with WEATHER_TFLITE.open("wb") as f:
        f.write(tflite_model)

    size_kb = WEATHER_TFLITE.stat().st_size / 1024
    print(f"✓ Saved {WEATHER_TFLITE.name} ({size_kb:.1f} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    df = load_and_clean()
    df, scaler = normalize(df)
    X, y = create_sequences(df)

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    X_train, X_val, y_train, y_val = train_test_split(
        X_train, y_train, test_size=0.2, shuffle=False
    )

    print(f"\nTrain: {len(X_train)} | Val: {len(X_val)} | Test: {len(X_test)}")

    model = build_model(
        input_shape=(WEATHER_LOOK_BACK, len(TRAIN_FEATURES)),
        output_size=len(WEATHER_TARGETS) * len(TARGET_COLS),
    )

    train(model, X_train, y_train, X_val, y_val)
    evaluate(model, X_test, y_test, scaler)
    export_tflite(model)

    print("\n✓ All done! Model ready for Pi deployment.")


if __name__ == "__main__":
    main()
