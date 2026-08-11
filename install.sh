#!/usr/bin/env bash
# Installs the pi02w-hub systemd service for the current user and repo path.
set -euo pipefail

REPO_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"

SERVICE_FILE="${REPO_PATH}/pi02w-hub.service"

if [[ ! -f "${SERVICE_FILE}" ]]; then
    echo "error: ${SERVICE_FILE} not found — run this script from the repo root" >&2
    exit 1
fi

if [[ ! -x "${REPO_PATH}/.venv/bin/python" ]]; then
    echo "error: ${REPO_PATH}/.venv not found — run 'python3 -m venv .venv' first" >&2
    exit 1
fi

if [[ ! -f "${REPO_PATH}/.env" ]]; then
    echo "error: ${REPO_PATH}/.env not found — run 'cp .env.example .env' and fill it in first" >&2
    exit 1
fi

if grep -qE "your_bot_token_here|your_telegram_numeric_user_id|your_aemet_key_here" "${REPO_PATH}/.env"; then
    echo "warning: .env still contains placeholder values — the bot will not work." >&2
    echo "         edit ${REPO_PATH}/.env first, then rerun ./install.sh" >&2
    exit 1
fi

sed -e "s|__USER__|${USER_NAME}|g" \
    -e "s|__REPO_PATH__|${REPO_PATH}|g" \
    "${SERVICE_FILE}" | sudo tee /etc/systemd/system/pi02w-hub.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable pi02w-hub
sudo systemctl start pi02w-hub

echo "pi02w-hub service installed and started."
