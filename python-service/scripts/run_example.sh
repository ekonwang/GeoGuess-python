#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# activate the gpt-researcher conda environment
eval "$(conda shell.bash hook)"
conda activate gpt-researcher

# 设置 SSL 以及代理
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export ALL_PROXY=http://127.0.0.1:7890

# 在后台运行 app
export CITY_SMALL_CACHE_DIR="app/geojson_cache"
if ! lsof -i:8001 >/dev/null 2>&1; then
    python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 > app/latest.log 2>&1 &
    sleep 1
else
    echo "[INFO] Port 8001 is occupied, killing the process..." >&2
    lsof -ti:8001 | xargs -r kill -9
    sleep 1
    python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 > app/latest.log 2>&1 &
    sleep 1
fi

: "${APP_BASE_URL:=http://localhost:8001}"

if [[ -z "${GOOGLE_MAPS_API_KEY:-}" ]]; then
  echo "ERROR: GOOGLE_MAPS_API_KEY is not set. Export it or pass --google-api-key." >&2
  echo "Example: export GOOGLE_MAPS_API_KEY=YOUR_KEY" >&2
  exit 1
fi

# Forward any CLI args to the Python script
python "$SCRIPT_DIR/get_panorama.py" \
  --app-base-url "$APP_BASE_URL" \
  --google-api-key "$GOOGLE_MAPS_API_KEY" \
  --max-attempts 10 \
  --city "dubai" --batch_out_dir none \
  "$@" 