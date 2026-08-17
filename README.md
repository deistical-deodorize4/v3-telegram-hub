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
| `MESH_NODE_ID` | Meshtastic solar node ID (e.g. `!dc3b65fb`) for mesh weather |
| `MESH_HOST` / `MESH_PORT` | BLE bridge TCP endpoint (default `127.0.0.1:4403`) |
| `MESH_MORNING_HOUR` / `MESH_MORNING_MINUTE` | Morning mesh brief time (default 09:00) |

## Mesh weather

The bot reads live telemetry (temperature, humidity, hPa, battery) from your
Meshtastic solar node through the BLE bridge. The docker stack publishes the
bridge's TCP stream on the Pi's loopback (`127.0.0.1:4403`) — set `MESH_NODE_ID`
to your node's ID. Tap `📡 Mesh` (or send `/mesh`) for a live reading; the bot
also pushes a brief every morning like the AEMET report.

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
