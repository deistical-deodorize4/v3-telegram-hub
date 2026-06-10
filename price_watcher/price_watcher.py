"""
Price Watch — track product prices across multiple shops.
Sequential per-item to keep Pi Zero 2W memory flat.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from price_watcher.scrapers import PriceScrapeError, scrape

log = logging.getLogger("aihub.price_watcher")

_WATCHER_DIR = Path(__file__).resolve().parent
CONFIG_FILE: Path = _WATCHER_DIR / "watchlist.json"
HISTORY_FILE: Path = _WATCHER_DIR / "price_history.csv"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class WatchUrl:
    url: str
    site: str
    currency: str

    @classmethod
    def from_dict(cls, d: dict) -> WatchUrl:
        return cls(url=d["url"], site=d["site"], currency=d["currency"].upper())

    def to_dict(self) -> dict:
        return {"url": self.url, "site": self.site, "currency": self.currency}


@dataclass
class WatchItem:
    id: str
    name: str
    name_keywords: list[str] = field(default_factory=list)
    urls: list[WatchUrl] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> WatchItem:
        return cls(
            id=d["id"],
            name=d["name"],
            name_keywords=d.get("name_keywords", [d["name"]]),
            urls=[WatchUrl.from_dict(u) for u in d.get("urls", [])],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "name_keywords": list(self.name_keywords),
            "urls": [u.to_dict() for u in self.urls],
        }


@dataclass
class PriceSample:
    timestamp: str
    item_id: str
    site: str
    url: str
    price: float
    currency: str
    name_matched: bool


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> list[WatchItem]:
    if not CONFIG_FILE.exists():
        log.warning("No watchlist at %s", CONFIG_FILE)
        return []
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Config error: %s", exc)
        return []
    items_raw = raw if isinstance(raw, list) else raw.get("items", [])
    return [WatchItem.from_dict(d) for d in items_raw]


# ---------------------------------------------------------------------------
# CSV store
# ---------------------------------------------------------------------------

CSV_HEADERS = ["timestamp", "item_id", "site", "url", "price", "currency", "name_matched"]


def append_sample(sample: PriceSample) -> None:
    new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(CSV_HEADERS)
        writer.writerow([
            sample.timestamp, sample.item_id, sample.site, sample.url,
            f"{sample.price:.2f}", sample.currency,
            "1" if sample.name_matched else "0",
        ])


def get_previous_price(url: str) -> PriceSample | None:
    if not HISTORY_FILE.exists():
        return None
    best = None
    try:
        with HISTORY_FILE.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("url", "").strip() != url.strip():
                    continue
                try:
                    s = PriceSample(
                        timestamp=row["timestamp"], item_id=row["item_id"],
                        site=row["site"], url=row["url"],
                        price=float(row["price"]), currency=row["currency"],
                        name_matched=row.get("name_matched", "0") == "1",
                    )
                except (KeyError, ValueError):
                    continue
                if best is None or s.timestamp > best.timestamp:
                    best = s
    except OSError:
        pass
    return best


# ---------------------------------------------------------------------------
# Check one item
# ---------------------------------------------------------------------------

def check_item(item: WatchItem, timeout: int = 25) -> list[dict[str, Any]]:
    results = []
    for wurl in item.urls:
        start = time.time()
        prev = get_previous_price(wurl.url)

        try:
            price, currency, site_name, product_name, name_matched = scrape(
                wurl.site, wurl.url, item.name_keywords, timeout=timeout,
            )
            success = True
            error = None
        except PriceScrapeError as exc:
            price = currency = site_name = product_name = None
            name_matched = False
            success = False
            error = str(exc)

        elapsed = time.time() - start
        if success:
            log.info("%s @ %s: %.2f %s name_ok=%s [%.2fs]",
                     item.id, site_name, price, currency, name_matched, elapsed)
        else:
            log.info("%s @ %s: FAIL (%s) [%.2fs]", item.id, wurl.site, error, elapsed)

        if success and price is not None:
            append_sample(PriceSample(
                timestamp=datetime.now(timezone.utc).isoformat(),
                item_id=item.id, site=site_name, url=wurl.url,
                price=price, currency=currency, name_matched=name_matched,
            ))

        results.append({
            "item_id": item.id,
            "item_name": item.name,
            "site": site_name,
            "url": wurl.url,
            "price": price,
            "currency": currency,
            "previous_price": prev.price if prev else None,
            "previous_currency": prev.currency if prev else None,
            "name_matched": name_matched,
            "product_name": product_name,
            "error": error,
        })
    return results


def check_all(timeout: int = 25) -> list[dict[str, Any]]:
    items = load_config()
    if not items:
        return []
    results = []
    for item in items:
        log.info("Checking %s (%s)", item.id, item.name)
        results.extend(check_item(item, timeout=timeout))
        time.sleep(1)
    return results


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_changes(results: list[dict]) -> list[dict]:
    changed = []
    for r in results:
        if r["error"] or r["price"] is None or r["previous_price"] is None:
            continue
        # Skip if currencies don't match — comparison would be meaningless
        cur = r.get("currency")
        pcur = r.get("previous_currency")
        if cur != pcur:
            log.warning("Currency mismatch for %s @ %s: %s vs %s, skipping change",
                        r["item_id"], r["site"], cur, pcur)
            continue
        diff = round(r["price"] - r["previous_price"], 2)
        if abs(diff) > 0.01:
            r["change"] = "up" if diff > 0 else "down"
            r["diff"] = diff
            changed.append(r)
    return changed


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _price_str(p: float, c: str) -> str:
    return f"{p:.2f} {c}"


def format_ondemand(results: list[dict] | None = None) -> str:
    if results is None:
        results = check_all()
    if not results:
        return "> Price Watch\n  no items"

    changes_count = 0
    currency_flips = 0
    for r in results:
        if r["error"] or r["price"] is None:
            continue
        prev = r.get("previous_price")
        pcur = r.get("previous_currency")
        cur = r.get("currency")
        if prev is not None and cur != pcur:
            currency_flips += 1
        elif prev is not None and cur == pcur and abs(r["price"] - prev) > 0.01:
            changes_count += 1

    lines = ["> Price Watch"]
    if changes_count or currency_flips:
        parts = []
        if changes_count:
            parts.append(f"{changes_count} change{'s' if changes_count != 1 else ''}")
        if currency_flips:
            parts.append(f"{currency_flips} currency change{'s' if currency_flips != 1 else ''}")
        lines.append(f"  {', '.join(parts)}")
    by_item: dict[str, list[dict]] = {}
    for r in results:
        by_item.setdefault(r["item_name"], []).append(r)

    lines.append("")
    for name, entries in by_item.items():
        lines.append(f"  {name}")
        for e in entries:
            if e["error"]:
                lines.append(f"    {e['site']}  broken link")
            else:
                curr = _price_str(e["price"], e["currency"])
                prev = e.get("previous_price")
                pcur = e.get("previous_currency")
                cur = e.get("currency")
                if prev is not None and pcur == cur and abs(e["price"] - prev) > 0.01:
                    arrow = "↑" if e["price"] > prev else "↓"
                    lines.append(
                        f"    {e['site']:<10} {curr}  {arrow}  from {_price_str(prev, pcur)}"
                    )
                elif prev is not None and pcur != cur:
                    lines.append(
                        f"    {e['site']:<10} {curr}  currency changed (was {_price_str(prev, pcur)})"
                    )
                else:
                    lines.append(f"    {e['site']:<10} {curr}")
    return "\n".join(lines)


def format_alerts(changes: list[dict]) -> str | None:
    if not changes:
        return None
    lines = ["> Price Alert"]
    for c in changes:
        lines.append(
            f"  {c['item_name']} ({c['site']})"
        )
        lines.append(
            f"    {_price_str(c['price'], c['currency'])}  ({c['diff']:+.2f} {c['currency']})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    results = check_all()
    print(format_ondemand(results))
    changes = detect_changes(results)
    alert = format_alerts(changes)
    if alert:
        print("\n" + "=" * 40 + "\n" + alert)


if __name__ == "__main__":
    main()
