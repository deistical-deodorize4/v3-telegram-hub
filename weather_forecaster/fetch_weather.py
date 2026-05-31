"""
Fetch historical weather data from Open-Meteo Archive API and save to CSV.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import WEATHER_HISTORICAL, WEATHER_LAT, WEATHER_LON  # noqa: E402

VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "cloud_cover",
    "surface_pressure",
]


def fetch_historical(days_back: int = 365) -> dict:
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)

    print(f"Fetching historical data from {start_date} to {end_date}…")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params: dict = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": ",".join(VARIABLES),
        "timezone": "Europe/Madrid",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def save_to_csv(data: dict) -> None:
    hourly = data["hourly"]
    timestamps = hourly["time"]

    rows = []
    for i, timestamp in enumerate(timestamps):
        row = [timestamp] + [hourly[var][i] for var in VARIABLES]
        rows.append(row)

    WEATHER_HISTORICAL.parent.mkdir(parents=True, exist_ok=True)
    with WEATHER_HISTORICAL.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp"] + VARIABLES)
        writer.writerows(rows)

    print(f"✓ Saved {len(rows)} hourly records to {WEATHER_HISTORICAL}")


def show_sample() -> None:
    if not WEATHER_HISTORICAL.exists():
        print("No data file found.")
        return
    with WEATHER_HISTORICAL.open() as f:
        rows = list(csv.reader(f))

    print(f"\n--- Sample (first 3 rows) ---")
    print(", ".join(rows[0]))
    print("-" * 60)
    for row in rows[1:4]:
        print(", ".join(str(x) for x in row))
    print()
    print(f"Total records: {len(rows) - 1} hourly entries")


if __name__ == "__main__":
    data = fetch_historical(days_back=365)
    save_to_csv(data)
    show_sample()
