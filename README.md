# pi02w Hub

Telegram bot for a Raspberry Pi Zero 2W. Weather, chatbot, system monitor, study & finance logging.

## Quick start

```bash
git clone <repo-url> && cd pi02w-hub
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python3 telegram_bot/bot.py
```

## Service (auto-start on boot)

```bash
sudo cp pi02w-hub.service /etc/systemd/system/
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub
```

## Required env vars

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `GEMINI_API_KEY` | Google AI Studio key |
| `TELEGRAM_USER_ID` | Your Telegram user ID |
| `AEMET_API_KEY` | (optional) AEMET OpenData key for Spanish weather |

## Commands

- `/start` — show menu
- `/daily` — pull today's hardware report
- Weather, Chatbot, Study Log, Finance Log buttons in the menu
