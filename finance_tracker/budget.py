"""
Budget monitor — set monthly limits per category, get warned when close.

Persists budgets to a JSON file, reads spending from the existing
finance_log.csv, and provides formatted status/recap messages.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("aihub.budget")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BUDGET_FILE: Path = _DATA_DIR / "budgets.json"
FINANCE_LOG: Path = _DATA_DIR / "finance_log.csv"

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_raw() -> dict[str, float]:
    """Return {category: monthly_limit} from disk."""
    if not BUDGET_FILE.exists():
        return {}
    try:
        return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load budgets: %s", exc)
        return {}


def _save_raw(budgets: dict[str, float]) -> None:
    BUDGET_FILE.write_text(
        json.dumps(budgets, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def set_budget(category: str, amount: float) -> None:
    """Set monthly budget for *category* to *amount* (€)."""
    budgets = _load_raw()
    budgets[category.strip().lower()] = round(amount, 2)
    _save_raw(budgets)


def remove_budget(category: str) -> bool:
    """Remove budget for *category*. Returns True if it existed."""
    budgets = _load_raw()
    key = category.strip().lower()
    if key in budgets:
        del budgets[key]
        _save_raw(budgets)
        return True
    return False


def list_budgets() -> dict[str, float]:
    """Return {category: monthly_limit} sorted alphabetically."""
    return dict(sorted(_load_raw().items()))


# ---------------------------------------------------------------------------
# Spending check
# ---------------------------------------------------------------------------

def _current_month_display() -> str:
    """Return current month/year in DD-MM-YYYY format (1st of month)."""
    now = datetime.now()
    return now.strftime("%m-%Y")


def _in_current_month(date_str: str) -> bool:
    """Check if a DD-MM-YYYY date string falls in the current month."""
    try:
        dt = datetime.strptime(date_str, "%d-%m-%Y")
        now = datetime.now()
        return dt.month == now.month and dt.year == now.year
    except (ValueError, TypeError):
        return False


def get_spending(category: str) -> float:
    """Return total spending (€, positive) for *category* this month."""
    total = 0.0
    try:
        with FINANCE_LOG.open(newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 4:
                    continue
                if not _in_current_month(row[0]):
                    continue
                if row[2].strip().lower() != category.strip().lower():
                    continue
                amount = float(row[3])
                if amount < 0:  # expense
                    total += abs(amount)
    except (FileNotFoundError, OSError):
        pass
    return round(total, 2)


def check_all() -> dict[str, dict[str, float]]:
    """
    Return mapping of every budgeted category to its status.

    Result: {category: {"limit": 300, "spent": 245.5, "percent": 81.8}}
    """
    budgets = _load_raw()
    result: dict[str, dict[str, float]] = {}
    for cat, limit in budgets.items():
        spent = get_spending(cat)
        pct = round((spent / limit) * 100, 1) if limit > 0 else 0.0
        result[cat] = {"limit": limit, "spent": spent, "percent": pct}
    return result


def check_category(category: str) -> dict[str, float] | None:
    """Return status dict for *category*, or None if no budget set."""
    budgets = _load_raw()
    cat = category.strip().lower()
    if cat not in budgets:
        return None
    limit = budgets[cat]
    spent = get_spending(cat)
    pct = round((spent / limit) * 100, 1) if limit > 0 else 0.0
    return {"limit": limit, "spent": spent, "percent": pct}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

WARN_THRESHOLD = 80  # percent — warn when spending exceeds this

def get_warnings() -> list[str]:
    """
    Return a list of warning strings for categories near/over budget.
    Empty list = all good.
    """
    warnings: list[str] = []
    for cat, status in check_all().items():
        pct = status["percent"]
        limit = status["limit"]
        spent = status["spent"]
        if pct >= 100:
            warnings.append(f"  {cat}  {spent:.0f}/{limit:.0f}€  exceeded")
        elif pct >= WARN_THRESHOLD:
            remaining = limit - spent
            warnings.append(f"  {cat}  {spent:.0f}/{limit:.0f}€  {remaining:.0f}€ left")
    return warnings


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_status() -> str:
    """Full budget status — all categories with bars."""
    all_status = check_all()
    if not all_status:
        return "> Budget Status\n  no budgets set"

    lines = ["> Budget Status"]
    total_limit = 0.0
    total_spent = 0.0
    longest_cat = max((len(c) for c in all_status), default=0)

    for cat, s in all_status.items():
        limit = s["limit"]
        spent = s["spent"]
        pct = s["percent"]
        total_limit += limit
        total_spent += spent

        filled = min(int(pct / 10), 10)
        bar = "█" * filled + "░" * (10 - filled)
        tag = f"  {pct:.0f}% exceeded" if pct >= 100 else ""
        lines.append(f"  {cat:<{longest_cat}}  {bar}  {spent:.0f}/{limit:.0f}€{tag}")

    if len(all_status) > 1:
        overall_pct = round((total_spent / total_limit) * 100, 1) if total_limit > 0 else 0
        lines.append(f"  {'─' * (longest_cat + 30)}")
        lines.append(f"  {'total':<{longest_cat}}  {total_spent:.0f}/{total_limit:.0f}€  ({overall_pct:.0f}%)")

    return "\n".join(lines)


def format_category_status(category: str) -> str | None:
    """Status for a single category, or None if not budgeted."""
    s = check_category(category)
    if s is None:
        return None
    limit = s["limit"]
    spent = s["spent"]
    pct = s["percent"]
    filled = min(int(pct / 10), 10)
    bar = "█" * filled + "░" * (10 - filled)
    tag = f"  {pct:.0f}% exceeded" if pct >= 100 else ""
    return f"> {category}\n  {bar}  {spent:.0f}/{limit:.0f}€{tag}"


def format_recap() -> str | None:
    """Month-end recap — only if budgets exist."""
    all_status = check_all()
    if not all_status:
        return None

    lines = [f"> Monthly Recap  {_current_month_display()}"]
    total_limit = 0.0
    total_spent = 0.0
    longest_cat = max((len(c) for c in all_status), default=0)

    for cat, s in all_status.items():
        limit = s["limit"]
        spent = s["spent"]
        pct = s["percent"]
        total_limit += limit
        total_spent += spent
        left = max(limit - spent, 0)
        over = max(spent - limit, 0)
        detail = f"{left:.0f}€ left" if pct <= 100 else f"{over:.0f}€ over"
        filled = min(int(pct / 10), 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"  {cat:<{longest_cat}}  {bar}  {spent:.0f}/{limit:.0f}€  {detail}")

    if len(all_status) > 1:
        overall_pct = round((total_spent / total_limit) * 100, 1) if total_limit > 0 else 0
        lines.append(f"  {'─' * (longest_cat + 30)}")
        lines.append(f"  {'total':<{longest_cat}}  {total_spent:.0f}/{total_limit:.0f}€  ({overall_pct:.0f}%)")

    return "\n".join(lines)
