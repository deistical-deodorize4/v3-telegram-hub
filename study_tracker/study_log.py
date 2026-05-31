"""
Study session logger with burnout detection.

Logs sessions to a CSV file, provides recent-entry view, and
per-session statistics with an optional month filter.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date
from typing import Optional

# Ensure config is importable when run as `python study_tracker/study_log.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import STUDY_LOG  # noqa: E402


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _ensure_header() -> None:
    if not STUDY_LOG.exists():
        STUDY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with STUDY_LOG.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "date", "unit", "hours_studied",
                "energy_level", "sleep_hours", "grade", "rating",
            ])
        print("Log file created!\n")


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _get_float(prompt: str, min_val: float, max_val: float) -> float:
    while True:
        try:
            value = float(input(prompt))
            if min_val <= value <= max_val:
                return value
            print(f"Please enter a value between {min_val} and {max_val}")
        except ValueError:
            print("Please enter a valid number")


def _get_int(prompt: str, min_val: int, max_val: int) -> int:
    while True:
        try:
            value = int(input(prompt))
            if min_val <= value <= max_val:
                return value
            print(f"Please enter a number between {min_val} and {max_val}")
        except ValueError:
            print("Please enter a valid number")


# ---------------------------------------------------------------------------
# Core logging
# ---------------------------------------------------------------------------

def log_session() -> None:
    print("\n--- Log Study Session ---")

    today = date.today().isoformat()
    print(f"Date: {today}")

    unit = _get_int("Unit studied (1-69): ", 1, 69)
    hours = _get_float("Hours studied (e.g. 1.5): ", 0.1, 12.0)
    energy = _get_float("Energy level before studying (1-10): ", 1, 10)
    sleep = _get_float("Hours of sleep last night: ", 0, 12)

    grade_input = input("Grade received (leave empty if none yet): ").strip()
    grade = float(grade_input) if grade_input else None

    rating = _get_float("Session quality rating (1-10): ", 1, 10)

    with STUDY_LOG.open("a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([today, unit, hours, energy, sleep, grade, rating])

    print(f"\n✓ Logged: Unit {unit} | {hours}h | Rating {rating}/10\n")


# ---------------------------------------------------------------------------
# View recent
# ---------------------------------------------------------------------------

def view_recent() -> None:
    if not STUDY_LOG.exists():
        print("No log file found.\n")
        return

    with STUDY_LOG.open() as f:
        rows = list(csv.reader(f))

    if len(rows) <= 1:
        print("No sessions logged yet.\n")
        return

    print("\n--- Recent Sessions (last 5) ---")
    print(f"{'Date':<12} {'Unit':<6} {'Hours':<7} {'Energy':<8} {'Sleep':<7} {'Grade':<7} {'Rating'}")
    print("-" * 65)

    for row in rows[-5:]:
        grade_display = row[5] if row[5] not in ("", "None") else "-"
        print(f"{row[0]:<12} {row[1]:<6} {row[2]:<7} {row[3]:<8} {row[4]:<7} {grade_display:<7} {row[6]}")

    print()


# ---------------------------------------------------------------------------
# Month filter helpers
# ---------------------------------------------------------------------------

def _valid_month(month: str) -> bool:
    if not month:
        return True
    try:
        year, m = month.split("-")
        return len(year) == 4 and 1 <= int(m) <= 12
    except (ValueError, IndexError):
        return False


def _filter_by_month(data: list[list[str]], month_str: str) -> list[list[str]]:
    if not month_str:
        return data
    return [r for r in data if r[0].startswith(month_str)]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def view_stats() -> None:
    if not STUDY_LOG.exists():
        print("No log file found.\n")
        return

    with STUDY_LOG.open() as f:
        rows = list(csv.reader(f))

    if len(rows) <= 1:
        print("No sessions logged yet.\n")
        return

    data = rows[1:]

    # month filter
    while True:
        month = input("Enter month (YYYY-MM) or press Enter: ").strip()
        if _valid_month(month):
            break
        print("Invalid format. Use YYYY-MM")

    data = _filter_by_month(data, month)

    if not data:
        print()
        return

    # totals
    total_sessions = len(data)
    total_hours = sum(float(r[2]) for r in data)
    avg_energy = sum(float(r[3]) for r in data) / total_sessions
    avg_sleep = sum(float(r[4]) for r in data) / total_sessions
    avg_rating = sum(float(r[6]) for r in data) / total_sessions
    units_studied = sorted({r[1] for r in data})

    print("\n--- Your Stats ---")
    print(f"Total sessions:    {total_sessions}")
    print(f"Total hours:       {total_hours:.1f}h")
    print(f"Average energy:    {avg_energy:.1f}/10")
    print(f"Average sleep:     {avg_sleep:.1f}h")
    print(f"Average rating:    {avg_rating:.1f}/10")
    print(f"Units covered:     {len(units_studied)}")

    # best session
    valid = [r for r in data if r[6] not in ("", "None")]
    if valid:
        best = max(valid, key=lambda r: float(r[6]))
        print("\n--- Best Session ---")
        print(f"Date:    {best[0]}")
        print(f"Unit:    {best[1]}")
        print(f"Rating:  {best[6]}/10")
        print(f"Energy:  {best[3]}/10")
        print(f"Sleep:   {best[4]}h")
        grade_val = best[5]
        if grade_val not in ("", "None"):
            print(f"Grade:   {grade_val}")
        else:
            print("Grade:   -")

    # burnout detection
    low_count = 0
    for r in data:
        energy = float(r[3])
        sleep = float(r[4])
        rating = float(r[6])
        try:
            grade = float(r[5]) if r[5] not in ("", "None") else None
        except ValueError:
            grade = None

        low_energy = energy <= 4
        low_sleep = sleep <= 5
        low_rating = rating <= 4
        low_grade = grade is not None and grade < 5

        if low_energy and low_sleep and low_rating and (grade is None or low_grade):
            low_count += 1

    if low_count >= 5:
        print("\n⚠ You have several low-quality sessions — consider rest or shorter sessions.")

    print()


# ---------------------------------------------------------------------------
# CLI main loop
# ---------------------------------------------------------------------------

def main() -> None:
    _ensure_header()
    print("Study Tracker")
    print("Commands: 'log' | 'view' | 'stats' | 'q'\n")

    while True:
        command = input("Command: ").strip().lower()
        if command == "q":
            print("Cheerio!")
            break
        elif command == "log":
            log_session()
        elif command == "view":
            view_recent()
        elif command == "stats":
            view_stats()
        else:
            print("Unknown command. Use 'log', 'view', 'stats' or 'q'\n")


if __name__ == "__main__":
    main()
