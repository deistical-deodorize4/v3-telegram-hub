#!/bin/bash
# Launcher for Writing Corrector v2 (Samuel style)
# Usage: ./run_corrector.sh <path-to-docx> [CEFR-level]

set -euo pipefail

# Determine project root (parent of this script's directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source environment variables (project .env or legacy ~/.env_keys)
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
elif [ -f ~/.env_keys ]; then
    source ~/.env_keys
fi

# Activate virtual environment if present
if [ -d "$PROJECT_ROOT/venv" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

FILE="${1:?Usage: $0 <path-to-docx> [CEFR-level]}"
LEVEL="${2:-B1}"

cd "$PROJECT_ROOT"

exec python3 writing_corrector/corrector_v2.py "$FILE" "$LEVEL"
