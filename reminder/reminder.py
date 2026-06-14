"""
Reminder system — stores, parses, and checks timed reminders.
Persists to a JSON file so reminders survive bot restarts.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("aihub.reminder")

_DATA_DIR = Path(__file__).resolve().parent
REMINDERS_FILE: Path = _DATA_DIR / "reminders.json"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Reminder:
    id: str
    message: str
    dt: datetime       # target datetime (naive, in Europe/Madrid)
    done: bool = False
    created: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "message": self.message,
            "datetime": self.dt.isoformat(),
            "done": self.done,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict, tz) -> Reminder | None:
        try:
            dt = datetime.fromisoformat(d["datetime"])
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz).replace(tzinfo=None)
            return cls(
                id=d["id"],
                message=d["message"],
                dt=dt,
                done=d.get("done", False),
                created=d.get("created", ""),
            )
        except (KeyError, ValueError) as exc:
            log.warning("Skipping bad reminder: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_reminders(tz) -> list[Reminder]:
    if not REMINDERS_FILE.exists():
        return []
    try:
        raw = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("reminders", [])
        result = []
        for d in items:
            r = Reminder.from_dict(d, tz)
            if r is not None:
                result.append(r)
        return result
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load reminders: %s", exc)
        return []


def save_reminders(reminders: list[Reminder]) -> None:
    data = [r.to_dict() for r in reminders]
    REMINDERS_FILE.write_text(
        json.dumps({"reminders": data}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def add_reminder(message: str, dt: datetime, tz) -> Reminder:
    reminders = load_reminders(tz)
    now_str = datetime.now().isoformat(timespec="seconds")
    r = Reminder(
        id=uuid.uuid4().hex[:8],
        message=message,
        dt=dt,
        done=False,
        created=now_str,
    )
    reminders.append(r)
    save_reminders(reminders)
    return r


def get_due_reminders(tz) -> list[Reminder]:
    """Return reminders whose time has passed and are not yet done."""
    now = datetime.now()
    reminders = load_reminders(tz)
    due = []
    for r in reminders:
        if not r.done and r.dt <= now:
            due.append(r)
    return due


def mark_done(reminder_id: str, tz) -> None:
    reminders = load_reminders(tz)
    for r in reminders:
        if r.id == reminder_id:
            r.done = True
            break
    save_reminders(reminders)


def delete_old(days: int = 7, tz=None) -> int:
    """Remove done reminders older than `days`. Returns count removed."""
    if tz is None:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Madrid")
    cutoff = datetime.now() - timedelta(days=days)
    reminders = load_reminders(tz)
    before = len(reminders)
    reminders = [r for r in reminders if not (r.done and r.dt < cutoff)]
    save_reminders(reminders)
    return before - len(reminders)


# ---------------------------------------------------------------------------
# Time parser (natural language → datetime)
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_MONTH_NAMES = {
    "january": 1, "jan": 1, "enero": 1,
    "february": 2, "feb": 2, "febrero": 2,
    "march": 3, "mar": 3, "marzo": 3,
    "april": 4, "apr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "june": 6, "jun": 6, "junio": 6,
    "july": 7, "jul": 7, "julio": 7,
    "august": 8, "aug": 8, "agosto": 8,
    "september": 9, "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9,
    "october": 10, "oct": 10, "octubre": 10,
    "november": 11, "nov": 11, "noviembre": 11,
    "december": 12, "dec": 12, "diciembre": 12,
}

_RELATIVE_PATTERNS = [
    (re.compile(r"in (\d+)\s*minutes?\b", re.I), "minutes"),
    (re.compile(r"in (\d+)\s*mins?\b", re.I), "minutes"),
    (re.compile(r"in (\d+)\s*min\b", re.I), "minutes"),
    (re.compile(r"in (\d+)\s*hours?\b", re.I), "hours"),
    (re.compile(r"in (\d+)\s*h\b", re.I), "hours"),
    (re.compile(r"in (\d+)\s*days?\b", re.I), "days"),
    (re.compile(r"in (\d+)\s*d\b", re.I), "days"),
]

_TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)?", re.I)
_AMPM_PATTERN = re.compile(r"(\d{1,2})\s*(am|pm)\b", re.I)


def _parse_time(text: str) -> tuple[int, int] | None:
    """Extract hour, minute from text. Returns None if not found."""
    # Strip leading "at"
    text = re.sub(r"^\s*at\s+", "", text, flags=re.I)

    # Try "HH:MM am/pm" or "HH:MM"
    m = _TIME_PATTERN.search(text)
    if m:
        h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and h != 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi

    # Try "H am/pm" (no colon)
    m = _AMPM_PATTERN.search(text)
    if m:
        h = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return h, 0

    return None


def parse_datetime(text: str, tz) -> datetime | None:
    """Parse a natural-language time into a datetime.

    Supported formats:
        - "in X minutes/hours/days"
        - "in X min/h/d"
        - "tomorrow at HH:MM" / "tomorrow HH:MM"
        - "today at HH:MM"
        - "HH:MM" (today if future, else tomorrow)
        - "DD/MM at HH:MM" / "DD/MM HH:MM"
        - "next monday at HH:MM" / "friday at HH:MM"
        - "now"
    """
    from zoneinfo import ZoneInfo

    now = datetime.now()
    text = text.strip().lower()

    # --- "now" ---
    if text in ("now", "right now", "asap"):
        return now

    # --- Relative: "in X minutes/hours/days" ---
    for pattern, unit in _RELATIVE_PATTERNS:
        m = pattern.search(text)
        if m:
            amount = int(m.group(1))
            kwargs = {unit: amount}
            return now + timedelta(**kwargs)

    # --- Extract a DD/MM date ---
    date_specified = False
    date_day, date_month = None, None
    dm = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", text)
    if dm:
        date_day = int(dm.group(1))
        date_month = int(dm.group(2))
        text = text.replace(dm.group(0), "").strip()
        date_specified = True

    # --- Extract a month-name date ---
    if not date_specified:
        _mn = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
        m = re.search(
            r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?\b(" + _mn + r")\b", text
        )
        if m:
            d, mn = int(m.group(1)), _MONTH_NAMES[m.group(2)]
            if 1 <= d <= 31:
                date_specified, date_day, date_month = True, d, mn
                text = text.replace(m.group(0), "").strip()
        if not date_specified:
            m = re.search(r"\b(" + _mn + r")\s+(\d{1,2})(?:st|nd|rd|th)?", text)
            if m:
                mn, d = _MONTH_NAMES[m.group(1)], int(m.group(2))
                if 1 <= d <= 31:
                    date_specified, date_day, date_month = True, d, mn
                    text = text.replace(m.group(0), "").strip()

    # --- "tomorrow" flag ---
    tomorrow = False
    if "tomorrow" in text:
        tomorrow = True
        text = text.replace("tomorrow", "").strip()

    # --- "today" flag ---
    if "today" in text:
        text = text.replace("today", "").strip()

    # --- "tonight" flag ---
    if "tonight" in text:
        text = text.replace("tonight", "").strip()

    # --- Day-of-week ---
    day_offset = None
    for name, idx in _DAY_NAMES.items():
        if name in text:
            text = text.replace(name, "").strip()
            text = text.replace("next", "").strip()
            today_idx = now.weekday()
            day_offset = (idx - today_idx) % 7
            if day_offset == 0:
                day_offset = 7  # "next" means next week
            break

    # --- Extract time ---
    hm = _parse_time(text)
    if hm is None:
        # Try "HHMM" without colon (e.g. "1430")
        m2 = re.search(r"\b(\d{1,2})(\d{2})\b", text)
        if m2:
            h, mi = int(m2.group(1)), int(m2.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                hm = (h, mi)
    if hm is None:
        return None

    h, mi = hm

    # --- Build target ---
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)

    if date_specified and date_day is not None and date_month is not None:
        # Specific date
        year = now.year
        target = target.replace(day=date_day, month=date_month)
        if target < now:
            # Date already passed this year → next year
            target = target.replace(year=year + 1)
    elif tomorrow:
        target += timedelta(days=1)
    elif day_offset is not None:
        target += timedelta(days=day_offset)
    elif target <= now:
        # Time passed today → schedule for tomorrow
        target += timedelta(days=1)

    return target


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_reminder(r: Reminder) -> str:
    """Pretty format for confirmation."""
    day = r.dt.strftime("%A").capitalize()
    return (
        f"> Reminder\n"
        f"  {r.message}\n"
        f"  {day} {r.dt.strftime('%d-%m')}  {r.dt.strftime('%H:%M')}"
    )


# ---------------------------------------------------------------------------
# Cleanup job
# ---------------------------------------------------------------------------

def cleanup_old(tz=None) -> int:
    """Remove done reminders older than 7 days."""
    return delete_old(days=7, tz=tz)
