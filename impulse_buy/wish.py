"""
Impulse Buy Cooler — evaluate your wishes, get re-asked later if you still want it.
Persists to JSON so nothing is lost on restart.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
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
    created: str                   # ISO timestamp
    asked_at: str | None = None    # ISO timestamp when we last re-checked
    status: str = "pending"        # "pending" | "kept" | "dropped"
    evaluation: dict[str, Any] | None = None   # {"uses": "2-5", "alternative": "no", "situations": "...", "money": "no"}
    result: str | None = None      # "buy" | "wait" — verdict from evaluation
    next_check: str | None = None  # ISO datetime for next re-check

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "created": self.created,
            "asked_at": self.asked_at,
            "status": self.status,
            "evaluation": self.evaluation,
            "result": self.result,
            "next_check": self.next_check,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WishItem:
        return cls(
            id=d["id"],
            text=d["text"],
            created=d["created"],
            asked_at=d.get("asked_at"),
            status=d.get("status", "pending"),
            evaluation=d.get("evaluation"),
            result=d.get("result"),
            next_check=d.get("next_check"),
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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_wish_by_id(wish_id: str) -> WishItem | None:
    """Return a single wish by ID, or None."""
    items = load_all()
    for w in items:
        if w.id == wish_id:
            return w
    return None


def add_wish(text: str) -> WishItem:
    items = load_all()
    now_str = datetime.now().isoformat(timespec="seconds")
    w = WishItem(
        id=uuid.uuid4().hex[:8],
        text=text,
        created=now_str,
    )
    items.append(w)
    save_all(items)
    return w


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

EVAL_QUESTIONS = [
    ("uses", "How many times a month do you think you'd use it?\n\n0-1 times / 2-5 times / 6+ times"),
    ("alternative", "Do you already have something that does the same or similar?\n\nYes / No"),
    ("situations", "Describe 3 concrete situations where you'd use it this month (one short phrase):"),
    ("money", "If you had the money right now, would you rather keep it?\n\nYes / No"),
]

EVAL_RESPONSES: dict[str, list[str]] = {
    "uses": ["0-1", "2-5", "6+"],
    "alternative": ["yes", "no"],
    "money": ["yes", "no"],
}


def _calc_score(evaluation: dict[str, Any]) -> int:
    """Score the evaluation answers (0-9 scale)."""
    score = 0
    uses = evaluation.get("uses", "")
    if uses.startswith("2") or uses.startswith("2-5"):
        score += 2
    elif uses.startswith("6"):
        score += 3

    alt = evaluation.get("alternative", "").strip().lower()
    if alt == "no":
        score += 2

    sit = evaluation.get("situations", "").strip()
    if len(sit) >= 15:  # at least 15 chars = real situations described
        score += 2

    money = evaluation.get("money", "").strip().lower()
    if money == "no":
        score += 2

    return score


def _calc_result(score: int) -> str:
    return "buy" if score >= 5 else "wait"


def _recheck_days(result: str) -> int:
    return 5 if result == "buy" else 7


def save_evaluation(wish_id: str, evaluation: dict[str, Any]) -> WishItem | None:
    """Store evaluation, calculate result, schedule re-check. Returns updated wish or None."""
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.evaluation = evaluation
            score = _calc_score(evaluation)
            w.result = _calc_result(score)
            now = datetime.now()
            w.next_check = (now + timedelta(days=_recheck_days(w.result))).isoformat(timespec="seconds")
            w.asked_at = None
            save_all(items)
            return w
    return None


# ---------------------------------------------------------------------------
# Re-check (replaces old get_pending)
# ---------------------------------------------------------------------------


def get_due_for_recheck() -> list[WishItem]:
    """Return pending wishes whose next_check time has arrived."""
    now = datetime.now()
    items = load_all()
    due = []
    for w in items:
        if w.status != "pending":
            continue
        if not w.next_check:
            continue
        try:
            check_dt = datetime.fromisoformat(w.next_check)
        except ValueError:
            continue
        if check_dt <= now and w.asked_at is None:
            due.append(w)
    return due


def mark_kept(wish_id: str) -> None:
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.status = "kept"
            w.next_check = None
            w.asked_at = datetime.now().isoformat(timespec="seconds")
            break
    save_all(items)


def mark_dropped(wish_id: str) -> None:
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.status = "dropped"
            w.next_check = None
            w.asked_at = datetime.now().isoformat(timespec="seconds")
            break
    save_all(items)


def mark_asked(wish_id: str) -> None:
    """Record that we asked about this wish (without changing status)."""
    items = load_all()
    for w in items:
        if w.id == wish_id:
            w.asked_at = datetime.now().isoformat(timespec="seconds")
            break
    save_all(items)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_evaluation(w: WishItem) -> str:
    """Full evaluation verdict for a wish."""
    if not w.evaluation or not w.result:
        return f"> {w.text}\n  not yet evaluated"

    e = w.evaluation
    score = _calc_score(e)
    days = _recheck_days(w.result)

    lines = [f"> Evaluation: {w.text}"]
    lines.append(f"  uses        {e.get('uses', '?')}")
    lines.append(f"  alternative {e.get('alternative', '?')}")
    sit_ok = "yes" if len(e.get("situations", "").strip()) >= 15 else "no"
    lines.append(f"  situations  {sit_ok}")
    lines.append(f"  keep money  {e.get('money', '?')}")
    lines.append("")
    verdict = "buy" if w.result == "buy" else "wait"
    lines.append(f"  verdict     {verdict} ({score}/9)")
    lines.append(f"  next check  in {days} days")

    return "\n".join(lines)


def format_wishlist() -> str | None:
    items = load_all()
    if not items:
        return None
    lines = ["> Wish History"]
    for w in reversed(items):
        tag = {"pending": "", "kept": "kept", "dropped": "dropped"}.get(w.status, "")
        if w.result and w.status == "pending":
            tag = f"buy ({_calc_score(w.evaluation)}/9)" if w.evaluation else "buy"
        lines.append(f"  · {w.text:<20} {tag}".rstrip())
    return "\n".join(lines)


def format_recheck_prompt(w: WishItem) -> str:
    """Message to ask the user at re-check time — shows date + past answers."""
    try:
        dt = datetime.fromisoformat(w.created)
        date_str = dt.strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        date_str = w.created

    lines = [
        f"> Re-evaluation: {w.text}",
        f"  from {date_str}",
        "",
    ]

    if w.evaluation:
        lines.append(f"  uses        {w.evaluation.get('uses', '?')}")
        lines.append(f"  alternative {w.evaluation.get('alternative', '?')}")
        sit_ok = "yes" if len(w.evaluation.get("situations", "").strip()) >= 15 else "no"
        lines.append(f"  situations  {sit_ok}")
        lines.append(f"  keep money  {w.evaluation.get('money', '?')}")
        if w.result:
            score = _calc_score(w.evaluation)
            lines.append(f"  verdict     {w.result} ({score}/9)")

    lines.append("")
    lines.append("  Still want it?")

    return "\n".join(lines)
