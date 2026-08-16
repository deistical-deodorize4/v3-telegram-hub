"""
AEMET weather module — Zaragoza (Valdespartera → Aeropuerto fallback).

Provides current observations + hourly municipio forecast through
the official AEMET OpenData API.  No ML, no TFLite — just the
Spanish state meteorological agency's professional forecast.

New in v2:
  - Feels-like temperature (sensTermica) from hourly forecast data
  - Wind direction as compass point (N/NE/E/SE/S/SW/W/NW)
  - Sunrise & sunset times (orto/ocaso) from the municipio forecast
  - UV Index from the daily municipio forecast (uvMax)
  - Weather warnings (avisos) for Aragón via CAP endpoint
  - Unicode temp sparkline for visual temperature trend
  - Weekday names (Mon/Tue/Wed/Thu/Fri/Sat/Sun) in forecast
  - Telegram Markdown formatting in morning brief
  - Richer on-demand display
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, date, timedelta
from typing import Any

import requests

# Ensure config is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    AEMET_API_KEY,
    AEMET_STATION_VALDESPARTERA,
    AEMET_STATION_AEROPUERTO,
    AEMET_MUNICIPIO_ID,
    AEMET_CCAA_ARAGON,
    FORECAST_CACHE_SECONDS,
)

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

SKY_CODES: dict[str, str] = {
    "1": "☀️ Clear",
    "2": "🌤 Mostly clear",
    "3": "⛅ Partly cloudy",
    "4": "☁️ Cloudy",
    "5": "☁️ Very cloudy",
    "6": "🌧 Overcast",
    "7": "🌦 Light rain",
    "11": "🌧 Moderate rain",
    "12": "🌧 Heavy rain",
    "13": "⛈ Showers",
    "14": "🌧 Heavy showers",
    "15": "⛈ Thunderstorm",
    "16": "🌨 Snow",
    "17": "🌨 Moderate snow",
    "18": "🌨 Heavy snow",
    "19": "🌫 Haze",
    "20": "🌫 Fog",
    "21": "🌫 Dust haze",
}

SKY_SHORT: dict[str, str] = {
    "1": "☀️",
    "2": "🌤",
    "3": "⛅",
    "4": "☁️",
    "5": "☁️",
    "6": "🌧",
    "7": "🌦",
    "11": "🌧",
    "12": "🌧",
    "13": "⛈",
    "14": "🌧",
    "15": "⛈",
    "16": "🌨",
    "17": "🌨",
    "18": "🌨",
    "19": "🌫",
    "20": "🌫",
    "21": "🌫",
}

WEEKDAY_EN: list[str] = [
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
]

UV_LEVELS: list[tuple[int, str]] = [
    (0, "Low"),
    (3, "Moderate"),
    (6, "High"),
    (8, "Very High"),
    (11, "Extreme"),
]

WARNING_LEVELS: dict[str, tuple[str, str]] = {
    "1": ("🟢", "Green (no risk)"),
    "2": ("🟡", "Yellow"),
    "3": ("🟠", "Orange"),
    "4": ("🔴", "Red"),
}

# Compass rose: 16 points
COMPASS_POINTS: list[str] = [
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
]


# ---------------------------------------------------------------------------
# AEMET API helper — two-step request/redirect pattern
# ---------------------------------------------------------------------------


def _aemet_get(endpoint: str, timeout: int = 15,
               data_timeout: int = 20, max_retries: int = 2) -> list | dict | None:
    """Make an AEMET API call, follow the data redirect, return the result.

    *timeout* applies to the initial API metadata request.
    *data_timeout* applies to the second request (the actual data shard),
    which can be much slower than the metadata step.

    Retries up to *max_retries* times on transient failures.  The AEMET
    servers are notoriously intermittent, so a retry or two avoids most
    "no data" errors in practice.
    """
    if not AEMET_API_KEY:
        return None

    base = "https://opendata.aemet.es/opendata/api"
    url = f"{base}{endpoint}"

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            # Step 1: get the data URL (metadata)
            r1 = requests.get(
                url,
                params={"api_key": AEMET_API_KEY},
                timeout=timeout,
            )
            r1.raise_for_status()
            body = r1.json()

            datos_url = body.get("datos")
            if not datos_url:
                return None

            # Step 2: fetch the actual data (data shard — can be very slow)
            r2 = requests.get(datos_url, timeout=data_timeout)
            r2.raise_for_status()
            return r2.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s, …

    return None


# ---------------------------------------------------------------------------
# In-memory cache (to avoid hammering slow AEMET endpoints)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_aemet_last_ok: float = 0.0  # timestamp of the most recent *fresh* AEMET success


def _cached_get(endpoint: str, timeout: int = 15,
                cache_key: str = "",
                failure_ttl: int = 300) -> list | dict | None:
    """Cached wrapper around ``_aemet_get``.

    Successful results are cached for ``FORECAST_CACHE_SECONDS`` (15 min).
    Failed calls (None) are cached for *failure_ttl* seconds (default 5 min)
    to avoid hammering slow endpoints on every request.

    **On failure, good cached data is never evicted.**  Stale data is returned
    if available, so a 30-minute AEMET outage doesn't wipe the cache and leave
    the user with nothing.
    """
    key = cache_key or endpoint
    now = time.time()
    global _aemet_last_ok

    # Return cached value if still fresh
    if key in _cache:
        ts, data = _cache[key]
        if data is not None:
            # Success data — return if within TTL
            if now - ts < FORECAST_CACHE_SECONDS:
                return data
        else:
            # Failure data — respect failure TTL
            if now - ts < failure_ttl:
                return None

    # Cache expired or missing — fetch fresh
    data = _aemet_get(endpoint, timeout=timeout)

    if data is not None:
        # Fresh success — update cache and timestamp
        _cache[key] = (now, data)
        _aemet_last_ok = now
        return data

    # AEMET failed — never evict existing good data
    if key in _cache and _cache[key][1] is not None:
        _, stale = _cache[key]  # return stale but better than nothing
        return stale

    # Nothing at all in cache either — store the failure marker
    _cache[key] = (now, None)
    return None


# ---------------------------------------------------------------------------
# Current observation
# ---------------------------------------------------------------------------


def _fetch_current() -> tuple[list | None, str]:
    """Fetch latest observation.

    Returns ``(parsed_data, station_name)`` where *station_name* is
    ``"Valdespartera"`` or ``"Aeropuerto"`` so the UI can show which
    source was used.
    """
    for sid, sname in [
        (AEMET_STATION_VALDESPARTERA, "Valdespartera"),
        (AEMET_STATION_AEROPUERTO, "Aeropuerto"),
    ]:
        data = _cached_get(
            f"/observacion/convencional/datos/estacion/{sid}",
            cache_key=f"obs_{sid}",
            failure_ttl=120,  # retry failed obs quickly (2 min)
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data, sname
    return None, "N/D"


def _parse_current(data: list) -> dict:
    """Extract the most recent observation row into a flat dict.

    AEMET normally returns observations sorted by time, but sort defensively
    by ``fint`` so we always pick the newest row regardless of server order.
    """
    def _obs_key(row: dict) -> str:
        return str(row.get("fint", ""))
    latest = max(data, key=_obs_key)
    return {
        "temp": float(latest.get("ta", latest.get("t", 0)) or 0),
        "humidity": float(latest.get("hr", 0) or 0),
        "wind_speed": float(latest.get("vv", 0) or 0),
        "pressure": float(latest.get("pres", 0) or 0),
        "precip": float(latest.get("prec", 0) or 0),
        "station": latest.get("sta", ""),
        "updated": latest.get("fint", ""),
    }


# ---------------------------------------------------------------------------
# Municipio hourly forecast — PARSE ALL FIELDS
# ---------------------------------------------------------------------------


def _fetch_forecast() -> list | None:
    """Fetch hourly forecast for Zaragoza municipio (7 days)."""
    return _cached_get(
        f"/prediccion/especifica/municipio/horaria/{AEMET_MUNICIPIO_ID}",
        cache_key="forecast",
    )


def _fetch_daily() -> list | None:
    """Fetch daily forecast for Zaragoza municipio (official min/max + UV)."""
    return _cached_get(
        f"/prediccion/especifica/municipio/diaria/{AEMET_MUNICIPIO_ID}",
        cache_key="forecast_daily",
    )


def _parse_daily_summary(data: list | None) -> dict[str, Any] | None:
    """Extract today's official min/max temperature and UV from the daily forecast.

    Returns ``{"fecha", "minima", "maxima", "uv"}`` (``uv`` may be None) or None.

    The daily prediction is the only AEMET source for the true 24h min/max:
    the hourly forecast's first day is *partial* (it only covers hours from
    ~08/09 local onward), so computing min/max from it misses the overnight low.
    """
    try:
        day = data[0]["prediccion"]["dia"][0]
    except (KeyError, IndexError, TypeError):
        return None

    temp = day.get("temperatura", {})
    try:
        maxima = float(temp.get("maxima", 0))
        minima = float(temp.get("minima", 0))
    except (TypeError, ValueError):
        return None

    uv = day.get("uvMax")
    try:
        uv = float(uv)
    except (TypeError, ValueError):
        uv = None

    return {
        "fecha": day.get("fecha", ""),
        "minima": minima,
        "maxima": maxima,
        "uv": uv,
    }


def _safe_dato(day: dict, key: str) -> list:
    """Extract the ``dato`` list from an AEMET field.

    AEMET sometimes returns ``{"dato": [...]}`` and sometimes returns
    ``[...]`` directly, depending on the server's mood.  Handle both.
    """
    val = day.get(key)
    if isinstance(val, dict):
        return val.get("dato", [])
    if isinstance(val, list):
        return val
    return []


def _parse_forecast(data: list) -> list:
    """Parse municipio forecast into a list of rich day dicts.

    Each day dict now includes:
      - fecha, weekday
      - temperatura, sensTermica, estadoCielo, probPrecipitacion, precipitacion
      - viento (direccion is a Spanish compass point: N/NE/E/SE/S/SO/O/NO)
      - humedad, probTormenta
      - orto (sunrise), ocaso (sunset)

    Source key names follow the AEMET API exactly: the humidity field is
    ``humedadRelativa`` and the hourly wind field is ``vientoAndRachaMax``.
    """
    try:
        days = data[0]["prediccion"]["dia"]
    except (KeyError, IndexError, TypeError):
        return []

    parsed = []
    for day in days:
        fecha_str = day.get("fecha", "")
        # Compute weekday name — try common AEMET date formats
        weekday = "?"
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                     "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(fecha_str, fmt).date()
                weekday = WEEKDAY_EN[dt.weekday()]
                break
            except (ValueError, TypeError):
                continue

        parsed.append({
            "fecha": fecha_str,
            "weekday": weekday,
            "temperatura": _safe_dato(day, "temperatura"),
            "sensTermica": _safe_dato(day, "sensTermica"),
            "estadoCielo": _safe_dato(day, "estadoCielo"),
            "probPrecipitacion": _safe_dato(day, "probPrecipitacion"),
            "precipitacion": _safe_dato(day, "precipitacion"),
            "viento": _safe_dato(day, "vientoAndRachaMax"),
            "humedad": _safe_dato(day, "humedadRelativa"),
            "probTormenta": _safe_dato(day, "probTormenta"),
            "orto": day.get("orto", ""),
            "ocaso": day.get("ocaso", ""),
        })
    return parsed


# ---------------------------------------------------------------------------
# Weather warnings (avisos) for Aragón
# ---------------------------------------------------------------------------


def _fetch_warnings() -> dict[str, Any] | None:
    """Fetch current weather warnings for Aragón via CAP endpoint.

    Uses short timeout — this can be slow.  Fails silently.
    """
    return _cached_get(
        f"/avisos_cap/ultimoelaborado/area/{AEMET_CCAA_ARAGON}",
        timeout=10,
        cache_key="warnings",
    )


def _parse_warnings(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse CAP warnings into a list of human-readable warning dicts.

    The CAP JSON structure is:
      alert.info[] = { language, event, urgency, severity, certainty,
                       headline, description, area[], ... }
    We only process spanish-language (es) entries.
    """
    if not raw or not isinstance(raw, dict):
        return []

    alerts: list[dict[str, Any]] = []
    alert_node = raw.get("alert", raw)
    if not isinstance(alert_node, dict):
        return []
    info_list = alert_node.get("info", [])

    # Normalise to list if single
    if isinstance(info_list, dict):
        info_list = [info_list]

    for info in info_list:
        if not isinstance(info, dict):
            continue
        if info.get("language", "").lower() not in ("es", "", "spa"):
            continue

        severity = info.get("severity", "").lower()  # Minor, Moderate, Severe, Extreme
        level_icon, level_label = _severity_to_warning(severity)

        alerts.append({
            "event": info.get("event", "Adverse weather"),
            "level_icon": level_icon,
            "level_label": level_label,
            "onset": info.get("onset", ""),
            "expires": info.get("expires", ""),
            "headline": info.get("headline", ""),
            "description": info.get("description", ""),
        })

    return alerts


def _severity_to_warning(severity: str) -> tuple[str, str]:
    """Map CAP severity to AEMET-style colour label."""
    mapping = {
        "extreme": ("🔴", "Red"),
        "severe": ("🟠", "Orange"),
        "moderate": ("🟡", "Yellow"),
        "minor": ("🟢", "Green"),
        "unknown": ("⚪", "Unknown"),
    }
    return mapping.get(severity, mapping["unknown"])


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _wind_degrees_to_compass(degrees: float | str) -> str:
    """Convert wind direction to a 16-point compass bearing.

    AEMET returns Spanish compass points (``"N"``, ``"NE"``, ``"E"``, ``"SE"``,
    ``"S"``, ``"SO"``, ``"O"``, ``"NO"``) or a numeric bearing in degrees.
    Returns the English equivalent (``"SW"``, ``"W"``, ``"NW"``, …).
    """
    if isinstance(degrees, str):
        # Spanish compass point → English equivalent
        es_map = {
            "N": "N", "NE": "NE", "E": "E", "SE": "SE",
            "S": "S", "SO": "SW", "O": "W", "NO": "NW",
        }
        key = degrees.strip().upper()
        if key in es_map:
            return es_map[key]
        if key in COMPASS_POINTS:
            return key
        # Might be "CALMA" or empty
        return "---"
    if degrees is None or degrees < 0:
        return "---"
    # Shift by half a sector (360/32 = 11.25 degrees) so the 16 sectors are centred
    index = round(degrees / 22.5) % 16
    return COMPASS_POINTS[index]


def _wind_arrow(degrees: float) -> str:
    """Return a wind direction arrow ←↗︎ etc based on degrees."""
    if degrees is None or degrees < 0:
        return "─"
    # 8 arrows for 8 compass sectors (each 45°)
    # N=↓ (blows south), NE=↙, E=←, SE=↖, S=↑, SW=↗, W=→, NW=↘
    arrows = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"]
    sector = round(degrees / 45) % 8
    return arrows[sector]


def _temp_sparkline(values: list[float], width: int = 8) -> str:
    """Create a unicode bar sparkline from a list of temperatures.

    Uses 8 block characters: ▁▂▃▄▅▆▇█
    Returns a string of *width* characters (default 8).
    """
    if not values:
        return ""

    # If we have more values than width, sample evenly
    n = len(values)
    if n >= width:
        step = n / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        # Linear interpolation to avoid misleading flat tail
        sampled = []
        for i in range(width):
            pos = (i / (width - 1)) * (n - 1)
            lo = int(pos)
            hi = min(lo + 1, n - 1)
            frac = pos - lo
            sampled.append(values[lo] * (1 - frac) + values[hi] * frac)

    mn = min(sampled)
    mx = max(sampled)
    span = mx - mn

    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    chars = []
    for v in sampled:
        if span == 0:
            idx = 3  # middle block if flat
        else:
            idx = int((v - mn) / span * (len(blocks) - 1))
            idx = max(0, min(idx, len(blocks) - 1))
        chars.append(blocks[idx])

    return "".join(chars)


def _get_slot(datos: list, hour: int, key: str = "value", default=None):
    """Extract a value from AEMETs ``dato`` list-of-dicts structure.

    ``periodo`` is the time key.  Hourly fields (temperatura, sensTermica,
    humedadRelativa…) use 2-digit periods like ``"09"``, while probabilities
    (probPrecipitacion, probTormenta…) use 4-digit intervals like ``"0814"``
    (08:00–14:00) that may roll over midnight (``"2002"``).  ``hora`` is tried
    as a fallback key, then interval matching.

    Usage::

        _get_slot(temps, 14)          → temperature at 14:00
        _get_slot(vientos, 14, "direccion") → wind direction at 14:00
    """
    # Exact 2-digit period match first
    for d in datos:
        try:
            raw = d.get("periodo", d.get("hora", -1))
            if int(raw) == hour:
                return d.get(key, default)
        except (ValueError, TypeError):
            continue

    # Interval periods like "0814" / "00-24" — find the span containing *hour*
    for d in datos:
        span = str(d.get("periodo", "")).replace("-", "")
        if len(span) != 4:
            continue
        try:
            start, end = int(span[:2]), int(span[2:])
        except ValueError:
            continue
        if end <= start:  # interval rolls over midnight
            end += 24
        h = hour + 24 if end >= 24 and hour < start else hour
        if start <= h < end:
            return d.get(key, default)
    return default


def _get_slot_str(datos: list, hour: int, key: str = "value") -> str:
    val = _get_slot(datos, hour, key)
    return str(val) if val is not None else ""


def _max_temp(datos: list) -> float:
    vals = []
    for d in datos:
        try:
            v = float(d.get("value", 0))
            vals.append(v)
        except (ValueError, TypeError):
            continue
    return max(vals) if vals else 0.0


def _min_temp(datos: list) -> float:
    vals = []
    for d in datos:
        try:
            v = float(d.get("value", 0))
            vals.append(v)
        except (ValueError, TypeError):
            continue
    return min(vals) if vals else 0.0


def _midday_sky(datos: list) -> str:
    """Best sky emoji around 14:00."""
    for d in datos:
        try:
            raw = d.get("periodo", d.get("hora", -1))
            if int(raw) == 14:
                return _sky_emoji(str(d.get("value", "")))
        except (ValueError, TypeError):
            continue
    return _sky_emoji("")


def _strip_night(code: str) -> str:
    """AEMET night sky codes append an 'n' (e.g. '17n' → '17')."""
    return code[:-1] if isinstance(code, str) and code.endswith("n") else code


def _sky_emoji(code: str) -> str:
    return SKY_CODES.get(_strip_night(code), "")


def _sky_short(code: str) -> str:
    return SKY_SHORT.get(_strip_night(code), "")


def _weekday_name(fecha_str: str) -> str:
    """Convert '2025-05-31' -> 'Sat'."""
    try:
        dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        return WEEKDAY_EN[dt.weekday()]
    except (ValueError, IndexError):
        return "?"


def _uv_label(uvi: float) -> str:
    """Return human-friendly UV level string (highest matching threshold)."""
    label = "Unknown"
    for threshold, l in reversed(UV_LEVELS):
        if uvi >= threshold:
            return l
    return label


def _format_time(iso_str: str) -> str:
    """Extract HH:MM from ISO datetime string or return as-is."""
    if not iso_str:
        return "--:--"
    # Try parsing ISO format
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(iso_str, fmt)
            return dt.strftime("%H:%M")
        except ValueError:
            continue
    # Maybe it's just HH:MM already
    if len(iso_str) >= 5 and ":" in iso_str[:5]:
        return iso_str[:5]
    return iso_str


def _format_date_short(iso_str: str) -> str:
    """Convert '2025-05-31T14:00:00' -> '31/05'."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(iso_str, fmt)
            return dt.strftime("%d-%m")
        except ValueError:
            continue
    return iso_str[:10] if len(iso_str) >= 10 else iso_str


def _cache_age_line() -> str:
    """Return a short note line if the data is from an earlier fetch.

    Returns something like ``"📡 data from 08:32 (cached)"`` or ``""``
    when the data is fresh (fetched within the last 120 seconds).
    """
    elapsed = time.time() - _aemet_last_ok
    if elapsed > 120 and _aemet_last_ok > 0:
        cache_time = datetime.fromtimestamp(_aemet_last_ok).strftime("%H:%M")
        return f"  data from {cache_time} (cached)"
    return ""


# ---------------------------------------------------------------------------
# Morning report (Markdown-friendly, pushed at 09:00)
# ---------------------------------------------------------------------------


def format_morning_report() -> str | None:
    """Morning weather brief — clean Markdown for Telegram."""
    current, station = _fetch_current()
    forecast_data = _fetch_forecast()
    days = _parse_forecast(forecast_data) if forecast_data else []
    daily = _parse_daily_summary(_fetch_daily())
    warnings = _parse_warnings(_fetch_warnings())

    if not current and not days:
        return None

    lines = []
    now = datetime.now()

    # ── Header ────────────────────────────────────────────────────────────
    lines.append("> Morning — Zaragoza")
    if station:
        lines.append(f"  {station} · AEMET")
    cache_note = _cache_age_line()
    if cache_note:
        lines.append(cache_note)
    lines.append("───")
    lines.append("")

    # ── Current conditions ────────────────────────────────────────────────
    if current:
        cur = _parse_current(current)
        time_str = now.strftime("%H:%M")
        lines.append(
            f"  Now ({time_str}): {cur['temp']:.1f}°C · "
            f"{cur['humidity']:.0f}% · {cur['wind_speed']:.1f} km/h"
        )
        lines.append("")

    # ── Today's forecast ──────────────────────────────────────────────────
    if days:
        today = days[0]
        fecha = today.get("fecha", "")
        weekday = today.get("weekday", "")
        temps = today.get("temperatura", [])
        sky = today.get("estadoCielo", [])
        precip = today.get("probPrecipitacion", [])
        viento = today.get("viento", [])
        humedad = today.get("humedad", [])
        orto = today.get("orto", "")
        ocaso = today.get("ocaso", "")

        # Official daily min/max — the hourly day 0 only covers hours from
        # ~08/09 local onward, so its own min/max misses the overnight low.
        if daily:
            t_min = daily["minima"]
            t_max = daily["maxima"]
        else:
            t_min = _min_temp(temps)
            t_max = _max_temp(temps)

        # Day header with sunrise-sunset
        header = f">> {weekday} {_format_date_short(fecha)}"
        if orto and ocaso:
            header += f" · {orto}-{ocaso}"
        lines.append(header)

        # +5h and +9h from now
        now_hour = now.hour
        for label, offset in [("+5h", 5), ("+9h", 9)]:
            h = (now_hour + offset) % 24
            t = _get_slot(temps, h)
            if t is None:
                continue
            parts = [f"▸ {label} ({h:02d}:00)", f"{float(t):.0f}°C"]
            h_val = _get_slot(humedad, h, "value", None)
            if h_val is not None:
                try:
                    parts.append(f"{int(float(h_val))}%")
                except (ValueError, TypeError):
                    pass
            v_dir = _get_slot(viento, h, "direccion", None)
            v_speed = _get_slot(viento, h, "velocidad", None)
            if v_speed is not None:
                try:
                    speed = float(v_speed)
                    if speed > 0:
                        compass = _wind_degrees_to_compass(v_dir or 0)
                        parts.append(f"{compass} {speed:.0f}")
                except (ValueError, TypeError):
                    pass
            lines.append("  " + " · ".join(parts))

        # Min/max + sparkline
        temp_values = []
        for d in temps:
            try:
                temp_values.append(float(d.get("value", 0)))
            except (ValueError, TypeError):
                continue
        spark = _temp_sparkline(temp_values, width=8)
        lines.append(
            f"  {t_min:.0f}–{t_max:.0f}°C  {spark}"
        )
        lines.append("")

        # ── UV Index ──────────────────────────────────────────────────────
        if daily and daily["uv"] is not None:
            uvi_val = daily["uv"]
            lines.append(f"  UV {uvi_val:.0f} ({_uv_label(uvi_val)})")
            lines.append("")

        # ── Warnings ──────────────────────────────────────────────────────
        if warnings:
            lines.append("  warnings")
            for w in warnings[:3]:  # max 3 in the brief
                icon = w["level_icon"]
                event = w["event"]
                level = w["level_label"]
                onset = _format_time(w["onset"]) if w.get("onset") else ""
                expires = _format_time(w["expires"]) if w.get("expires") else ""
                time_range = f" {onset}–{expires}" if onset and expires else ""
                lines.append(f"  {icon} {event} · {level}{time_range}")
            if len(warnings) > 3:
                lines.append(f"  … and {len(warnings) - 3} more")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# On-demand display (clean Markdown)
# ---------------------------------------------------------------------------


def format_ondemand() -> str | None:
    """Full detailed weather display — for the on-demand button.

    Returns a Markdown-formatted string focused on today's hourly forecast.
    """
    current, station = _fetch_current()
    forecast_data = _fetch_forecast()
    days = _parse_forecast(forecast_data) if forecast_data else []
    daily = _parse_daily_summary(_fetch_daily())
    warnings = _parse_warnings(_fetch_warnings())

    if not current and not days:
        return None

    lines = []
    now = datetime.now()

    # ── Header ────────────────────────────────────────────────────────────
    lines.append("> Zaragoza — AEMET")
    if station:
        lines.append(f"  {station}")
    cache_note = _cache_age_line()
    if cache_note:
        lines.append(cache_note)
    lines.append("───")
    lines.append("")

    # ── Current Conditions ────────────────────────────────────────────────
    if current:
        cur = _parse_current(current)
        lines.append(
            f"  Now ({now.strftime('%H:%M')}): {cur['temp']:.1f}°C · "
            f"{cur['humidity']:.0f}% · {cur['wind_speed']:.1f} km/h"
        )
        lines.append("")

    # ── UV Index ──────────────────────────────────────────────────────────
    if daily and daily["uv"] is not None:
        uvi_val = daily["uv"]
        lines.append(f"  UV {uvi_val:.0f} ({_uv_label(uvi_val)})")
        lines.append("")

    # ── Warnings ──────────────────────────────────────────────────────────
    if warnings:
        lines.append("  warnings")
        for w in warnings:
            icon = w["level_icon"]
            event = w["event"]
            level = w["level_label"]
            lines.append(f"  {icon} {event} · Level {level}")
            if w.get("headline"):
                lines.append(f"     {w['headline']}")
            onset = _format_time(w["onset"]) if w.get("onset") else ""
            expires = _format_time(w["expires"]) if w.get("expires") else ""
            if onset or expires:
                lines.append(f"     {onset} → {expires}" if onset and expires
                             else f"     {onset or expires}")
            if w.get("description"):
                # Truncate long descriptions
                desc = w["description"].strip()
                lines.append(f"     {desc[:120]}{'…' if len(desc) > 120 else ''}")
        lines.append("")

    # ── Hourly Forecast ───────────────────────────────────────────────────
    if days:
        today = days[0]
        fecha = today.get("fecha", "")
        weekday = today.get("weekday", "")
        temps = today.get("temperatura", [])
        sky = today.get("estadoCielo", [])
        precip = today.get("probPrecipitacion", [])
        humedad = today.get("humedad", [])
        viento = today.get("viento", [])
        orto = today.get("orto", "")
        ocaso = today.get("ocaso", "")

        # Official daily min/max (see format_morning_report for why)
        if daily:
            t_min = daily["minima"]
            t_max = daily["maxima"]
        else:
            t_min = _min_temp(temps)
            t_max = _max_temp(temps)

        # Day header
        day_label = f">> {weekday} {_format_date_short(fecha)}"
        if orto and ocaso:
            day_label += f" · {orto}-{ocaso}"
        lines.append(day_label)

        # Row for each hourly entry
        for entry in temps:
            raw_h = entry.get("periodo", entry.get("hora", 0))
            try:
                h = int(raw_h)
            except (ValueError, TypeError):
                h = 0
            t_val = entry.get("value", "")
            try:
                t_str = f"{float(t_val):.0f}°C"
            except (ValueError, TypeError):
                t_str = f"{t_val}°C"

            # Sky (emoji only, no text)
            s_code = _get_slot_str(sky, h, "value")
            s_short = _sky_short(s_code)

            # Precipitation
            p_val = _get_slot(precip, h, "value", 0)
            try:
                p_str = f"{int(float(p_val))}%"
            except (ValueError, TypeError):
                p_str = "0%"

            # Humidity
            h_val = _get_slot(humedad, h, "value", None)
            try:
                h_str = f"{int(float(h_val))}%" if h_val is not None else ""
            except (ValueError, TypeError):
                h_str = ""

            # Wind — only show if speed > 0
            v_dir = _get_slot(viento, h, "direccion", None)
            v_speed = _get_slot(viento, h, "velocidad", None)
            try:
                speed = float(v_speed) if v_speed is not None else 0
                if speed > 0:
                    compass = _wind_degrees_to_compass(v_dir or 0)
                    v_str = f"{compass} {speed:.0f}"
                else:
                    v_str = ""
            except (ValueError, TypeError):
                v_str = ""

            # Build parts, skipping empty
            parts = [f"{h:02d}h {t_str}"]
            if s_short:
                parts.append(s_short)
            parts.append(p_str)
            if h_str:
                parts.append(h_str)
            if v_str:
                parts.append(v_str)
            lines.append("  " + " · ".join(parts))

        # Min/max + sparkline
        temp_values = []
        for d in temps:
            try:
                temp_values.append(float(d.get("value", 0)))
            except (ValueError, TypeError):
                continue
        spark = _temp_sparkline(temp_values, width=8)
        lines.append(f"  {t_min:.0f}–{t_max:.0f}°C  {spark}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Print the on-demand report to stdout."""
    report = format_ondemand()
    if report:
        print(report)
    else:
        print("❌ Could not fetch AEMET data. Check your API key and internet.")


if __name__ == "__main__":
    main()
