"""
AEMET weather module — Zaragoza (Valdespartera → Aeropuerto fallback).

Provides current observations + hourly municipio forecast through
the official AEMET OpenData API.  No ML, no TFLite — just the
Spanish state meteorological agency's professional forecast.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import requests

# Ensure config is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    AEMET_API_KEY,
    AEMET_STATION_VALDESPARTERA,
    AEMET_STATION_AEROPUERTO,
    AEMET_MUNICIPIO_ID,
    WEATHER_LAT,
    WEATHER_LON,
)

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

# ---------------------------------------------------------------------------
# AEMET API helper — handles the two-step request/redirect pattern
# ---------------------------------------------------------------------------


def _aemet_get(endpoint: str, timeout: int = 15) -> list | dict | None:
    """Make an AEMET API call, follow the data redirect, return the result.

    The AEMET API always responds with a JSON containing a ``datos`` URL
    that must be fetched separately to obtain the actual payload.
    """
    if not AEMET_API_KEY:
        return None

    base = "https://opendata.aemet.es/opendata/api"
    url = f"{base}{endpoint}"

    try:
        # Step 1: get the data URL
        r1 = requests.get(url, params={"api_key": AEMET_API_KEY}, timeout=timeout)
        r1.raise_for_status()
        body = r1.json()

        datos_url = body.get("datos")
        if not datos_url:
            return None

        # Step 2: fetch the actual data
        r2 = requests.get(datos_url, timeout=timeout)
        r2.raise_for_status()
        return r2.json()
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Current observation (tries Valdespartera → falls back to Aeropuerto)
# ---------------------------------------------------------------------------


def _fetch_current() -> tuple[dict | None, str]:
    """Fetch latest observation.

    Returns ``(parsed_data, station_name)`` where *station_name* is
    ``"Valdespartera"`` or ``"Aeropuerto"`` so the UI can show which
    source was used.
    """
    for sid, sname in [
        (AEMET_STATION_VALDESPARTERA, "Valdespartera"),
        (AEMET_STATION_AEROPUERTO, "Aeropuerto"),
    ]:
        data = _aemet_get(f"/observacion/convencional/datos/estacion/{sid}")
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
# Municipio hourly forecast
# ---------------------------------------------------------------------------


def _fetch_forecast() -> list | None:
    """Fetch hourly forecast for Zaragoza municipio (7 days)."""
    return _aemet_get(f"/prediccion/especifica/municipio/horaria/{AEMET_MUNICIPIO_ID}")


def _parse_forecast(data: list) -> list:
    """Parse municipio forecast into a list of day dicts."""
    try:
        days = data[0]["prediccion"]["dia"]
    except (KeyError, IndexError, TypeError):
        return []

    parsed = []
    for day in days:
        fecha = day.get("fecha", "")
        temps = day.get("temperatura", {}).get("dato", [])
        sky = day.get("estadoCielo", {}).get("dato", [])
        precip_prob = day.get("probPrecipitacion", {}).get("dato", [])
        viento = day.get("viento", {}).get("dato", [])

        parsed.append({
            "fecha": fecha,
            "temperatura": temps,
            "estadoCielo": sky,
            "probPrecipitacion": precip_prob,
            "viento": viento,
        })
    return parsed


# ---------------------------------------------------------------------------
# Display formatters
# ---------------------------------------------------------------------------


def _sky_emoji(code: str) -> str:
    return SKY_CODES.get(code, "🌡")


def _hora(temp: float, sky: str, precip: int) -> str:
    """Single hour line: 14:00  26°C  ⛅  20%"""
    emoji = _sky_emoji(sky)
    return f"{temp:>3.0f}°C  {emoji}  {precip}%"


def format_morning_report() -> str | None:
    """Morning weather brief (push at 09:00)."""
    current, station = _fetch_current()
    forecast_data = _fetch_forecast()
    days = _parse_forecast(forecast_data) if forecast_data else []

    if not current and not days:
        return None

    lines = []
    lines.append("☀️ Buenos días — Zaragoza Forecast")
    lines.append("━" * 35)

    # Source header
    if station:
        lines.append(f"📍 {station} · AEMET")
    lines.append("")

    # Current conditions
    if current:
        cur = _parse_current(current)
        now = datetime.now().strftime("%H:%M")
        lines.append(f"🌡 Now ({now}): {cur['temp']:.1f}°C  ·  feels like —")
        lines.append(f"💧 {cur['humidity']:.0f}%  ·  💨 {cur['wind_speed']:.1f} km/h")
        lines.append("")

    # Today's forecast (first day in the list = today if available)
    if days:
        today = days[0]
        fecha = today.get("fecha", "")
        temps = today.get("temperatura", [])
        sky = today.get("estadoCielo", [])
        precip = today.get("probPrecipitacion", [])

        lines.append(f"📅 {fecha}")
        # Show morning / afternoon / evening highlights
        slots = {"Morning": 9, "Afternoon": 14, "Evening": 20}
        for label, h in slots.items():
            t = _get_slot(temps, h)
            s = _get_slot_str(sky, h, "value")
            p = int(_get_slot(precip, h, "value", 0))
            if t is not None:
                lines.append(f"  {label:<11}  {_hora(t, s, p)}")
        lines.append("")

        # Next 3 days summary
        if len(days) > 1:
            lines.append("🔮 Próximos días")
            for day in days[1:4]:
                t_min = _min_temp(day.get("temperatura", []))
                t_max = _max_temp(day.get("temperatura", []))
                s = _midday_sky(day.get("estadoCielo", []))
                fecha_short = day.get("fecha", "")[5:]  # MM-DD
                lines.append(f"  {fecha_short}  {t_min:.0f}–{t_max:.0f}°C  {s}")

    lines.append("━" * 35)
    return "\n".join(lines)


def format_ondemand() -> str | None:
    """On-demand weather display (fuller than the morning brief)."""
    current, station = _fetch_current()
    forecast_data = _fetch_forecast()
    days = _parse_forecast(forecast_data) if forecast_data else []

    if not current and not days:
        return None

    lines = []
    lines.append(f"🌤  Zaragoza Weather — AEMET")
    lines.append("━" * 45)

    if station:
        lines.append(f"📍 Estación: {station}")
    lines.append("")

    # Current
    if current:
        cur = _parse_current(current)
        lines.append("🔴 CURRENT CONDITIONS")
        lines.append(f"  🌡 Temperature:   {cur['temp']:.1f}°C")
        lines.append(f"  💧 Humidity:      {cur['humidity']:.0f}%")
        lines.append(f"  💨 Wind:          {cur['wind_speed']:.1f} km/h")
        lines.append(f"  📊 Pressure:      {cur['pressure']:.1f} hPa")
        if cur["precip"] > 0:
            lines.append(f"  🌧 Precipitation: {cur['precip']:.1f} mm")
        lines.append("")

    # Hourly forecast
    if days:
        today = days[0]
        lines.append(f"📅 TODAY · {today.get('fecha', '')}")
        lines.append(f"  {'Hora':<8} {'Temp':<8} {'Cielo':<20} {'Lluvia':<8}")
        lines.append(f"  {'─' * 44}")
        temps = today.get("temperatura", [])
        sky = today.get("estadoCielo", [])
        precip = today.get("probPrecipitacion", [])
        for entry in temps:
            h = entry.get("hora", 0)
            t = entry.get("value", "")
            s = _get_slot_str(sky, h, "value")
            p = int(_get_slot(precip, h, "value", 0))
            emoji = _sky_emoji(s)
            lines.append(f"  {h:02d}:00    {t:>4}°C   {emoji:<18} {p:>3}%")

        lines.append("")

        # Multi-day
        if len(days) > 1:
            lines.append("📅 NEXT DAYS")
            for day in days[1:7]:
                t_min = _min_temp(day.get("temperatura", []))
                t_max = _max_temp(day.get("temperatura", []))
                s = _midday_sky(day.get("estadoCielo", []))
                fecha_label = day.get("fecha", "")
                lines.append(f"  {fecha_label}  {t_min:.0f}–{t_max:.0f}°C  {s}")

    lines.append("━" * 45)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper: extract values from AEMET's "dato" list-of-dicts structure
# ---------------------------------------------------------------------------


def _get_slot(datos: list, hour: int, key: str = "value", default=None):
    for d in datos:
        if int(d.get("hora", -1)) == hour:
            return d.get(key, default)
    return default


def _get_slot_str(datos: list, hour: int, key: str = "value") -> str:
    val = _get_slot(datos, hour, key)
    return str(val) if val is not None else ""


def _max_temp(datos: list) -> float:
    vals = [float(d.get("value", 0)) for d in datos if d.get("value")]
    return max(vals) if vals else 0.0


def _min_temp(datos: list) -> float:
    vals = [float(d.get("value", 0)) for d in datos if d.get("value")]
    return min(vals) if vals else 0.0


def _midday_sky(datos: list) -> str:
    """Best sky emoji around 14:00."""
    for d in datos:
        if int(d.get("hora", -1)) == 14:
            return _sky_emoji(str(d.get("value", "")))
    return _sky_emoji("")


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
