"""
Impulse Buy Cooler — save a wish, get asked 10 days later if you still want it.
Persists to JSON so nothing is lost on restart.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("aihub.impulse")

_DATA_DIR = Path(__file__).resolve().parent
WISHLIST_FILE: Path = _DATA_DIR / "wishlist.json"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class WishItem:
    id: str
    text: str
    created: str          # ISO timestamp
    asked_at: str | None  # ISO timestamp when we last asked
    status: str           # "pending" | "kept" | "dropped"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "created": self.created,
            "asked_at": self.asked_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WishItem:
        return cls(
            id=d["id"],
            text=d["text"],
            created=d["created"],
            asked_at=d.get("asked_at"),
            status=d.get("status", "pending"),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_all() -> list[WishItem]:
    if not WISHLIST_FILE.exists():
        return []
    try:
        raw = json.loads(WISHLIST_FILE.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("wishes", [])
        return [WishItem.from_dict(d) for d in items]
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load wishlist: %s", exc)
        return []


def save_all(items: list[WishItem]) -> None:
    data = [w.to_dict() for w in items]
    WISHLIST_FILE.write_text(
        json.dumps({"wishes": data}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def add_wish(text: str) -> WishItem:
    items = load_all()
    now_str = datetime.now().isoformat(timespec="seconds")
    w = WishItem(
        id=uuid.uuid4().hex[:8],
        text=text,
        created=now_str,
        asked_at=None,
        status="pending",
    )
    items.append(w)
    save_all(items)
    return w


def get_pending(days: int = 10) -> list[WishItem]:
    """Return pending wishes older than `days` days that haven't been asked yet."""
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    items = load_all()
    due = []
    for w in items:
        if w.status != "pending":
            continue
        try:
            created_dt = datetime.fromisoformat(w.created)
        except ValueError:
            continue
        if created_dt <= cutoff and w.asked_at is None:
            due.append(w)
    return due


def mark_kept(wish_id: str) -> None:
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.status = "kept"
            break
    save_all(items)


def mark_dropped(wish_id: str) -> None:
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.status = "dropped"
            break
    save_all(items)


def mark_asked(wish_id: str) -> None:
    """Record that we asked about this wish."""
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.asked_at = datetime.now().isoformat(timespec="seconds")
            break
    save_all(items)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_wishlist() -> str | None:
    items = load_all()
    if not items:
        return None
    lines = ["💸 *Wish History*", "───", ""]
    for w in reversed(items):  # newest first
        icon = {"pending": "⏳", "kept": "✅", "dropped": "❌"}.get(w.status, "❓")
        lines.append(f"{icon} {w.text}")
    return "\n".join(lines)


def format_prompt(w: WishItem) -> str:
    """Message to ask the user if they still want it."""
    return (
        f"💸 *Impulse Check*\n"
        f"You wanted: {w.text}\n\n"
        f"Still want it?"
    )
