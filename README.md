## Install

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install pip git ghostscript python3-pillow    
python3 -m pip install --break-system-packages -r requirements.txt
```

## Setup

```bash
git clone <https://github.com/deistical-deodorize4/v3-telegram-hub> pi02w-hub && cd pi02w-hub
cp .env.example .env 
python3 telegram_bot/bot.py
```

| Env var | Description |
|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | AEMET OpenData key weather |
| `PRINTER_ADDR` | Oprinter IP |

## Auto-start on boot

The unit expects the repo at `/home/pi/pi02w-hub` (edit it if yours differs).

```bash
sudo cp pi02w-hub.service /etc/systemd/system/
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```
