#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Activate venv if available
if [[ -f "$PROJECT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$PROJECT_DIR/.venv/bin/activate"
fi

: "${APP_BASE_URL:=http://localhost:8001}"

if [[ -z "${GOOGLE_MAPS_API_KEY:-}" ]]; then
  echo "ERROR: GOOGLE_MAPS_API_KEY is not set. Export it or pass --google-api-key." >&2
  echo "Example: export GOOGLE_MAPS_API_KEY=YOUR_KEY" >&2
  exit 1
fi

# Forward any CLI args to the Python script
exec python3 "$SCRIPT_DIR/get_panorama.py" \
  --app-base-url "$APP_BASE_URL" \
  --google-api-key "$GOOGLE_MAPS_API_KEY" \
  --max-attempts 10 \
  --city "New York" \
  "$@" 