"""
Study dashboard — streak, weekly summary, unit coverage, all-time progress.

Reads the existing study_log.csv and computes analytics without needing
any new data files. All formatting is mobile-friendly (no box-drawing).
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("aihub.study")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STUDY_LOG: Path = DATA_DIR / "study_log.csv"


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------

def _load_rows() -> list[dict[str, str | float | None]]:
    """Return all study log rows as dicts, or empty list if no data."""
    if not STUDY_LOG.exists():
        return []
    rows: list[dict[str, str | float | None]] = []
    try:
        with STUDY_LOG.open(newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return []
            for row in reader:
                if len(row) < 7:
                    continue
                try:
                    rows.append({
                        "date": row[0],
                        "unit": int(row[1]),
                        "hours": float(row[2]),
                        "energy": float(row[3]),
                        "sleep": float(row[4]),
                        "grade": float(row[5]) if row[5] and row[5] not in ("", "None") else None,
                        "rating": float(row[6]),
                    })
                except (ValueError, IndexError):
                    continue
    except (FileNotFoundError, OSError) as exc:
        log.error("Failed to read study log: %s", exc)
        return []
    return rows


# ---------------------------------------------------------------------------
# Streak
# ---------------------------------------------------------------------------

def calc_streak() -> tuple[int, int, list[str]]:
    """
    Return (current_streak, longest_streak, dates_in_streak).

    Streak = consecutive calendar days with at least one study entry,
    ending at the most recent entry date.
    """
    rows = _load_rows()
    if not rows:
        return (0, 0, [])

    # Unique study dates, sorted
    study_dates = sorted({r["date"] for r in rows if r["date"]})
    if not study_dates:
        return (0, 0, [])

    # Convert to date objects
    study_dates_dt = []
    for d in study_dates:
        try:
            study_dates_dt.append(date.fromisoformat(d))
        except ValueError:
            continue
    if not study_dates_dt:
        return (0, 0, [])

    study_set = set(study_dates_dt)
    study_dates_dt.sort()

    # Current streak: from the most recent study date, count backwards
    last = study_dates_dt[-1]
    current_streak = 1
    check = last - timedelta(days=1)
    while check in study_set:
        current_streak += 1
        check -= timedelta(days=1)

    # Dates in current streak
    streak_dates = []
    for d in study_dates_dt:
        if d >= last - timedelta(days=current_streak - 1):
            streak_dates.append(d.isoformat())

    # Longest streak: find the max consecutive run
    longest = 1
    run = 1
    for i in range(1, len(study_dates_dt)):
        if (study_dates_dt[i] - study_dates_dt[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return (current_streak, longest, streak_dates)


# ---------------------------------------------------------------------------
# Weekly summary
# ---------------------------------------------------------------------------

def week_summary(year_week: str | None = None) -> str | None:
    """
    Return a markdown summary for the given ISO week (YYYY-Www or YYYY-Www).

    If no week is given, use the current ISO week.
    Returns None if no data.
    """
    from datetime import datetime

    rows = _load_rows()
    if not rows:
        return None

    # Determine target week
    today = date.today()
    if year_week:
        try:
            year_s, week_s = year_week.split("-W")
            target_year = int(year_s)
            target_week = int(week_s)
        except (ValueError, IndexError):
            return "⚠️ Use format YYYY-Www, e.g. `/week 2026-W22`."
    else:
        target_year, target_week, _ = today.isocalendar()

    # Filter rows for that ISO week
    filtered = []
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
            y, w, _ = d.isocalendar()
            if y == target_year and w == target_week:
                filtered.append(r)
        except (ValueError, TypeError):
            continue

    if not filtered:
        if year_week:
            return f"📊 No data for week {year_week}."
        return "📊 No study data this week yet."

    n = len(filtered)
    total_hours = sum(r["hours"] for r in filtered)
    units = sorted({r["unit"] for r in filtered})
    avg_rating = sum(r["rating"] for r in filtered) / n
    avg_energy = sum(r["energy"] for r in filtered) / n

    lines = [
        f"📊 *Week {target_week}* ({target_year})",
        "───",
        "",
        f"📚 Sessions:  {n}",
        f"⏱ Hours:      {total_hours:.1f}h",
        f"📖 Units:      {len(units)} ({', '.join(str(u) for u in units)})",
        f"⭐ Avg rating: {avg_rating:.1f}/10",
        f"⚡ Avg energy: {avg_energy:.1f}/10",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unit coverage
# ---------------------------------------------------------------------------

def unit_coverage() -> str | None:
    """
    Return covered units, gaps, and next suggested unit.
    """
    rows = _load_rows()
    if not rows:
        return None

    covered = sorted({r["unit"] for r in rows})
    if not covered:
        return None

    max_unit = max(covered)
    all_units = set(range(1, max_unit + 1))
    covered_set = set(covered)
    gaps = sorted(all_units - covered_set)

    lines = ["📖 *Unit Coverage*", "───", ""]

    # Show covered units in groups of 10
    covered_strs = [str(u) for u in covered]
    # Compact: wrap at ~20 chars
    chunks = []
    chunk = []
    for s in covered_strs:
        chunk.append(s)
        if len(", ".join(chunk)) > 24:
            chunks.append(chunk[:-1])
            chunk = [s]
    if chunk:
        chunks.append(chunk)

    lines.append("*Covered:*")
    for ch in chunks:
        lines.append("  " + ", ".join(ch))

    if gaps:
        lines.append("")
        lines.append("*Gaps:*")
        gap_strs = [str(g) for g in gaps]
        chunks = []
        chunk = []
        for s in gap_strs:
            chunk.append(s)
            if len(", ".join(chunk)) > 24:
                chunks.append(chunk[:-1])
                chunk = [s]
        if chunk:
            chunks.append(chunk)
        for ch in chunks:
            lines.append("  " + ", ".join(ch))

        # Next suggested unit
        next_unit = gaps[0]
        lines.append("")
        lines.append(f"👉 Next: *Unit {next_unit}*")
    else:
        lines.append("")
        lines.append("✅ All units covered!")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# All-time progress
# ---------------------------------------------------------------------------

def all_time_progress() -> str | None:
    """
    Return all-time study stats with burnout risk check.
    """
    rows = _load_rows()
    if not rows:
        return None

    n = len(rows)
    total_hours = sum(r["hours"] for r in rows)
    units = sorted({r["unit"] for r in rows})
    avg_rating = sum(r["rating"] for r in rows) / n
    avg_hours = total_hours / n
    avg_energy = sum(r["energy"] for r in rows) / n
    avg_sleep = sum(r["sleep"] for r in rows) / n

    # Streaks
    current_streak, longest_streak, _ = calc_streak()

    # Unique study days
    study_dates = sorted({r["date"] for r in rows if r["date"]})
    unique_days = len(study_dates)

    # Best session
    best = max(rows, key=lambda r: r["rating"])
    best_rating = int(best["rating"])
    best_unit = best["unit"]
    best_date = best["date"]

    # Burnout check: last 5 sessions
    recent = rows[-5:]
    burnout_flags = 0
    burnout_reasons = []
    for r in recent:
        flags = []
        if r["energy"] <= 4:
            flags.append("⚡ low energy")
        if r["sleep"] <= 5:
            flags.append("😴 little sleep")
        if r["rating"] <= 4:
            flags.append("⭐ low rating")
        if flags:
            burnout_flags += 1
            burnout_reasons.append(f"  {r['date']}: {', '.join(flags)}")

    lines = [
        "📈 *Study Progress*",
        "───",
        "",
        f"📚 Total sessions:    {n}",
        f"⏱ Total hours:       {total_hours:.1f}h",
        f"📖 Units covered:     {len(units)}",
        f"📅 Unique study days: {unique_days}",
        f"📊 Avg session:       {avg_hours:.1f}h · rating {avg_rating:.1f}/10",
        f"⚡ Avg energy:        {avg_energy:.1f}/10",
        f"😴 Avg sleep:         {avg_sleep:.1f}h",
        "",
        f"🔥 Current streak:    {current_streak} days",
        f"🏆 Longest streak:    {longest_streak} days",
        "",
        f"🌟 *Best Session* — Unit {best_unit} on {best_date}",
        f"   Rating: {best_rating}/10",
    ]

    # Burnout warning
    if burnout_flags >= 3:
        lines.append("")
        lines.append("⚠️ *Burnout Risk*")
        lines.extend(burnout_reasons)
        lines.append("")
        lines.append("💡 Consider a rest day or shorter sessions.")

    return "\n".join(lines)
