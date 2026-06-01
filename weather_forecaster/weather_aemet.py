"""
AEMET weather module — Zaragoza (Valdespartera → Aeropuerto fallback).

Provides current observations + hourly municipio forecast through
the official AEMET OpenData API.  No ML, no TFLite — just the
Spanish state meteorological agency's professional forecast.

New in v2:
  - Feels-like temperature (sensTermica) from hourly forecast data
  - Wind direction as compass point (N/NE/E/SE/S/SW/W/NW)
  - Sunrise & sunset times (orto/ocaso) from the municipio forecast
  - UV Index from AEMET specific prediction endpoint
  - Weather warnings (avisos) for Aragón via CAP endpoint
  - Unicode temp sparkline for visual temperature trend
  - Weekday names (lun/mar/mié/jue/vie/sáb/dom) in forecast
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
    AEMET_UVI_LOCALIDAD,
    FORECAST_CACHE_SECONDS,
)

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

SKY_CODES: dict[str, str] = {
    "1": "☀️ Despejado",
    "2": "🌤 Poco nuboso",
    "3": "⛅ Intervalos nubosos",
    "4": "☁️ Nuboso",
    "5": "☁️ Muy nuboso",
    "6": "🌧 Cubierto",
    "7": "🌦 Lluvia débil",
    "11": "🌧 Lluvia moderada",
    "12": "🌧 Lluvia fuerte",
    "13": "⛈ Chubascos",
    "14": "🌧 Chubascos fuertes",
    "15": "⛈ Tormenta",
    "16": "🌨 Nieve",
    "17": "🌨 Nieve moderada",
    "18": "🌨 Nieve fuerte",
    "19": "🌫 Bruma",
    "20": "🌫 Niebla",
    "21": "🌫 Calima",
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

WEEKDAY_ES: list[str] = [
    "lun", "mar", "mié", "jue", "vie", "sáb", "dom",
]

UV_LEVELS: list[tuple[int, str]] = [
    (0, "Bajo"),
    (3, "Moderado"),
    (6, "Alto"),
    (8, "Muy Alto"),
    (11, "Extremo"),
]

WARNING_LEVELS: dict[str, tuple[str, str]] = {
    "1": ("🟢", "Verde (sin riesgo)"),
    "2": ("🟡", "Amarillo"),
    "3": ("🟠", "Naranja"),
    "4": ("🔴", "Rojo"),
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
    """Extract the most recent observation row into a flat dict."""
    latest = data[-1]  # last entry = most recent
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
      - viento (with direccion in degrees), humedad, probTormenta
      - orto (sunrise), ocaso (sunset)
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
                weekday = WEEKDAY_ES[dt.weekday()]
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
            "viento": _safe_dato(day, "viento"),
            "humedad": _safe_dato(day, "humedad"),
            "probTormenta": _safe_dato(day, "probTormenta"),
            "orto": day.get("orto", ""),
            "ocaso": day.get("ocaso", ""),
        })
    return parsed


# ---------------------------------------------------------------------------
# UV Index
# ---------------------------------------------------------------------------


def _fetch_uvi() -> dict[str, Any] | None:
    """Fetch UV Index predictions for today (day=0).

    Uses short timeout — this endpoint is often slow/unavailable.
    Fails silently so the rest of the report still renders.
    """
    data = _cached_get("/prediccion/especifica/uvi/0", timeout=10, cache_key="uvi")
    if not data or not isinstance(data, list):
        return None
    # Find the entry for Zaragoza
    for entry in data:
        if not isinstance(entry, dict):
            continue
        localidad = entry.get("localidad", entry.get("nombre", "")).strip().lower()
        if localidad == AEMET_UVI_LOCALIDAD.strip().lower():
            return entry
    # Fall back to the first dict entry if no match (defensive)
    for entry in data:
        if isinstance(entry, dict):
            return entry
    return None


def _parse_uvi(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Parse UV index response into a clean dict."""
    if not raw or not isinstance(raw, dict):
        return None
    try:
        uvi = float(raw.get("uvi", raw.get("valor", 0)) or 0)
    except (ValueError, TypeError):
        return None

    level = "Desconocido"
    for threshold, label in UV_LEVELS:
        if uvi >= threshold:
            level = label

    return {
        "uvi": uvi,
        "level": level,
        "localidad": raw.get("localidad", raw.get("nombre", "")),
    }


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
            "event": info.get("event", "Fenómeno adverso"),
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
        "extreme": ("🔴", "Rojo"),
        "severe": ("🟠", "Naranja"),
        "moderate": ("🟡", "Amarillo"),
        "minor": ("🟢", "Verde"),
        "unknown": ("⚪", "Desconocido"),
    }
    return mapping.get(severity, mapping["unknown"])


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _wind_degrees_to_compass(degrees: float | str) -> str:
    """Convert wind direction to 16-point compass bearing.

    Accepts either numeric degrees or a compass string (``"N"``, ``"NE"``…).
    Returns something like ``"NE"``, ``"SSW"``, etc.
    """
    if isinstance(degrees, str):
        # Already a compass point — validate and return
        if degrees.upper() in COMPASS_POINTS:
            return degrees.upper()
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
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
        # Pad if fewer
        while len(sampled) < width:
            sampled.append(sampled[-1] if sampled else 0)

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

    AEMET uses ``periodo`` (2-digit string like ``"09"``) as the time key,
    but sometimes returns ``hora`` instead.  Try both.

    Usage::

        _get_slot(temps, 14)          → temperature at 14:00
        _get_slot(vientos, 14, "direccion") → wind direction at 14:00
    """
    for d in datos:
        try:
            raw = d.get("periodo", d.get("hora", -1))
            if int(raw) == hour:
                return d.get(key, default)
        except (ValueError, TypeError):
            continue
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


def _sky_emoji(code: str) -> str:
    return SKY_CODES.get(code, "")


def _sky_short(code: str) -> str:
    return SKY_SHORT.get(code, "")


def _weekday_name(fecha_str: str) -> str:
    """Convert '2025-05-31' -> 'sáb'."""
    try:
        dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        return WEEKDAY_ES[dt.weekday()]
    except (ValueError, IndexError):
        return "?"


def _uv_label(uvi: float) -> str:
    """Return human-friendly UV level string (highest matching threshold)."""
    label = "Desconocido"
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
            return dt.strftime("%d/%m")
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
        return f"📡 data from {cache_time} (cached)"
    return ""


# ---------------------------------------------------------------------------
# Morning report (Markdown-friendly, pushed at 09:00)
# ---------------------------------------------------------------------------


def format_morning_report() -> str | None:
    """Morning weather brief — clean Markdown for Telegram."""
    current, station = _fetch_current()
    forecast_data = _fetch_forecast()
    days = _parse_forecast(forecast_data) if forecast_data else []
    uvi_data = _parse_uvi(_fetch_uvi())
    warnings = _parse_warnings(_fetch_warnings())

    if not current and not days:
        return None

    lines = []
    now = datetime.now()

    # ── Header ────────────────────────────────────────────────────────────
    lines.append("☀️ *Buenos días — Zaragoza*")
    if station:
        lines.append(f"📍 {station} · AEMET")
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
            f"🌡 *Ahora* ({time_str}): {cur['temp']:.1f}°C · "
            f"💧 {cur['humidity']:.0f}% · 💨 {cur['wind_speed']:.1f} km/h"
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
        orto = today.get("orto", "")
        ocaso = today.get("ocaso", "")

        t_min = _min_temp(temps)
        t_max = _max_temp(temps)

        # Day header with sunrise/sunset
        header = f"📅 *{weekday} {_format_date_short(fecha)}*"
        if orto and ocaso:
            header += f" · 🌅{orto}  🌇{ocaso}"
        lines.append(header)

        # Morning / afternoon / evening slots
        slots = {"Mañana": 9, "Tarde": 14, "Noche": 20}
        for label, h in slots.items():
            t = _get_slot(temps, h)
            s = _get_slot_str(sky, h, "value")
            p = int(_get_slot(precip, h, "value", 0))
            v = _get_slot(viento, h, "direccion", 0)
            if t is not None:
                try:
                    compass = _wind_degrees_to_compass(float(v))
                except (ValueError, TypeError):
                    compass = ""
                sky_short = _sky_short(s)
                lines.append(
                    f"  ▸ {h:02d}h  {float(t):.0f}°C · {sky_short} · 💧{p}% {compass}"
                )

        # Min/max + sparkline
        temp_values = []
        for d in temps:
            try:
                temp_values.append(float(d.get("value", 0)))
            except (ValueError, TypeError):
                continue
        spark = _temp_sparkline(temp_values, width=8)
        lines.append(
            f"  🌡 {t_min:.0f}–{t_max:.0f}°C  {spark}"
        )
        lines.append("")

        # ── UV Index ──────────────────────────────────────────────────────
        if uvi_data:
            uvi_val = uvi_data["uvi"]
            uvi_lvl = uvi_data["level"]
            lines.append(f"🔆 UV {uvi_val:.0f} ({uvi_lvl})")
            lines.append("")

        # ── Warnings ──────────────────────────────────────────────────────
        if warnings:
            lines.append("⚠️ *Avisos activos*")
            for w in warnings[:3]:  # max 3 in the brief
                icon = w["level_icon"]
                event = w["event"]
                level = w["level_label"]
                onset = _format_time(w["onset"]) if w.get("onset") else ""
                expires = _format_time(w["expires"]) if w.get("expires") else ""
                time_range = f" {onset}–{expires}" if onset and expires else ""
                lines.append(f"  {icon} {event} · {level}{time_range}")
            if len(warnings) > 3:
                lines.append(f"  … y {len(warnings) - 3} más")
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
    uvi_data = _parse_uvi(_fetch_uvi())
    warnings = _parse_warnings(_fetch_warnings())

    if not current and not days:
        return None

    lines = []
    now = datetime.now()

    # ── Header ────────────────────────────────────────────────────────────
    lines.append("🌤 *Zaragoza — AEMET*")
    if station:
        lines.append(f"📍 {station}")
    cache_note = _cache_age_line()
    if cache_note:
        lines.append(cache_note)
    lines.append("───")
    lines.append("")

    # ── Current Conditions ────────────────────────────────────────────────
    if current:
        cur = _parse_current(current)
        lines.append(
            f"🌡 *Ahora* ({now.strftime('%H:%M')}): {cur['temp']:.1f}°C · "
            f"💧 {cur['humidity']:.0f}% · 💨 {cur['wind_speed']:.1f} km/h"
        )
        lines.append("")

    # ── UV Index ──────────────────────────────────────────────────────────
    if uvi_data:
        uvi_val = uvi_data["uvi"]
        uvi_lvl = uvi_data["level"]
        lines.append(f"🔆 UV {uvi_val:.0f} ({uvi_lvl})")
        lines.append("")

    # ── Warnings ──────────────────────────────────────────────────────────
    if warnings:
        lines.append("⚠️ *Avisos activos*")
        for w in warnings:
            icon = w["level_icon"]
            event = w["event"]
            level = w["level_label"]
            lines.append(f"  {icon} {event} · Nivel {level}")
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

        t_min = _min_temp(temps)
        t_max = _max_temp(temps)

        # Day header
        day_label = f"📅 *{weekday} {_format_date_short(fecha)}*"
        if orto and ocaso:
            day_label += f" · 🌅{orto}  🌇{ocaso}"
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
                p_str = f"💧{int(float(p_val))}%"
            except (ValueError, TypeError):
                p_str = "💧0%"

            # Humidity
            h_val = _get_slot(humedad, h, "value", None)
            try:
                h_str = f"💦{int(float(h_val))}%" if h_val is not None else ""
            except (ValueError, TypeError):
                h_str = ""

            # Wind — only show if speed > 0
            v_dir = _get_slot(viento, h, "direccion", None)
            v_speed = _get_slot(viento, h, "velocidad", None)
            try:
                speed = float(v_speed) if v_speed is not None else 0
                if speed > 0:
                    compass = _wind_degrees_to_compass(v_dir or 0)
                    v_str = f"🌬{compass} {speed:.0f}"
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
        lines.append(f"  🌡 {t_min:.0f}–{t_max:.0f}°C  {spark}")

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
