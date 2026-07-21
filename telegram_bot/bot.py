"""
Telegram bot for pi02w Hub.

Provides a custom-keyboard interface for all six features via
python-telegram-bot v21+, plus a daily health report at 22:00
Europe/Madrid with spikes, averages, and alerts.
"""

from __future__ import annotations

from collections import defaultdict
import csv
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path (for direct `python bot.py` invocation)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config as cfg
from system_monitor import monitor as sysmon
from weather_forecaster import weather_aemet
from price_watcher import price_watcher as pw
from reminder import reminder as rmd
from impulse_buy import wish as ibw
from finance_tracker import budget as bgt
from study_tracker import dashboard as stdash
from lens_tracker import tracker as lens
from telegram_bot import printer as prn
from utils import log, setup_logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
setup_logging(logging.WARNING)

# ---------------------------------------------------------------------------
# Credentials & guards
# ---------------------------------------------------------------------------
TOKEN = cfg.TELEGRAM_BOT_TOKEN
ALLOWED_USER = cfg.TELEGRAM_USER_ID

if not TOKEN:
    log.error("TELEGRAM_BOT_TOKEN not set — bot cannot start.")
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Daily stats collector
# ---------------------------------------------------------------------------

class DailyStats:
    """In-memory ring of hardware samples for today's report.

    Samples are keyed by date (ISO string) so midnight rollover is automatic.
    Each sample stores a small dict with CPU, RAM, temp, disk & throttling data.
    """

    def __init__(self) -> None:
        self._samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def record(self, snap: sysmon.SystemSnapshot) -> None:
        """Append a snapshot to today's sample list."""
        today = date.today().isoformat()
        now_str = datetime.now().strftime("%H:%M")
        ld = snap.load_avg
        self._samples[today].append({
            "time_str": now_str,
            "timestamp": time.time(),
            "cpu": snap.cpu.value if snap.cpu else 0.0,
            "ram_used_mb": snap.ram.used_mb if snap.ram else 0.0,
            "ram_available_mb": snap.ram.available_mb if snap.ram else 0.0,
            "ram_total_mb": snap.ram.total_mb if snap.ram else 0.0,
            "temp": snap.temp.value if snap.temp else None,
            "disk_percent": snap.disk.percent if snap.disk else 0.0,
            "disk_free_gb": snap.disk.free_gb if snap.disk else 0.0,
            "throttled": snap.throttled.state if snap.throttled else "ok",
            "load_1m": ld.one["value"] if ld else None,
            "load_5m": ld.five["value"] if ld else None,
            "load_15m": ld.fifteen["value"] if ld else None,
            "load_cores": ld.cores if ld else None,
        })

    def build_report(self, day: str | None = None) -> str | None:
        """Build the daily tl;dr report string, or return None if no data."""
        if day is None:
            day = date.today().isoformat()
        samples = self._samples.get(day)
        if not samples:
            return None

        n = len(samples)

        # ---- CPU ----
        cpu_vals = [s["cpu"] for s in samples]
        cpu_avg = sum(cpu_vals) / n
        cpu_max_s = max(samples, key=lambda s: s["cpu"])
        cpu_now = cpu_vals[-1]

        # ---- RAM ----
        ram_used_vals = [s["ram_used_mb"] for s in samples]
        ram_avail_vals = [s["ram_available_mb"] for s in samples]
        ram_avg = sum(ram_used_vals) / n
        ram_min_free_s = min(samples, key=lambda s: s["ram_available_mb"])
        ram_total = samples[-1]["ram_total_mb"]
        ram_free_now = ram_avail_vals[-1]

        # ---- Temperature ----
        temp_vals = [s["temp"] for s in samples if s["temp"] is not None]
        if temp_vals:
            temp_avg = sum(temp_vals) / len(temp_vals)
            temp_max_s = max(samples, key=lambda s: s["temp"] or 0)
            temp_now = temp_vals[-1]
        else:
            temp_avg = None
            temp_max_s = None
            temp_now = None

        # ---- Disk (latest) ----
        disk_free = samples[-1]["disk_free_gb"]
        disk_pct = samples[-1]["disk_percent"]

        # ---- Throttling ----
        throttled_events = [s for s in samples if s["throttled"] != "ok"]

        # ---- Build report ----
        lines = []
        header = f"> Daily Report — {day}"
        lines.append(header)

        # CPU line
        cpu_line = f"  CPU · avg {cpu_avg:.0f}% · max {cpu_max_s['cpu']:.0f}% at {cpu_max_s['time_str']} · now {cpu_now:.0f}%"
        lines.append(cpu_line)

        # RAM line
        ram_free_pct = (ram_free_now / ram_total * 100) if ram_total > 0 else 0
        ram_alert = "  !" if ram_free_now < 150 else ""
        ram_line = (
            f"  RAM · avg {ram_avg:.0f}MB · min free {ram_min_free_s['ram_available_mb']:.0f}MB "
            f"at {ram_min_free_s['time_str']} · now {ram_free_now:.0f}MB ({ram_free_pct:.0f}% free){ram_alert}"
        )
        lines.append(ram_line)

        # Temp line
        if temp_avg is not None and temp_max_s is not None:
            temp_alert = "  !" if temp_max_s["temp"] and temp_max_s["temp"] > 65 else ""
        else:
            temp_alert = ""
        if temp_avg is not None and temp_max_s is not None:
            temp_now_str = f"{temp_now:.1f}°C" if temp_now is not None else "N/A"
            temp_line = (
                f"  Temp · avg {temp_avg:.1f}°C · max {temp_max_s['temp']:.1f}°C "
                f"at {temp_max_s['time_str']} · now {temp_now_str}{temp_alert}"
            )
        else:
            temp_line = "  Temp · N/A (sensor unavailable)"
        lines.append(temp_line)

        # Disk line
        disk_alert = "  !" if disk_pct > 75 else ""
        lines.append(f"  Disk · {disk_free:.1f}GB free ({disk_pct:.0f}%){disk_alert}")

        # Load average line
        load_1m_vals = [s["load_1m"] for s in samples if s["load_1m"] is not None]
        if load_1m_vals:
            load_1m_avg = sum(load_1m_vals) / len(load_1m_vals)
            load_1m_max = max(load_1m_vals)
            cores = samples[-1].get("load_cores", 4) or 4
            load_alert = "  !" if load_1m_max > cores else ""
            lines.append(
                f"  Load · avg {load_1m_avg:.2f} · max {load_1m_max:.2f} "
                f"({cores} cores){load_alert}"
            )

        # SD wear
        sd_wear = _read_sd_wear()
        if sd_wear:
            label = "lifetime" if sd_wear["type"] == "lifetime" else "since boot"
            lines.append(f"  SD Wear · {sd_wear['total_gb']}GB ({label})")

        # ---- Alerts section ----
        alerts = []
        if cpu_avg > 80:
            alerts.append(
                f"  CPU avg at {cpu_avg:.0f}% (> 80% threshold)"
            )
        if ram_free_now < 150:
            alerts.append(
                f"  RAM free critically low: {ram_free_now:.0f}MB (< 150MB) "
                f"at {ram_min_free_s['time_str']}"
            )
        if load_1m_vals and load_1m_max > cores:
            alerts.append(
                f"  Load peaked at {load_1m_max:.2f} (saturated, ≥ {cores} cores)"
            )
        if temp_max_s and temp_max_s["temp"] and temp_max_s["temp"] > 65:
            alerts.append(
                f"  Temp peaked at {temp_max_s['temp']:.1f}°C (> 65°C threshold)"
            )
        if disk_pct > 75:
            alerts.append(f"  Disk usage at {disk_pct:.0f}% (> 75% threshold)")

        if throttled_events:
            alerts.append(f"  {len(throttled_events)} throttling event(s) detected")

        if alerts:
            lines.append("")
            lines.append("  alerts")
            lines.extend(alerts)

        # ---- Throttling status ----
        lines.append("")
        lines.append("  ok: no throttling events" if not throttled_events else "  ! throttling occurred today")

        # ---- Sample count & footer ----
        lines.append("")
        lines.append(f"  {n} samples collected")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Global stats collector instance
# ---------------------------------------------------------------------------
daily_stats = DailyStats()

MADRID_TZ = ZoneInfo("Europe/Madrid")


def _seconds_until_sampling_slot() -> float:
    """Return seconds until the next :15 or :45 mark."""
    now = datetime.now()
    minutes = now.minute
    sec = now.second + now.microsecond / 1_000_000
    if minutes < 15:
        return (15 - minutes) * 60 - sec
    if minutes < 45:
        return (45 - minutes) * 60 - sec
    return (75 - minutes) * 60 - sec  # next hour at :15


def _seconds_until(hour: int, minute: int = 0) -> float:
    """Return seconds until next *hour:minute* Europe/Madrid."""
    now = datetime.now(MADRID_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_22_madrid() -> float:
    return _seconds_until(22, 0)


def _seconds_until_09_madrid() -> float:
    return _seconds_until(9, 0)


def _read_sd_wear() -> dict | None:
    """Read SD card wear metrics.

    Returns a dict with ``total_gb`` and ``type`` ("lifetime" or "boot"),
    or ``None`` if neither source is available.
    """
    # 1. Persistent lifetime counter (ext4 filesystem — survives reboots)
    try:
        with open("/sys/fs/ext4/mmcblk0p2/lifetime_write_kbytes") as f:
            total_kb = int(f.read().strip())
        return {
            "total_gb": round(total_kb / (1024 * 1024), 1),
            "type": "lifetime",
        }
    except (FileNotFoundError, ValueError, OSError):
        pass

    # 2. Fallback: since-boot counter from /proc/diskstats
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if parts[2] == "mmcblk0":
                    sectors = int(parts[9])  # field 9 = sectors written
                    total_gb = (sectors * 512) / (1024**3)
                    return {"total_gb": round(total_gb, 1), "type": "boot"}
    except (FileNotFoundError, ValueError, IndexError, OSError):
        pass

    return None


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

async def sample_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic hardware snapshot (every 30 min at :15 / :45)."""
    snap = sysmon.snapshot()
    daily_stats.record(snap)


async def morning_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build and send the morning weather brief (09:00), then re-schedule."""
    try:
        report = weather_aemet.format_morning_report()
        if report:
            await context.bot.send_message(
                chat_id=ALLOWED_USER, text=report, parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=ALLOWED_USER,
                text="> Morning\n  aemet data unavailable",
            )
    except Exception as exc:
        log.error("Morning report send failed: %s", exc)

    # Check for impulse buy wishes due for re-evaluation
    try:
        due = ibw.get_due_for_recheck()
        for w in due:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes, buy it", callback_data=f"impulse_yes_{w.id}"),
                    InlineKeyboardButton("No, pass", callback_data=f"impulse_no_{w.id}"),
                ]
            ])
            await context.bot.send_message(
                chat_id=ALLOWED_USER,
                text=ibw.format_recheck_prompt(w),
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            ibw.mark_asked(w.id)
        if due:
            log.info("Impulse re-check: asked about %d wish(es)", len(due))
    except Exception as exc:
        log.error("Impulse re-check failed: %s", exc)
    finally:
        # Re-schedule for tomorrow — always, even if sending failed
        _schedule_morning_report(context.job_queue)


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build and send the daily tl;dr, then re-schedule for tomorrow."""
    try:
        report = daily_stats.build_report()
        if report:
            await context.bot.send_message(
                chat_id=ALLOWED_USER, text=report, parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=ALLOWED_USER,
                text="> Daily Report\n  no data collected today (bot just started).",
            )

        # Month-end budget recap
        tomorrow = date.today() + timedelta(days=1)
        if tomorrow.day == 1:
            recap = bgt.format_recap()
            if recap:
                await context.bot.send_message(
                    chat_id=ALLOWED_USER, text=recap, parse_mode="Markdown",
                )
    except Exception as exc:
        log.error("Daily report send failed: %s", exc)
    finally:
        # Re-schedule next day's report — always, even if sending failed
        _schedule_daily_report(context.job_queue)


async def price_watch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly price check — alerts on changes."""
    log.info("Running hourly price watch…")
    try:
        results = pw.check_all()
        changes = pw.detect_changes(results)
        alert = pw.format_alerts(changes)
        if alert:
            await context.bot.send_message(
                chat_id=ALLOWED_USER, text=alert, parse_mode="Markdown",
            )
        else:
            log.info("No price changes detected")
    except Exception as exc:
        log.error("Price watch job failed: %s", exc)


async def reminder_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for due reminders every 30 seconds."""
    try:
        due = rmd.get_due_reminders(cfg.TIMEZONE)
        for r in due:
            day_name = r.dt.strftime("%A").capitalize()
            await context.bot.send_message(
                chat_id=ALLOWED_USER,
                text=(
                    f"> Reminder\n"
                    f"  {r.message}\n"
                    f"  {day_name} {r.dt.strftime('%d-%m')}  {r.dt.strftime('%H:%M')}"
                ),
                parse_mode="Markdown",
            )
            rmd.mark_done(r.id, cfg.TIMEZONE)
        if due:
            rmd.cleanup_old(cfg.TIMEZONE)
    except Exception as exc:
        log.error("Reminder check failed: %s", exc)


def _schedule_sampling(job_queue) -> None:
    """Start 30-minute sampling aligned to :15 / :45."""
    delay = _seconds_until_sampling_slot()
    job_queue.run_repeating(sample_job, interval=1800, first=delay)
    log.info("Daily stats sampling started (first in %.0fs, then every 30 min)", delay)


def _schedule_morning_report(job_queue) -> None:
    """Schedule a one-shot brief for 09:00 Europe/Madrid (re-schedules itself)."""
    delay = _seconds_until_09_madrid()
    job_queue.run_once(morning_report_job, delay)
    log.info("Morning report scheduled at 09:00 Madrid (in %.0fs)", delay)


def _schedule_daily_report(job_queue) -> None:
    """Schedule a one-shot report for 22:00 Europe/Madrid (re-schedules itself)."""
    delay = _seconds_until_22_madrid()
    job_queue.run_once(daily_report_job, delay)
    log.info("Daily report scheduled at 22:00 Madrid (in %.0fs)", delay)


def _schedule_price_watch(job_queue) -> None:
    """Start hourly price checks."""
    delay = _seconds_until_sampling_slot()
    job_queue.run_repeating(price_watch_job, interval=cfg.PRICE_WATCH_INTERVAL_SECONDS, first=delay)
    log.info("Price watch scheduled every %d s (first in %.0fs)",
             cfg.PRICE_WATCH_INTERVAL_SECONDS, delay)


def _schedule_reminder_check(job_queue) -> None:
    """Check for due reminders every 30 seconds."""
    job_queue.run_repeating(reminder_check_job, interval=30, first=5)
    log.info("Reminder checker scheduled every 30 s (first in 5 s)")


# ---------------------------------------------------------------------------
# Startup / boot notification
# ---------------------------------------------------------------------------


def _get_uptime() -> float:
    """Return system uptime in hours (reads /proc/uptime)."""
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
            return seconds / 3600
    except (FileNotFoundError, ValueError, IndexError):
        return 0.0


async def startup_notification(app: Application) -> None:
    """Send a boot notification once when the bot connects.

    If system uptime is short (< 10 min), it was likely a power-cycle
    (⚠️).  Otherwise it's just a normal process restart (ℹ️).
    """
    now = datetime.now().strftime("%H:%M %Z")
    uptime_h = _get_uptime()

    if uptime_h < 0.17:  # less than ~10 minutes
        icon = "*(hard resetted) Pi02w*"
        note = "possible power outage/restart"
    else:
        icon = "*Pi02w*"
        note = "bot restarted (softly)"

    msg = (
        f"> {icon} *Hub*\n"
        f"  {now}\n"
        f"  uptime: {uptime_h:.1f}h  ·  {note}"
    )
    try:
        await app.bot.send_message(
            chat_id=ALLOWED_USER, text=msg, parse_mode="Markdown"
        )
    except Exception:
        log.warning("Could not send startup notification")


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
user_sessions: dict[int, dict[str, Any]] = {}

STUDY_STEPS = [
    ("unit", "Unit studied (1-69)?"),
    ("hours", "Hours studied (e.g. 1.5)?"),
    ("energy", "Energy level before studying (1-10)?"),
    ("sleep", "Hours of sleep last night?"),
    ("grade", "Grade received? (send . if none yet)"),
    ("rating", "Session quality rating (1-10)?"),
]

FINANCE_STEPS = [
    ("type", "Type? (fixed / variable)"),
    ("category", "Category? (e.g. food, transport, salary)"),
    ("amount", "Amount? (+ for income, - for expense. e.g. -12.50)"),
    ("description", "Description?"),
]

# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🌤 Weather", "🖨 Print"],
        ["💰 Finance Log", "📚 Study Log"],
        ["📈 Price Watch", "💸 Impulse Buy"],
        ["📢 Reminder", "👁 Lenses"],
        ["🕵️ Monitor", "📋 Commands"],
    ],
    resize_keyboard=True,
)

def _get_session(user_id: int) -> dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {"mode": "menu", "history": [], "form": {}}
    return user_sessions[user_id]


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("! unauthorized")
        return
    _get_session(user_id)["mode"] = "menu"
    await update.message.reply_text(
        "> *pi02w Hub*\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull the current day's report on demand."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("! unauthorized")
        return

    report = daily_stats.build_report()
    if report:
        await update.message.reply_text(report, parse_mode="Markdown")
    else:
        # No samples yet — take a live snapshot so there's something to show
        snap = sysmon.snapshot()
        daily_stats.record(snap)
        report = daily_stats.build_report()
        if report:
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
            await update.message.reply_text("> Daily Report\n  no data yet")


# ---------------------------------------------------------------------------
# Study Dashboard commands
# ---------------------------------------------------------------------------


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show study streak."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    cur, longest, dates = stdash.calc_streak()
    if cur == 0:
        await update.message.reply_text("> Streak\n  no data yet", parse_mode="Markdown")
        return

    lines = ["> Streak"]
    lines.append(f"  current  {cur} day{'s' if cur != 1 else ''}")
    lines.append(f"  longest  {longest} day{'s' if longest != 1 else ''}")

    if len(dates) <= 7:
        lines.append("  " + " · ".join(dates[-7:]))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show weekly study summary."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    args: list[str] = context.args if context.args else []
    week_arg = args[0] if args else None
    result = stdash.week_summary(week_arg)
    if result:
        await update.message.reply_text(result, parse_mode="Markdown")
    else:
        await update.message.reply_text("> Week\n  no data yet", parse_mode="Markdown")


async def units_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show unit coverage."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    result = stdash.unit_coverage()
    if result:
        await update.message.reply_text(result, parse_mode="Markdown")
    else:
        await update.message.reply_text("> Unit Coverage\n  no data yet", parse_mode="Markdown")


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all-time study progress."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    result = stdash.all_time_progress()
    if result:
        await update.message.reply_text(result, parse_mode="Markdown")
    else:
        await update.message.reply_text("> Study Progress\n  no data yet", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Budget command
# ---------------------------------------------------------------------------


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /budget: set, show, remove budget limits."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("! unauthorized")
        return

    args: list[str] = context.args if context.args else []
    text = " ".join(args).strip()

    if not text:
        # No args → show all budgets
        await update.message.reply_text(bgt.format_status(), parse_mode="Markdown")
        return

    # /budget rm <category>
    if text.lower().startswith("rm "):
        cat = text[3:].strip()
        if bgt.remove_budget(cat):
            await update.message.reply_text(f"> {cat}\n  removed", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"> {cat}\n  no budget set", parse_mode="Markdown")
        return

    # /budget <category> <amount>  — or just /budget <category> to show one
    parts = text.rsplit(maxsplit=1)
    category = parts[0].strip().lower()

    if len(parts) == 2:
        try:
            amount = float(parts[1])
            if amount <= 0:
                raise ValueError
            bgt.set_budget(category, amount)
            status = bgt.format_category_status(category)
            await update.message.reply_text(
                f"> {category}\n  set to {amount:.0f}€\n{status}",
                parse_mode="Markdown",
            )
        except ValueError:
            await update.message.reply_text("! amount must be positive", parse_mode="Markdown")
        return

    # Just category name → show that category
    status = bgt.format_category_status(category)
    if status:
        await update.message.reply_text(status, parse_mode="Markdown")
    else:
        await update.message.reply_text(f"> {category}\n  no budget set", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Price Watch — interactive flows
# ---------------------------------------------------------------------------

def _detect_site_from_url(url: str) -> tuple[str, str] | None:
    """Detect (site_key, currency) from a URL, or None if unknown."""
    domain = url.lower()
    if "seeedstudio.com" in domain:
        return "seeed", "USD"
    if "tiendatec.es" in domain:
        return "tiendatec", "EUR"
    if "amazon.es" in domain:
        return "amazon", "EUR"
    if "amazon.de" in domain:
        return "amazon", "EUR"
    if "amazon.co.uk" in domain:
        return "amazon", "GBP"
    if "amazon.com" in domain:
        return "amazon", "USD"
    return None


async def price_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the add-product flow."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    log.warning("price_add_start called by user %s", user_id)
    session = _get_session(user_id)
    session["mode"] = "price_add"
    session["form"] = {"name": "", "urls": [], "waiting_for": "name"}
    try:
        await update.message.reply_text(
            "> Price Watch\n  product name?\n  /cancel to cancel",
            parse_mode="Markdown",
        )
        log.warning("price_add_start reply sent OK")
    except Exception as e:
        log.error("price_add_start reply FAILED: %s", e)


async def price_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Finish URL entry and show preview (or finish editing)."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    form = session.get("form", {})

    # Handle price_edit mode: finish editing
    if session["mode"] == "price_edit":
        session["mode"] = "menu"
        session["form"] = {}
        await update.message.reply_text("> done", reply_markup=MENU_KEYBOARD)
        return

    if session["mode"] != "price_add" or form.get("waiting_for") != "url":
        await update.message.reply_text("Nothing to finish.")
        return

    if not form["urls"]:
        await update.message.reply_text("No URLs added yet. Send at least one link.")
        return

    # Show preview
    lines = ["> Preview", "───", ""]
    lines.append(f">> {form['name']}")
    for u in form["urls"]:
        lines.append(f"  {u['site_name']}: *{u['price']:.2f} {u['currency']}*")
    lines.append("")
    lines.append("Save? (y/n)")

    form["waiting_for"] = "confirm"
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def price_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel any price-watch operation."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    session["mode"] = "menu"
    session["form"] = {}
    await update.message.reply_text("> cancelled", reply_markup=MENU_KEYBOARD)


async def wishlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show impulse buy history."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    report = ibw.format_wishlist()
    if report:
        await update.message.reply_text(report, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "No wishes yet.\nTap Impulse Buy to add one.",
        )


async def impulse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Impulse Buy Yes/No button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("impulse_yes_"):
        wish_id = data.replace("impulse_yes_", "")
        w = ibw.get_wish_by_id(wish_id)
        ibw.mark_kept(wish_id)
        name = f" {w.text}" if w else ""
        await query.edit_message_text(f"> {name}\n  go for it")
    elif data.startswith("impulse_no_"):
        wish_id = data.replace("impulse_no_", "")
        w = ibw.get_wish_by_id(wish_id)
        ibw.mark_dropped(wish_id)
        name = f" {w.text}" if w else ""
        await query.edit_message_text(f"> {name}\n  dropped")


async def price_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show product list for removal."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    items = pw.load_config()
    if not items:
        await update.message.reply_text("No products in watchlist.")
        return

    lines = ["> Remove product", "───", ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item.name} (`{item.id}`)")
    lines.append("")
    lines.append("Send the number to remove or /cancel.")

    session["mode"] = "price_remove"
    session["form"] = {"items": items}
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def price_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run a fresh price check and show the report."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    session["mode"] = "price_menu"
    await update.message.reply_text("~ checking prices")
    try:
        results = pw.check_all()
        report = pw.format_ondemand(results)
        await update.message.reply_text(report, parse_mode="Markdown")
        changes = pw.detect_changes(results)
        alert = pw.format_alerts(changes)
        if alert:
            await update.message.reply_text(alert, parse_mode="Markdown")
    except Exception as exc:
        await update.message.reply_text(f"! {exc}")


async def price_test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the test-a-URL flow."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    session["mode"] = "price_test"
    session["form"] = {}
    await update.message.reply_text(
        "Send a URL to test, or /cancel.",
    )


async def price_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the edit-product flow."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    items = pw.load_config()
    if not items:
        await update.message.reply_text("No products in watchlist.")
        return

    lines = ["> Edit product", "───", ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. *{item.name}* (`{item.id}`)")
    lines.append("")
    lines.append("Send the number to edit or /cancel.")

    session["mode"] = "price_edit"
    session["form"] = {"step": "pick_item", "items": [i.to_dict() for i in items], "item_idx": None}
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def price_handle_edit_message(update: Update, text: str) -> None:
    """Handle messages during the price_edit flow."""
    user_id = update.effective_user.id
    session = _get_session(user_id)
    form = session.get("form", {})
    step = form.get("step", "pick_item")

    items = form.get("items", [])
    item_idx = form.get("item_idx")

    # ── Step: pick_item ────────────────────────────────────────────────
    if step == "pick_item":
        try:
            idx = int(text.strip()) - 1
            if idx < 0 or idx >= len(items):
                raise ValueError
        except (ValueError, IndexError):
            await update.message.reply_text(f"Send a number 1–{len(items)}.")
            return

        item = items[idx]
        form["item_idx"] = idx

        lines = [f">> {item['name']}", "───", ""]
        for i, u in enumerate(item.get("urls", []), 1):
            lines.append(f"{i}. {u['site']}: {u['url']}")
        lines.append("")
        lines.append("Send:")
        lines.append("  A         — add a URL")
        lines.append("  R <num>   — remove URL (e.g. R 2)")
        lines.append("  D or done — finish editing")
        lines.append("  /cancel — cancel")

        form["step"] = "action"
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # ── Step: action ───────────────────────────────────────────────────
    if step == "action":
        cmd = text.strip().lower()

        if cmd in ("a", "add"):
            form["step"] = "url"
            form["pending_url"] = {}
            await update.message.reply_text(
            "Send a link to add. I'll detect the site and test it.\n"
            "Send /cancel to cancel.",
            )
            return

        if cmd.startswith("r ") or cmd.startswith("remove "):
            try:
                url_idx = int(cmd.split()[-1]) - 1
                urls = items[item_idx].get("urls", [])
                if url_idx < 0 or url_idx >= len(urls):
                    raise ValueError
            except (ValueError, IndexError):
                await update.message.reply_text(
                    f"Send a valid number, e.g. R 2. "
                    f"Product has {len(items[item_idx].get('urls', []))} URLs."
                )
                return

            form["remove_url_idx"] = url_idx
            target = urls[url_idx]
            await update.message.reply_text(
                f"Remove this URL?\n"
                f"  {target['site']}: {target['url']}\n\n"
                "Confirm? (y/n)",
            )
            form["step"] = "remove_confirm"
            return

        if cmd in ("d", "done"):
            session["mode"] = "menu"
            session["form"] = {}
            await update.message.reply_text("> done", reply_markup=MENU_KEYBOARD)
            return

        await update.message.reply_text("Send A, R <num>, or D.")
        return

    # ── Step: url (same flow as add) ───────────────────────────────────
    if step == "url":
        detected = _detect_site_from_url(text)
        if not detected:
            await update.message.reply_text(
                "! can't detect site from that URL.\n"
                "Supported: seeedstudio.com, tiendatec.es, amazon.es / .com / .de / .co.uk\n"
                "Try again or /cancel."
            )
            return

        site_key, currency = detected
        status_msg = await update.message.reply_text("~ testing link")
        try:
            from price_watcher.scrapers import scrape
            item_name = items[item_idx]["name"]
            price, currency_got, site_name, product_name, matched = scrape(
                site_key, text, [item_name], timeout=20,
            )
            display_currency = currency_got or currency
        except Exception as exc:
            await status_msg.edit_text(
                f"! error scraping: {exc}\nSend another URL or /cancel."
            )
            return

        name_status = "matches" if matched else "*does NOT match*"
        await status_msg.edit_text(
            f">> {site_name}\n"
            f"  price: *{price:.2f} {display_currency}*\n"
            f"  name: {name_status}\n\n"
            "Save this link? (y/n)",
            parse_mode="Markdown",
        )
        form["pending_url"] = {
            "site": site_key,
            "site_name": site_name,
            "url": text,
            "currency": display_currency,
        }
        form["step"] = "url_confirm"
        return

    # ── Step: url_confirm ──────────────────────────────────────────────
    if step == "url_confirm":
        if text.lower() in ("y", "yes"):
            pending = form.pop("pending_url", None)
            if pending:
                items[item_idx].setdefault("urls", []).append({
                    "site": pending["site"],
                    "url": pending["url"],
                    "currency": pending["currency"],
                })
                _save_items(items)
                await update.message.reply_text("> saved")

                # Show updated product view
                item = items[item_idx]
                lines = [f">> {item['name']}", "───", ""]
                for i, u in enumerate(item.get("urls", []), 1):
                    lines.append(f"{i}. {u['site']}: {u['url']}")
                lines.append("")
                lines.append("Send:")
                lines.append("  A         — add another URL")
                lines.append("  R <num>   — remove a URL (e.g. R 2)")
                lines.append("  D or done — finish editing")
                lines.append("  /cancel — cancel")
                form["step"] = "action"
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
                return
        else:
            form.pop("pending_url", None)
            await update.message.reply_text("Discarded. Send A to add another URL, or D to finish.")
            form["step"] = "action"
        return

    # ── Step: remove_confirm ───────────────────────────────────────────
    if step == "remove_confirm":
        if text.lower() in ("y", "yes"):
            url_idx = form.get("remove_url_idx")
            removed = items[item_idx]["urls"].pop(url_idx)
            _save_items(items)
            await update.message.reply_text(f"> removed {removed['site']} link")

            # Show updated product view
            item = items[item_idx]
            if not item["urls"]:
                # All URLs removed — remove the product itself
                items.pop(item_idx)
                _save_items(items)
                session["mode"] = "menu"
                session["form"] = {}
                await update.message.reply_text(
                    "> no URLs left — removed",
                    reply_markup=MENU_KEYBOARD,
                )
                return

            lines = [f">> {item['name']}", "───", ""]
            for i, u in enumerate(item.get("urls", []), 1):
                lines.append(f"{i}. {u['site']}: {u['url']}")
            lines.append("")
            lines.append("Send:")
            lines.append("  A         — add a URL")
            lines.append("  R <num>   — remove a URL (e.g. R 2)")
            lines.append("  D or done — finish editing")
            lines.append("  /cancel — cancel")
            form["step"] = "action"
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text("Not removed. Send A, R <num>, or D.")
            form["step"] = "action"
        return


def _save_items(items: list[dict]) -> None:
    """Write the full watchlist to disk."""
    import json
    from price_watcher.price_watcher import CONFIG_FILE
    CONFIG_FILE.write_text(
        json.dumps({"items": items}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _save_product(form: dict) -> None:
    """Append a new product to watchlist.json."""
    import json
    from price_watcher.price_watcher import CONFIG_FILE, WatchItem, WatchUrl

    item_id = form["name"].lower().replace(" ", "-")[:30]
    # Avoid duplicate IDs
    existing = pw.load_config()
    used_ids = {e.id for e in existing}
    base_id = item_id
    n = 1
    while item_id in used_ids:
        item_id = f"{base_id}-{n}"
        n += 1

    urls = []
    for u in form["urls"]:
        urls.append({
            "site": u["site"],
            "url": u["url"],
            "currency": u["currency"],
        })

    entry = {
        "id": item_id,
        "name": form["name"],
        "name_keywords": form.get("name_keywords", [form["name"]]),
        "urls": urls,
    }

    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", [])
    else:
        items = []

    items.append(entry)
    _save_items(items)


async def price_handle_add_message(update: Update, text: str) -> bool:
    """Handle a message during the price_add flow. Returns True if handled."""
    user_id = update.effective_user.id
    session = _get_session(user_id)
    form = session.get("form", {})
    log.warning("price_handle_add_message waiting_for=%s", form.get("waiting_for"))

    # Guard: if form is lost (bot restart), reset to menu
    if "waiting_for" not in form:
        session["mode"] = "menu"
        session["form"] = {}
        await update.message.reply_text(
            "~ session expired. start over.\n"
            "Use /priceadd to add a product.",
            reply_markup=MENU_KEYBOARD,
        )
        return True

    # Step 1: waiting for product name
    if form["waiting_for"] == "name":
        if not text.strip():
            await update.message.reply_text("Name can't be empty.")
            return True
        form["name"] = text.strip()
        form["name_keywords"] = [text.strip()]
        form["waiting_for"] = "url"
        await update.message.reply_text(
            f">> {form['name']}\n\n"
            "Send a link. I'll detect the site and test it.\n"
            "Send /pricedone when finished or /cancel to cancel.",
        )
        return True

    # Step 2: waiting for URLs
    if form["waiting_for"] == "url":
        detected = _detect_site_from_url(text)
        if not detected:
            await update.message.reply_text(
                "! can't detect site from that URL.\n"
                "Supported: seeedstudio.com, tiendatec.es, amazon.es / .com / .de / .co.uk\n"
                "Try again or /pricedone."
            )
            return True

        site_key, currency = detected

        # Live-test the URL
        status_msg = await update.message.reply_text("~ testing link")
        try:
            from price_watcher.scrapers import scrape
            price, currency_got, site_name, product_name, matched = scrape(
                site_key, text, [form["name"]], timeout=20,
            )
            display_currency = currency_got or currency
        except Exception as exc:
            await status_msg.edit_text(
                f"! error scraping: {exc}\n"
                "Send another URL or /pricedone."
            )
            return True

        name_status = "matches" if matched else "*does NOT match*"
        await status_msg.edit_text(
            f">> {site_name}\n"
            f"  price: *{price:.2f} {display_currency}*\n"
            f"  name: {name_status}\n\n"
            "Save this link? (y/n)",
            parse_mode="Markdown",
        )
        form["pending_url"] = {
            "site": site_key,
            "site_name": site_name,
            "url": text,
            "currency": display_currency,
            "price": price,
            "name_matched": matched,
        }
        form["waiting_for"] = "url_confirm"
        return True

    # Step 3: URL confirmation (y/n)
    if form["waiting_for"] == "url_confirm":
        if text.lower() in ("y", "yes"):
            pending = form.pop("pending_url", None)
            if pending:
                form["urls"].append(pending)
                await update.message.reply_text(
                    f"> saved. send another URL or /pricedone"
                )
        else:
            form.pop("pending_url", None)
            await update.message.reply_text("Discarded. Send another URL or /pricedone.")
        form["waiting_for"] = "url"
        return True

    # Step 4: confirm save (y/n from preview)
    if form["waiting_for"] == "confirm":
        if text.lower() in ("y", "yes"):
            _save_product(form)
            session["mode"] = "menu"
            session["form"] = {}
            await update.message.reply_text(
                "> product added. run /pricereport to check",
                reply_markup=MENU_KEYBOARD,
            )
        else:
            session["mode"] = "menu"
            session["form"] = {}
            await update.message.reply_text("> not saved", reply_markup=MENU_KEYBOARD)
        return True

    return False


async def price_handle_remove_message(update: Update, text: str) -> bool:
    """Handle a number pick during removal flow."""
    user_id = update.effective_user.id
    session = _get_session(user_id)
    form = session["form"]
    items = form.get("items", [])

    try:
        idx = int(text.strip()) - 1
        if idx < 0 or idx >= len(items):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(f"Send a number 1–{len(items)}.")
        return True

    removed = items[idx]

    # Preview before deleting
    lines = ["> Preview", "───", ""]
    lines.append(f"  removing: *{removed.name}* (`{removed.id}`)")
    lines.append("")
    lines.append("Confirm? (y/n)")
    form["remove_idx"] = idx
    form["waiting_for"] = "remove_confirm"
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    return True


async def price_handle_message(update: Update, text: str) -> None:
    """Route messages to the right price-watch flow."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    mode = session["mode"]
    log.warning("price_handle_message mode=%s form=%s", mode, session.get("form"))

    if mode == "price_add":
        handled = await price_handle_add_message(update, text)
        if not handled:
            await update.message.reply_text("Hmm? Send a URL, /pricedone, or /cancel.")
        return

    if mode == "price_remove":
        # Check for removal confirmation (y/n)
        if session.get("form", {}).get("waiting_for") == "remove_confirm":
            form = session["form"]
            if text.lower() in ("y", "yes"):
                idx = form["remove_idx"]
                items = form["items"]
                removed_id = items[idx].id

                # Delete from watchlist.json
                import json
                from price_watcher.price_watcher import CONFIG_FILE
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                items_list = data if isinstance(data, list) else data.get("items", [])
                items_list = [i for i in items_list if i.get("id") != removed_id]
                CONFIG_FILE.write_text(
                    json.dumps({"items": items_list}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                session["mode"] = "menu"
                session["form"] = {}
                await update.message.reply_text(
                    f"> removed *{removed_id}*.", parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD,
                )
            else:
                session["mode"] = "menu"
                session["form"] = {}
                await update.message.reply_text("> not removed", reply_markup=MENU_KEYBOARD)
            return

        # Otherwise it's a number pick
        handled = await price_handle_remove_message(update, text)
        if not handled:
            await update.message.reply_text("Send a number or /cancel.")
        return

    if mode == "price_test":
        detected = _detect_site_from_url(text)
        if not detected:
            await update.message.reply_text(
                "! can't detect site.\n"
                "Supported: seeedstudio.com, tiendatec.es, amazon.es / .com"
            )
            return

        site_key, currency = detected
        status_msg = await update.message.reply_text("~ testing")
        try:
            from price_watcher.scrapers import scrape
            price, currency_got, site_name, product_name, matched = scrape(
                site_key, text, [], timeout=20,
            )
            display_currency = currency_got or currency
            name_str = f"  {product_name}" if product_name else ""
            await status_msg.edit_text(
                f">> {site_name}\n"
                f"  price: *{price:.2f} {display_currency}*\n"
                f"{name_str}",
                parse_mode="Markdown",
            )
        except Exception as exc:
            await status_msg.edit_text(f"! {exc}")

        session["mode"] = "price_menu"
        return

    if mode == "price_edit":
        await price_handle_edit_message(update, text)
        return

# ---------------------------------------------------------------------------
# Lens Tracker
# ---------------------------------------------------------------------------


async def lens_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    session["mode"] = "lens"
    session["form"] = {}
    await update.message.reply_text(lens.status(cfg.LENS_DATA), parse_mode="Markdown")


async def lens_refresh(update: Update, text: str) -> None:
    cmd = text.strip().lower()
    if cmd == "in":
        ok, msg = lens.start_session(cfg.LENS_DATA)
        await update.message.reply_text(f"{msg}", parse_mode="Markdown")
    elif cmd == "out":
        ok, msg, _ = lens.stop_session(cfg.LENS_DATA)
        await update.message.reply_text(f"{msg}", parse_mode="Markdown")
    elif cmd in ("new", "fresh"):
        ok, msg = lens.new_pair(cfg.LENS_DATA)
        await update.message.reply_text(f"{msg}", parse_mode="Markdown")
    else:
        await update.message.reply_text("Send `in`, `out`, or `new`.", parse_mode="Markdown")
    lens.reload()


async def lens_expiry_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = lens.check_expiry(cfg.LENS_DATA)
    if msg:
        await context.bot.send_message(chat_id=ALLOWED_USER, text=msg, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------


async def print_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the print flow — bot will ask for a PDF."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return
    session = _get_session(user_id)
    session["mode"] = "print"
    session["form"] = {}
    await update.message.reply_text(
        "> Print\n  send a PDF file\n  /cancel to cancel",
    )





async def handle_print_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download a PDF and send it to the printer."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        return

    session = _get_session(user_id)
    if session.get("mode") != "print":
        return

    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Only PDF files are supported.")
        return
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await update.message.reply_text("File too large (max 10 MB).")
        session["mode"] = "menu"
        session["form"] = {}
        return

    status_msg = await update.message.reply_text("~ downloading")
    try:
        file = await context.bot.get_file(doc.file_id)
        local_path = cfg.TEMP_DIR / f"print_{int(time.time())}_{doc.file_name or 'document.pdf'}"
        await file.download_to_drive(local_path)

        await status_msg.edit_text("~ sending to printer")
        success, msg = prn.print_pdf(local_path, cfg.PRINTER_ADDR, cfg.PRINTER_NAME)

        session["mode"] = "menu"
        session["form"] = {}

        if success:
            await status_msg.edit_text(f"> {msg}")
        else:
            await status_msg.edit_text(f"! {msg}")

        local_path.unlink(missing_ok=True)
    except Exception as e:
        session["mode"] = "menu"
        session["form"] = {}
        await status_msg.edit_text(f"! {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("! unauthorized")
        return

    text: str = update.message.text  # type: ignore[assignment]
    session = _get_session(user_id)
    log.warning("handle_message text=%r mode=%s", text, session.get("mode"))

    # ------- Weather (AEMET) -------
    if text == "🌤 Weather":
        await update.message.reply_text("~ fetching aemet data")
        try:
            report = weather_aemet.format_ondemand()
            if report:
                await update.message.reply_text(report, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "! Could not fetch AEMET data. Check your API key and internet."
                )
        except Exception as exc:
            await update.message.reply_text(f"! aemet error: {exc}")
        return

    # ------- Study Log -------
    if text == "📚 Study Log":
        session["mode"] = "study"
        session["form"] = {}
        await update.message.reply_text(STUDY_STEPS[0][1])
        return

    if session["mode"] == "study":
        step_index = len(session["form"])
        key, _ = STUDY_STEPS[step_index]

        try:
            if key == "unit":
                val = int(text)
                if not (1 <= val <= 69):
                    raise ValueError
            elif key in ("hours", "energy", "sleep", "rating"):
                val = float(text)
                constraints = {
                    "hours": (0.1, 12),
                    "energy": (1, 10),
                    "sleep": (0, 12),
                    "rating": (1, 10),
                }
                lo, hi = constraints[key]
                if not (lo <= val <= hi):
                    raise ValueError
            elif key == "grade":
                val = None if text.strip() == "." else float(text)
            else:
                val = text.strip()
        except (ValueError, TypeError):
            await update.message.reply_text(
                f"! Invalid value. Try again.\n{STUDY_STEPS[step_index][1]}"
            )
            return

        session["form"][key] = val

        if len(session["form"]) < len(STUDY_STEPS):
            await update.message.reply_text(STUDY_STEPS[len(session["form"])][1])
        else:
            f = session["form"]
            today = datetime.now().strftime("%d-%m-%Y")
            file_exists = cfg.STUDY_LOG.exists()
            with cfg.STUDY_LOG.open("a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(
                        ["date", "unit", "hours_studied", "energy_level", "sleep_hours", "grade", "rating"]
                    )
                writer.writerow(
                    [today, f["unit"], f["hours"], f["energy"], f["sleep"], f["grade"], f["rating"]]
                )
            session["mode"] = "menu"
            session["form"] = {}
            await update.message.reply_text(
                f"> Logged\n  unit {f['unit']}  ·  {f['hours']}h  ·  rating {f['rating']}/10",
                reply_markup=MENU_KEYBOARD,
            )
        return

    # ------- Finance Log -------
    if text == "💰 Finance Log":
        session["mode"] = "finance"
        session["form"] = {}
        await update.message.reply_text(FINANCE_STEPS[0][1])
        return

    if session["mode"] == "finance":
        step_index = len(session["form"])
        key, _ = FINANCE_STEPS[step_index]

        try:
            if key == "type":
                val = text.strip().lower()
                if val not in ("fixed", "variable"):
                    raise ValueError
            elif key == "amount":
                val = float(text)
            else:
                val = text.strip()
                if not val:
                    raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(
                f"! Invalid value. Try again.\n{FINANCE_STEPS[step_index][1]}"
            )
            return

        session["form"][key] = val

        if len(session["form"]) < len(FINANCE_STEPS):
            await update.message.reply_text(FINANCE_STEPS[len(session["form"])][1])
        else:
            f = session["form"]
            today = datetime.now().strftime("%d-%m-%Y")
            file_exists = cfg.FINANCE_LOG.exists()
            with cfg.FINANCE_LOG.open("a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["date", "type", "category", "amount", "description"])
                writer.writerow([today, f["type"], f["category"], f["amount"], f["description"]])
            session["mode"] = "menu"
            session["form"] = {}

            msg = f"> Logged\n  {f['category']}  ·  {float(f['amount']):+.2f}€  ·  {f['type']}"

            # Check budget warning for this category
            warnings = bgt.get_warnings()
            cat_warnings = [w for w in warnings if f"*{f['category']}*" in w]
            if cat_warnings:
                msg += "\n\n" + "\n".join(cat_warnings)

            await update.message.reply_text(
                msg,
                reply_markup=MENU_KEYBOARD,
                parse_mode="Markdown",
            )
        return

    # ------- Monitor -------
    if text in ("🕵️ Monitor", "/monitor"):
        try:
            report = sysmon.get_report()
            await update.message.reply_text(report)
        except Exception as exc:
            await update.message.reply_text(f"! monitor: {exc}")
        return

    # ------- Print -------
    if text in ("🖨 Print", "/print"):
        session["mode"] = "print"
        session["form"] = {}
        await update.message.reply_text(
            "> Print\n  send a PDF file\n  /cancel to cancel",
        )
        return

    # ------- Lens Tracker -------
    if text == "👁 Lenses":
        session["mode"] = "lens"
        session["form"] = {}
        await update.message.reply_text(lens.status(cfg.LENS_DATA), parse_mode="Markdown")
        return

    if session["mode"] == "lens":
        await lens_refresh(update, text)
        return

    # ------- Price Watch sub-menu -------
    if text == "📈 Price Watch":
        session["mode"] = "price_menu"
        results = pw.check_all()
        report = pw.format_ondemand(results)
        msg = (
            f"{report}\n"
            f"\n"
            f"  /priceadd     add product\n"
            f"  /priceedit    edit product\n"
            f"  /priceremove  remove product\n"
            f"  /pricetest    test a URL\n"
            f"  /pricereport  view report"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ------- Price Watch interactive flows -------
    if session["mode"] in ("price_add", "price_remove", "price_test", "price_edit"):
        await price_handle_message(update, text)
        return

    # ------- Reminder -------
    if text == "📢 Reminder":
        session["mode"] = "reminder_msg"
        session["form"] = {}
        await update.message.reply_text(
            "> Reminder\n  what to remind you about?",
        )
        return

    if session["mode"] == "reminder_msg":
        if not text.strip():
            await update.message.reply_text("Message can't be empty.")
            return
        session["form"]["msg"] = text.strip()
        session["mode"] = "reminder_time"
        await update.message.reply_text(
            "> When?\n  /cancel to cancel",
        )
        return

    if session["mode"] == "reminder_time":
        dt = rmd.parse_datetime(text, cfg.TIMEZONE)
        if dt is None:
            await update.message.reply_text(
                "! didnt understand. try again",
            )
            return

        if dt < datetime.now():
            await update.message.reply_text(
                "! that time is in the past"
            )
            return

        # Save immediately, no confirmation needed
        msg = session["form"]["msg"]
        r = rmd.add_reminder(msg, dt, cfg.TIMEZONE)
        session["mode"] = "menu"
        session["form"] = {}
        await update.message.reply_text(
            rmd.format_reminder(r),
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        return

    # ------- Impulse Buy -------
    if text == "💸 Impulse Buy":
        session["mode"] = "impulse_msg"
        session["form"] = {}
        await update.message.reply_text(
            "> Impulse Buy\n  what do you want to buy?\n  /cancel to cancel",
        )
        return

    if session["mode"] == "impulse_msg":
        if not text.strip():
            await update.message.reply_text("What do you want?")
            return
        w = ibw.add_wish(text.strip())
        session["form"] = {"wish_id": w.id}
        session["mode"] = "impulse_eval_uses"
        await update.message.reply_text(
            f"> {w.text}\n  saved\n\n"
            f"{ibw.EVAL_QUESTIONS[0][1]}",
            parse_mode="Markdown",
        )
        return

    # ------- Impulse Buy — evaluation flow -------
    if session["mode"] in ("impulse_eval_uses", "impulse_eval_alternative",
                           "impulse_eval_situations", "impulse_eval_money"):
        wish_id = session["form"].get("wish_id", "")
        w = ibw.get_wish_by_id(wish_id)
        if not w:
            await update.message.reply_text("! wish not found")
            session["mode"] = "menu"
            session["form"] = {}
            return

        eval_data = session["form"].setdefault("eval_data", {})

        if session["mode"] == "impulse_eval_uses":
            valid = ["0-1", "2-5", "6+"]
            if text.strip() not in valid and text.strip() not in ("0-1 veces", "2-5 veces", "6+ veces"):
                await update.message.reply_text(
                    "Reply: `0-1` `2-5` or `6+`",
                    parse_mode="Markdown",
                )
                return
            eval_data["uses"] = text.strip()
            session["mode"] = "impulse_eval_alternative"
            await update.message.reply_text(ibw.EVAL_QUESTIONS[1][1], parse_mode="Markdown")
            return

        if session["mode"] == "impulse_eval_alternative":
            if text.strip().lower() not in ("yes", "no"):
                await update.message.reply_text("Reply *Yes* or *No*.", parse_mode="Markdown")
                return
            eval_data["alternative"] = text.strip()
            session["mode"] = "impulse_eval_situations"
            await update.message.reply_text(ibw.EVAL_QUESTIONS[2][1], parse_mode="Markdown")
            return

        if session["mode"] == "impulse_eval_situations":
            if len(text.strip()) < 5:
                await update.message.reply_text("Describe at least one situation briefly.")
                return
            eval_data["situations"] = text.strip()
            session["mode"] = "impulse_eval_money"
            await update.message.reply_text(ibw.EVAL_QUESTIONS[3][1], parse_mode="Markdown")
            return

        if session["mode"] == "impulse_eval_money":
            if text.strip().lower() not in ("yes", "no"):
                await update.message.reply_text("Reply *Yes* or *No*.", parse_mode="Markdown")
                return
            eval_data["money"] = text.strip()

            # All answers collected → save evaluation and show result
            updated = ibw.save_evaluation(wish_id, eval_data)
            session["mode"] = "menu"
            session["form"] = {}
            if updated:
                await update.message.reply_text(
                    ibw.format_evaluation(updated),
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD,
                )
            else:
                await update.message.reply_text("! error saving", reply_markup=MENU_KEYBOARD)
            return

    # ------- Commands -------
    if text == "📋 Commands":
        cmds = (
            "> Commands\n\n"
            ">> General\n"
            "  /start       hub menu\n"
            "  /daily       daily report\n"
            "  /monitor     system monitor\n"
            "  /cancel      cancel flow\n"
            "\n"
            ">> Study\n"
            "  /streak      study streak\n"
            "  /week        weekly summary\n"
            "  /units       unit coverage\n"
            "  /progress    all-time progress\n"
            "\n"
            ">> Finance\n"
            "  /budget      budget limits\n"
            "\n"
            ">> Price Watch\n"
            "  /priceadd    add product\n"
            "  /priceedit   edit product\n"
            "  /priceremove remove product\n"
            "  /pricetest   test a URL\n"
            "  /pricedone   finish URLs\n"
            "  /pricereport view report\n"
            "\n"
            ">> Wishes\n"
            "  /wishlist    impulse buy history\n"
            "\n"
            ">> Tools\n"
            "  /print       print a PDF"
        )
        await update.message.reply_text(cmds, parse_mode="Markdown")
        return

    # ------- Default: show hub menu -------
    session["mode"] = "menu"
    session["history"] = []
    session["form"] = {}
    await update.message.reply_text(
        "> *pi02w Hub*\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )



# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the polling bot with scheduled jobs."""
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(startup_notification)
        .build()
    )

    # --- Register handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("monitor", handle_message))
    app.add_handler(CommandHandler("streak", streak_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("units", units_command))
    app.add_handler(CommandHandler("progress", progress_command))
    app.add_handler(CommandHandler("priceadd", price_add_start))
    app.add_handler(CommandHandler("pricedone", price_done))
    app.add_handler(CommandHandler("cancel", price_cancel))
    app.add_handler(CommandHandler("priceremove", price_remove_start))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("pricetest", price_test_start))
    app.add_handler(CommandHandler("priceedit", price_edit_start))
    app.add_handler(CommandHandler("pricereport", price_report))
    app.add_handler(CommandHandler("wishlist", wishlist_command))
    app.add_handler(CommandHandler("print", print_command))
    app.add_handler(CallbackQueryHandler(impulse_callback, pattern="^impulse_"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_print_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # --- Schedule background jobs ---
    _schedule_sampling(app.job_queue)
    _schedule_morning_report(app.job_queue)
    _schedule_daily_report(app.job_queue)
    _schedule_price_watch(app.job_queue)
    _schedule_reminder_check(app.job_queue)
    app.job_queue.run_repeating(lens_expiry_job, interval=3600, first=30)

    log.info("🤖 Bot running — polling for updates…")
    app.run_polling()


if __name__ == "__main__":
    main()
