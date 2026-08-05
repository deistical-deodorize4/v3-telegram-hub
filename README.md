# pi02w Hub

Telegram bot for a Raspberry Pi Zero 2W. Weather, system monitor, study & finance
logging, price watch, reminders, lenses, and PDF printing.

## System dependencies

```bash
sudo apt install ghostscript   # required to print PDFs (PDF → PCL rendering)
```

Python packages (installed for the system Python — the systemd service uses
`/usr/bin/python3`):

```bash
sudo apt install python3-requests python3-psutil
python3 -m pip install --break-system-packages \
    "python-telegram-bot[job-queue]" python-dotenv joblib numpy
# or simply:
python3 -m pip install --break-system-packages -r requirements.txt
```

> On Pi OS (Debian Trixie) `pip install` system-wide requires
> `--break-system-packages`; `requirements-training.txt` (tensorflow, etc.)
> is **not** needed on the Pi.

## Quick start

```bash
git clone <repo-url> telegram-bot && cd telegram-bot
cp .env.example .env   # fill in your keys
python3 telegram_bot/bot.py
```

## Service (auto-start on boot)

The unit assumes the repo lives at `/home/pi/telegram-bot`. Adjust paths if
yours differs.

```bash
sudo cp pi02w-hub.service /etc/systemd/system/
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```

The service sets `TZ=Europe/Madrid`, which reminders and the weather brief
depend on (they use naive `datetime.now()`).

## Required env vars

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | (optional) AEMET OpenData key for Spanish weather |
| `PRINTER_ADDR` | (optional) Printer IP for the 🖨 Print feature |

## Commands

- `/start` — show menu
- `/daily` — pull today's hardware report
- Weather, Print, Finance Log, Study Log, Reminder, Lenses buttons in the menu
