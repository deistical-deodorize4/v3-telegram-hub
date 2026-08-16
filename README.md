## Install

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git ghostscript python3-venv python3-dev build-essential
```

## Setup

```bash
git clone https://github.com/deistical-deodorize4/v3-telegram-hub.git /home/pi/pi02w-hub
cd /home/pi/pi02w-hub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
.venv/bin/python telegram_bot/bot.py
```

Fill in the `.env`

| Env var | Description |
|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | AEMET OpenData key weather |
| `PRINTER_ADDR` | Oprinter IP |

## Auto-start on boot

```bash
sudo cp pi02w-hub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```

The service assumes the standard Pi layout: user `pi` and repo at `/home/pi/pi02w-hub` (as in Setup above). If your Pi uses a different username, edit `/etc/systemd/system/pi02w-hub.service` and change the `User`/`Group` and `/home/pi/pi02w-hub` paths.

Check it's running:

```bash
systemctl status pi02w-hub
```
