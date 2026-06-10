from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("aihub.lens")

_LENS_DAYS = 30

_DATA: dict | None = None


def _load(path: Path) -> dict:
    global _DATA
    if _DATA is not None:
        return _DATA
    if path.exists():
        _DATA = json.loads(path.read_text())
    else:
        _DATA = {"pair_start": None, "session_start": None}
    return _DATA


def _save(path: Path) -> None:
    path.write_text(json.dumps(_DATA, indent=2) + "\n")


def status(path: Path) -> str:
    d = _load(path)
    now = datetime.now()
    lines = ["> Lenses"]

    pair = d.get("pair_start")
    if pair:
        pd = date.fromisoformat(pair)
        days_elapsed = (now.date() - pd).days
        remaining = _LENS_DAYS - days_elapsed
        if remaining > 0:
            lines.append(f"  pair     {days_elapsed}d old ({remaining}d left)")
        else:
            lines.append(f"  pair     {days_elapsed - _LENS_DAYS}d overdue!")
    else:
        lines.append("  pair     none")

    ss = d.get("session_start")
    if ss:
        sd = datetime.fromisoformat(ss)
        delta = now - sd
        lines.append(f"  wearing  since {sd.strftime('%d %b')} ({delta.days}d)")
    else:
        lines.append("  wearing  no")

    lines.append("")
    lines.append("  Send in / out / new")
    return "\n".join(lines)


def start_session(path: Path) -> tuple[bool, str]:
    d = _load(path)
    if d.get("session_start"):
        return False, "Already wearing lenses. Send `out` first."
    d["session_start"] = datetime.now().isoformat(timespec="minutes")
    _save(path)
    return True, f"On since {date.today().strftime('%d %b')}"


def stop_session(path: Path) -> tuple[bool, str, timedelta | None]:
    d = _load(path)
    ss = d.get("session_start")
    if not ss:
        return False, "Not wearing lenses.", None
    sd = datetime.fromisoformat(ss)
    now = datetime.now()
    delta = now - sd
    d["session_start"] = None
    _save(path)
    return True, f"Stopped. Wore for {delta.days}d.", delta


def new_pair(path: Path, force: bool = False) -> tuple[bool, str]:
    d = _load(path)
    if d.get("session_start"):
        return False, "Take lenses out first with `out`."
    d["session_start"] = None
    d["pair_start"] = date.today().isoformat()
    _save(path)
    return True, "Fresh pair started! 30-day countdown begins."


def check_expiry(path: Path) -> str | None:
    d = _load(path)
    pair = d.get("pair_start")
    if not pair:
        return None
    pd = date.fromisoformat(pair)
    days = (date.today() - pd).days
    if days >= _LENS_DAYS:
        overdue = days - _LENS_DAYS
        return (f"! *Lens pair is {_LENS_DAYS} days old!* "
                f"({overdue}d overdue)\n"
                f"Change to a fresh pair and send `new`.")
    return None


def reload() -> None:
    global _DATA
    _DATA = None
