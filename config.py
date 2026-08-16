"""
Centralised configuration for pi02w-hub.

All paths, environment variables, and performance tunables live here
so there is a single source of truth across CLI and Telegram modes.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Project root – resolved from this file's location
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
DATA_DIR: Path = PROJECT_ROOT / "data"

# Temporary directory (maps to tmpfs on Pi — protects SD card from wear)
TEMP_DIR: Path = Path("/tmp") / PROJECT_ROOT.name

# Price watch
PRICE_WATCH_INTERVAL_SECONDS: int = 3600  # hourly checks

# Timezone
TIMEZONE: ZoneInfo = ZoneInfo("Europe/Madrid")

# ---------------------------------------------------------------------------
# Environment variables (with optional .env support)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID: int = int(os.environ.get("TELEGRAM_USER_ID", "0"))
AEMET_API_KEY: str = os.environ.get("AEMET_API_KEY", "")

# ---------------------------------------------------------------------------
# Raspberry Pi Zero 2W performance tuning
# ---------------------------------------------------------------------------
FORECAST_CACHE_SECONDS: int = 900   # 15 min between weather API calls

# AEMET stations for Zaragoza (Valdespartera → Aeropuerto fallback)
AEMET_STATION_VALDESPARTERA: str = "9434P"  # Valdespartera (primary)
AEMET_STATION_AEROPUERTO: str = "9434"      # Aeropuerto (fallback)
AEMET_MUNICIPIO_ID: str = "50297"           # Zaragoza municipio
AEMET_CCAA_ARAGON: str = "62"              # Código CCAA Aragón (avisos endpoint)

# ---------------------------------------------------------------------------
# Printer (raw TCP/JetDirect – needs ghostscript to render PDF to PCL)
# ---------------------------------------------------------------------------
PRINTER_ADDR: str = os.environ.get("PRINTER_ADDR", "")

# ---------------------------------------------------------------------------
# Lens tracker
# ---------------------------------------------------------------------------
LENS_DATA: Path = DATA_DIR / "lens_tracker.json"

# ---------------------------------------------------------------------------
# Ensure essential directories exist
# ---------------------------------------------------------------------------
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

