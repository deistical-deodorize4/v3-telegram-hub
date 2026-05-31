"""
Standalone TFLite export script.

Loads a trained Keras model and converts it to TFLite with DEFAULT
optimisation.  Run this if you re-train the model and need to re-export.
"""

from __future__ import annotations

import os
import sys

os.environ["TF_USE_LEGACY_KERAS"] = "1"

import tensorflow as tf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WEATHER_KERAS_MODEL, WEATHER_SAVED_MODEL_DIR, WEATHER_TFLITE  # noqa: E402

print("Loading Keras model…")
model = tf.keras.models.load_model(str(WEATHER_KERAS_MODEL))
print("✓ Model loaded")

print("\nSaving in TF SavedModel format…")
WEATHER_SAVED_MODEL_DIR.mkdir(parents=True, exist_ok=True)
tf.saved_model.save(model, str(WEATHER_SAVED_MODEL_DIR))
print("✓ SavedModel saved")

print("\nConverting to TFLite…")
converter = tf.lite.TFLiteConverter.from_saved_model(str(WEATHER_SAVED_MODEL_DIR))
converter.optimizations = [tf.lite.Optimize.DEFAULT]
converter.target_spec.supported_ops = [
    tf.lite.OpsSet.TFLITE_BUILTINS,
    tf.lite.OpsSet.SELECT_TF_OPS,
]
converter._experimental_lower_tensor_list_ops = False
tflite_model = converter.convert()

WEATHER_TFLITE.parent.mkdir(parents=True, exist_ok=True)
with WEATHER_TFLITE.open("wb") as f:
    f.write(tflite_model)

size_kb = WEATHER_TFLITE.stat().st_size / 1024
print(f"✓ Saved {WEATHER_TFLITE.name} ({size_kb:.1f} KB)")
print("\n✓ Done! Model ready for Pi deployment.")
