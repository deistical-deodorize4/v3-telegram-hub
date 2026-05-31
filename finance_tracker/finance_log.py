"""
Personal expense / income tracker.

Logs transactions to a CSV file, shows recent entries, and displays
monthly stats with category breakdowns.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date

# Ensure config is importable when run as `python finance_tracker/finance_log.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import FINANCE_LOG  # noqa: E402


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _ensure_header() -> None:
    if not FINANCE_LOG.exists():
        FINANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FINANCE_LOG.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "type", "category", "amount", "description"])
        print("Finance log file created!\n")


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _get_float(prompt: str) -> float:
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print("Please enter a valid number")


def _get_type() -> str:
    while True:
        t = input("Type (fixed/variable): ").strip().lower()
        if t in ("fixed", "variable"):
            return t
        print("Please enter 'fixed' or 'variable'")


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
    filtered = [r for r in data if r[0].startswith(month_str)]
    if not filtered:
        print(f"No data found for {month_str}\n")
    return filtered


# ---------------------------------------------------------------------------
# Core logging
# ---------------------------------------------------------------------------

def log_entry() -> None:
    print("\n--- Log Expense/Income ---")

    today = date.today().isoformat()
    print(f"Date: {today}")

    entry_type = _get_type()
    category = input("Category: ").strip().lower()
    amount = _get_float("Amount (+ / -): ")
    description = input("Description: ").strip()

    with FINANCE_LOG.open("a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([today, entry_type, category, amount, description])

    print(f"\n✓ Logged: {category} | {amount:.2f}€ | {entry_type}\n")


# ---------------------------------------------------------------------------
# View recent
# ---------------------------------------------------------------------------

def view_recent() -> None:
    if not FINANCE_LOG.exists():
        print("No log file found.\n")
        return

    with FINANCE_LOG.open() as f:
        rows = list(csv.reader(f))

    if len(rows) <= 1:
        print("No entries yet.\n")
        return

    print("\n--- Recent Entries (last 5) ---")
    print(f"{'Date':<12} {'Type':<10} {'Category':<15} {'Amount':<12} {'Description'}")
    print("-" * 70)

    for row in rows[-5:]:
        desc = row[4] if row[4] else "-"
        amount = float(row[3])
        print(f"{row[0]:<12} {row[1]:<10} {row[2]:<15} {amount:>10.2f}€   {desc}")
    print()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def view_stats() -> None:
    if not FINANCE_LOG.exists():
        print("No log file found.\n")
        return

    with FINANCE_LOG.open() as f:
        rows = list(csv.reader(f))

    if len(rows) <= 1:
        print("No entries yet.\n")
        return

    data = rows[1:]

    # month filter
    while True:
        month = input("Enter month (YYYY-MM) or press Enter for all-time: ").strip()
        if _valid_month(month):
            break
        print("Invalid format. Use YYYY-MM")

    data = _filter_by_month(data, month)

    if not data:
        return

    # totals
    total_income = 0.0
    total_expenses = 0.0
    fixed_total = 0.0
    variable_total = 0.0

    for r in data:
        amount = float(r[3])
        if amount > 0:
            total_income += amount
        else:
            total_expenses += amount
            if r[1] == "fixed":
                fixed_total += amount
            elif r[1] == "variable":
                variable_total += amount

    balance = total_income + total_expenses

    print("\n--- Financial Summary ---")
    if month:
        print(f"Period: {month}")
    print(f"Total income:     {total_income:.2f}€")
    print(f"Total expenses:   {total_expenses:.2f}€")
    print(f"Balance:          {balance:.2f}€")
    print()
    print(f"Fixed expenses:   {fixed_total:.2f}€")
    print(f"Variable expenses: {variable_total:.2f}€")

    if total_income > 0:
        savings_rate = (balance / total_income) * 100
        print(f"Savings rate:     {savings_rate:.1f}%")

    # category breakdown
    print("\n--- Expense Breakdown by Category ---")

    category_totals: dict[str, float] = {}
    total_exp_abs = abs(total_expenses)

    for r in data:
        amount = float(r[3])
        if amount < 0:
            cat = r[2]
            category_totals[cat] = category_totals.get(cat, 0) + abs(amount)

    if not category_totals:
        print("No expenses recorded.\n")
        return

    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    for cat, amt in sorted_cats:
        percent = (amt / total_exp_abs) * 100 if total_exp_abs > 0 else 0
        bar = "#" * int(percent // 2)
        print(f"{cat:<15} {amt:>8.2f}€   ({percent:>5.1f}%) {bar}")

    print()


# ---------------------------------------------------------------------------
# CLI main loop
# ---------------------------------------------------------------------------

def main() -> None:
    _ensure_header()
    print("Finance Tracker")
    print("Commands: 'log' | 'view' | 'stats' | 'q'\n")

    while True:
        command = input("Command: ").strip().lower()
        if command == "q":
            print("Cheerio!")
            break
        elif command == "log":
            log_entry()
        elif command == "view":
            view_recent()
        elif command == "stats":
            view_stats()
        else:
            print("Unknown command. Use 'log', 'view', 'stats' or 'q'\n")


if __name__ == "__main__":
    main()
