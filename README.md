## Install

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git ghostscript python3-venv
```

## Setup

```bash
git clone https://github.com/deistical-deodorize4/v3-telegram-hub.git
cd v3-telegram-hub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
.venv/bin/python telegram_bot/bot.py
```

Fill in your own values in `.env` — see the table below. The bot refuses to start until `TELEGRAM_BOT_TOKEN` is set.

| Env var | Description |
|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | AEMET OpenData key weather |
| `PRINTER_ADDR` | Oprinter IP |

## Auto-start on boot

The unit is a template with `__USER__` and `__REPO_PATH__` placeholders; `install.sh` fills them in for your system.

```bash
./install.sh
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```

`install.sh` generates `/etc/systemd/system/pi02w-hub.service` from the template and reloads systemd. Rerun it after moving the repo or changing the service template. Start it yourself so you can check `systemctl status pi02w-hub` and the logs first.
