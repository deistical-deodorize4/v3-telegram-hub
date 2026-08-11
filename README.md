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

Fill in the `.env`

| Env var | Description |
|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | AEMET OpenData key weather |
| `PRINTER_ADDR` | Oprinter IP |

## Auto-start on boot

Replace the two `__USER__` and `__REPO_PATH__` placeholders in `pi02w-hub.service` with your username and the repo path, then:

```bash
sudo cp pi02w-hub.service /etc/systemd/system/
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```
