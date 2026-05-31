"""
Centralised configuration for RaspiPi 02 AI Hub.

All paths, environment variables, and performance tunables live here
so there is a single source of truth across CLI and Telegram modes.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root – resolved from this file's location
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
DATA_DIR: Path = PROJECT_ROOT / "data"
MODEL_DIR: Path = PROJECT_ROOT / "models"

# CSV data files
STUDY_LOG: Path = DATA_DIR / "study_log.csv"
FINANCE_LOG: Path = DATA_DIR / "finance_log.csv"
WEATHER_HISTORICAL: Path = DATA_DIR / "weather_zaragoza.csv"

# Weather model artifacts
WEATHER_TFLITE: Path = MODEL_DIR / "weather_model.tflite"
WEATHER_SCALER: Path = MODEL_DIR / "weather_scaler.pkl"
WEATHER_KERAS_MODEL: Path = MODEL_DIR / "weather_keras_model.keras"
WEATHER_SAVED_MODEL_DIR: Path = MODEL_DIR / "weather_saved_model"

# Writing corrector
WRITING_CACHE_DIR: Path = PROJECT_ROOT / "writing_corrector" / ".cache"
WRITING_INPUTS_DIR: Path = PROJECT_ROOT / "writing_corrector" / "inputs"
WRITING_OUTPUTS_DIR: Path = PROJECT_ROOT / "writing_corrector" / "outputs"

# ---------------------------------------------------------------------------
# Environment variables (with optional .env support)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_USER_ID: int = int(os.environ.get("TELEGRAM_USER_ID", "0"))
AEMET_API_KEY: str = os.environ.get("AEMET_API_KEY", "")

# ---------------------------------------------------------------------------
# Raspberry Pi Zero 2W performance tuning
# ---------------------------------------------------------------------------
CHAT_HISTORY_CAP: int = 10          # Max messages kept per session (RAM)
FORECAST_CACHE_SECONDS: int = 900   # 15 min between weather API calls
TFLITE_NUM_THREADS: int = 2         # Pi Zero 2W is dual-core Cortex-A53

# ---------------------------------------------------------------------------
# Weather model constants (shared by fetch / train / forecast)
# ---------------------------------------------------------------------------
WEATHER_LAT: float = 41.6488
WEATHER_LON: float = -0.8891

WEATHER_FEATURES: list[str] = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "cloud_cover",
    "surface_pressure",
]

WEATHER_TARGETS: list[int] = [0, 6, 24]
WEATHER_LOOK_BACK: int = 48

# AEMET stations for Zaragoza (Valdespartera → Aeropuerto fallback)
AEMET_STATION_VALDESPARTERA: str = "9434P"  # Valdespartera (primary)
AEMET_STATION_AEROPUERTO: str = "9434"      # Aeropuerto (fallback)
AEMET_MUNICIPIO_ID: str = "50297"           # Zaragoza municipio

# ---------------------------------------------------------------------------
# Gemini model name (single source of truth)
# ---------------------------------------------------------------------------
GEMINI_MODEL: str = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Ensure essential directories exist
# ---------------------------------------------------------------------------
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)
WRITING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
WRITING_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
