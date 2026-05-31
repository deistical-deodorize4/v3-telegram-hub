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

import requests
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path (for direct `python bot.py` invocation)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config as cfg
from system_monitor import monitor as sysmon
from weather_forecaster import weather_aemet
from utils import log, setup_logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
setup_logging(logging.WARNING)

# ---------------------------------------------------------------------------
# Credentials & guards
# ---------------------------------------------------------------------------
TOKEN = cfg.TELEGRAM_BOT_TOKEN
GEMINI_KEY = cfg.GEMINI_API_KEY
ALLOWED_USER = cfg.TELEGRAM_USER_ID

if not TOKEN:
    log.error("TELEGRAM_BOT_TOKEN not set — bot cannot start.")
    raise SystemExit(1)
if not GEMINI_KEY:
    log.error("GEMINI_API_KEY not set — chatbot will fail.")
    raise SystemExit(1)

client = genai.Client(api_key=GEMINI_KEY)

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

        # ---- Weather health checks ----
        om_status = _check_open_meteo()
        aemet_status = _check_aemet()

        # ---- Build report ----
        lines = []
        header = f"📊 Pi Daily Report — {day}"
        lines.append(header)
        lines.append("━" * len(header))
        lines.append("")

        # CPU line
        cpu_line = f"🌡 CPU · avg {cpu_avg:.0f}% · max {cpu_max_s['cpu']:.0f}% at {cpu_max_s['time_str']} · now {cpu_now:.0f}%"
        lines.append(cpu_line)

        # RAM line
        ram_free_pct = (ram_free_now / ram_total * 100) if ram_total > 0 else 0
        ram_alert = "  ⚠️" if ram_free_now < 150 else ""
        ram_line = (
            f"💾 RAM · avg {ram_avg:.0f}MB · min free {ram_min_free_s['ram_available_mb']:.0f}MB "
            f"at {ram_min_free_s['time_str']} · now {ram_free_now:.0f}MB ({ram_free_pct:.0f}% free){ram_alert}"
        )
        lines.append(ram_line)

        # Temp line
        if temp_avg is not None and temp_max_s is not None:
            temp_alert = "  ⚠️" if temp_max_s["temp"] and temp_max_s["temp"] > 65 else ""
        else:
            temp_alert = ""
        if temp_avg is not None and temp_max_s is not None:
            temp_now_str = f"{temp_now:.1f}°C" if temp_now is not None else "N/A"
            temp_line = (
                f"🔥 Temp · avg {temp_avg:.1f}°C · max {temp_max_s['temp']:.1f}°C "
                f"at {temp_max_s['time_str']} · now {temp_now_str}{temp_alert}"
            )
        else:
            temp_line = "🔥 Temp · N/A (sensor unavailable)"
        lines.append(temp_line)

        # Disk line
        disk_alert = "  ⚠️" if disk_pct > 75 else ""
        lines.append(f"💽 Disk · {disk_free:.1f}GB free ({disk_pct:.0f}%){disk_alert}")

        # Load average line
        load_1m_vals = [s["load_1m"] for s in samples if s["load_1m"] is not None]
        if load_1m_vals:
            load_1m_avg = sum(load_1m_vals) / len(load_1m_vals)
            load_1m_max = max(load_1m_vals)
            cores = samples[-1].get("load_cores", 4) or 4
            load_alert = "  ⚠️" if load_1m_max > cores else ""
            lines.append(
                f"📊 Load · avg {load_1m_avg:.2f} · max {load_1m_max:.2f} "
                f"({cores} cores){load_alert}"
            )

        # SD wear
        sd_wear = _read_sd_wear()
        if sd_wear:
            label = "lifetime" if sd_wear["type"] == "lifetime" else "since boot"
            lines.append(f"💾 SD Wear · {sd_wear['total_gb']}GB ({label})")

        # Weather
        lines.append(f"🌤 Weather APIs · Open-Meteo {om_status} · AEMET {aemet_status}")

        # ---- Alerts section ----
        alerts = []
        if cpu_avg > 80:
            alerts.append(
                f"• CPU avg at {cpu_avg:.0f}% (> 80% threshold)"
            )
        if ram_free_now < 150:
            alerts.append(
                f"• RAM free critically low: {ram_free_now:.0f}MB (< 150MB) "
                f"at {ram_min_free_s['time_str']}"
            )
        if load_1m_vals and load_1m_max > cores:
            alerts.append(
                f"• Load peaked at {load_1m_max:.2f} (saturated, ≥ {cores} cores)"
            )
        if temp_max_s and temp_max_s["temp"] and temp_max_s["temp"] > 65:
            alerts.append(
                f"• Temp peaked at {temp_max_s['temp']:.1f}°C (> 65°C threshold)"
            )
        if disk_pct > 75:
            alerts.append(f"• Disk usage at {disk_pct:.0f}% (> 75% threshold)")

        if throttled_events:
            alerts.append(f"• {len(throttled_events)} throttling event(s) detected")

        if alerts:
            lines.append("")
            lines.append("⚠️ ALERTS")
            lines.extend(alerts)

        # ---- Throttling status ----
        lines.append("")
        lines.append("✅ No throttling events" if not throttled_events else "⚠️ Throttling occurred today")

        # ---- Sample count & footer ----
        lines.append("")
        lines.append(f"_{n} samples collected_")
        lines.append("━" * len(header))

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


def _check_open_meteo() -> str:
    """Return ✅, ❌, or ⏭️ for Open-Meteo."""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": cfg.WEATHER_LAT,
            "longitude": cfg.WEATHER_LON,
            "hourly": "temperature_2m",
            "forecast_days": 1,
            "timezone": "Europe/Madrid",
        }
        resp = requests.get(url, params=params, timeout=10)
        return "✅" if resp.status_code == 200 else "❌"
    except Exception:
        return "❌"


def _check_aemet() -> str:
    """Return ✅, ❌, or ⏭️ for AEMET (skipped if no key)."""
    if not cfg.AEMET_API_KEY:
        return "⏭️"
    try:
        # Request observation data for Zaragoza Aeropuerto station
        url = (
            f"https://opendata.aemet.es/opendata/api/observacion/convencional"
            f"/datos/estacion/{cfg.AEMET_STATION}"
        )
        resp = requests.get(url, params={"api_key": cfg.AEMET_API_KEY}, timeout=10)
        return "✅" if resp.status_code == 200 else "❌"
    except Exception:
        return "❌"


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
    report = weather_aemet.format_morning_report()
    if report:
        await context.bot.send_message(
            chat_id=ALLOWED_USER, text=report, parse_mode="Markdown"
        )
    else:
        await context.bot.send_message(
            chat_id=ALLOWED_USER,
            text="☀️ Buenos días — AEMET data unavailable this morning.",
        )
    # Re-schedule for tomorrow
    _schedule_morning_report(context.job_queue)


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build and send the daily tl;dr, then re-schedule for tomorrow."""
    report = daily_stats.build_report()
    if report:
        await context.bot.send_message(
            chat_id=ALLOWED_USER, text=report, parse_mode="Markdown"
        )
    else:
        await context.bot.send_message(
            chat_id=ALLOWED_USER,
            text="📊 Pi Daily Report — no data collected today (bot just started).",
        )
    # Re-schedule next day's report
    _schedule_daily_report(context.job_queue)


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
        icon = "⚠️"
        note = "Posible reinicio/corte de luz"
    else:
        icon = "ℹ️"
        note = "Bot reiniciado (soft)"

    msg = (
        f"{icon} *pi02w Hub*\n"
        f"🕐 {now}\n"
        f"⏱ uptime: {uptime_h:.1f}h  ·  {note}"
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
    ("unit", "📖 Unit studied (1-69)?"),
    ("hours", "⏱ Hours studied (e.g. 1.5)?"),
    ("energy", "⚡ Energy level before studying (1-10)?"),
    ("sleep", "😴 Hours of sleep last night?"),
    ("grade", "🎯 Grade received? (send . if none yet)"),
    ("rating", "⭐ Session quality rating (1-10)?"),
]

FINANCE_STEPS = [
    ("type", "💳 Type? (fixed / variable)"),
    ("category", "🏷 Category? (e.g. food, transport, salary)"),
    ("amount", "💶 Amount? (+ for income, - for expense. e.g. -12.50)"),
    ("description", "📝 Description?"),
]

# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🌤 Weather", "🤖 Chatbot"],
        ["📚 Study Log", "💰 Finance Log"],
        ["🖥 Monitor"],
        ["🚪 Menu"],
    ],
    resize_keyboard=True,
)

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
BASE_PROMPT = """You are a helpful personal assistant running on a Raspberry Pi Zero 2.
You are concise, friendly, and precise.
When asked technical questions, especially about Python, ML, or Raspberry Pi,
prioritize practical and lightweight solutions."""


def _get_prompt() -> str:
    today = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    return f"{BASE_PROMPT}\nCurrent date and time: {today}."


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
        await update.message.reply_text("⛔ Unauthorized.")
        return
    _get_session(user_id)["mode"] = "menu"
    await update.message.reply_text(
        "🤖 *pi02w Hub*\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull the current day's report on demand."""
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("⛔ Unauthorized.")
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
            await update.message.reply_text("📊 No data yet — try again in a minute.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER:
        await update.message.reply_text("⛔ Unauthorized.")
        return

    text: str = update.message.text  # type: ignore[assignment]
    session = _get_session(user_id)

    # ------- Menu / Start -------
    if text in ("🚪 Menu", "/start"):
        session["mode"] = "menu"
        session["history"] = []
        session["form"] = {}
        await update.message.reply_text(
"🤖 *pi02w Hub*\nSelect an option:",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        return

    # ------- Weather (AEMET) -------
    if text == "🌤 Weather":
        await update.message.reply_text("⏳ Fetching AEMET data…")
        try:
            report = weather_aemet.format_ondemand()
            if report:
                await update.message.reply_text(report, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "❌ Could not fetch AEMET data. Check your API key and internet."
                )
        except Exception as exc:
            await update.message.reply_text(f"❌ AEMET error: {exc}")
        return

    # ------- Chatbot -------
    if text == "🤖 Chatbot":
        session["mode"] = "chatbot"
        session["history"] = []
        await update.message.reply_text(
            "💬 Chatbot active. Type your message.\nSend *🚪 Menu* to exit.",
            parse_mode="Markdown",
        )
        return

    if session["mode"] == "chatbot":
        session["history"].append({"role": "user", "parts": [{"text": text}]})

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=cfg.GEMINI_MODEL,
                    config=types.GenerateContentConfig(
                        system_instruction=_get_prompt(),
                        max_output_tokens=300,
                    ),
                    contents=session["history"][-cfg.CHAT_HISTORY_CAP:],
                )
                break
            except Exception as exc:
                if "503" in str(exc) and attempt < 2:
                    time.sleep(2)
                else:
                    await update.message.reply_text(f"Error: {exc}")
                    return

        reply = response.text or "No response"
        session["history"].append({"role": "model", "parts": [{"text": reply}]})

        # Cap history to prevent memory bloat on Pi Zero 2W
        session["history"] = session["history"][-cfg.CHAT_HISTORY_CAP * 2 :]

        await update.message.reply_text(reply)
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
                f"⚠️ Invalid value. Try again.\n{STUDY_STEPS[step_index][1]}"
            )
            return

        session["form"][key] = val

        if len(session["form"]) < len(STUDY_STEPS):
            await update.message.reply_text(STUDY_STEPS[len(session["form"])][1])
        else:
            f = session["form"]
            today = datetime.now().strftime("%Y-%m-%d")
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
                f"✅ Logged: Unit {f['unit']} | {f['hours']}h | Rating {f['rating']}/10",
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
                f"⚠️ Invalid value. Try again.\n{FINANCE_STEPS[step_index][1]}"
            )
            return

        session["form"][key] = val

        if len(session["form"]) < len(FINANCE_STEPS):
            await update.message.reply_text(FINANCE_STEPS[len(session["form"])][1])
        else:
            f = session["form"]
            today = datetime.now().strftime("%Y-%m-%d")
            file_exists = cfg.FINANCE_LOG.exists()
            with cfg.FINANCE_LOG.open("a", newline="") as csvfile:
                writer = csv.writer(csvfile)
                if not file_exists:
                    writer.writerow(["date", "type", "category", "amount", "description"])
                writer.writerow([today, f["type"], f["category"], f["amount"], f["description"]])
            session["mode"] = "menu"
            session["form"] = {}
            await update.message.reply_text(
                f"✅ Logged: {f['category']} | {float(f['amount']):.2f}€ | {f['type']}",
                reply_markup=MENU_KEYBOARD,
            )
        return

    # ------- Monitor -------
    if text in ("🖥 Monitor", "/monitor"):
        try:
            report = sysmon.get_report()
            await update.message.reply_text(f"```{report}```", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"Monitor error: {exc}")
        return

    # ------- Default -------
    await update.message.reply_text("Select an option.", reply_markup=MENU_KEYBOARD)


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # --- Schedule background jobs ---
    _schedule_sampling(app.job_queue)
    _schedule_morning_report(app.job_queue)
    _schedule_daily_report(app.job_queue)

    log.info("🤖 Bot running — polling for updates…")
    app.run_polling()


if __name__ == "__main__":
    main()
