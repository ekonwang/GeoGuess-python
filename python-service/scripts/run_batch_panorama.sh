#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MAIN_PROJECT_TEMP=$(cd $PROJECT_DIR/../../../.temp && pwd)
echo ${MAIN_PROJECT_TEMP}

# 设置环境变量
set -a; source ${MAIN_PROJECT_TEMP}/../.env; set +a;

# activate the gpt-researcher conda environment
eval "$(conda shell.bash hook)" || true
conda activate gpt-researcher || true
cd ${PROJECT_DIR}

# 设置 SSL 以及代理
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
if [[ -n "${https_proxy:-}" || -n "${http_proxy:-}" ]]; then
    echo "Using existing proxy settings:"
    echo "http_proxy=${http_proxy:-<not set>}"
    echo "https_proxy=${https_proxy:-<not set>}"
else
    export ALL_PROXY=http://127.0.0.1:7890
    echo "Using proxy: http://127.0.0.1:7890"
fi

# 检查 Google Maps API Key 是否设置
if [[ -z "${GOOGLE_MAPS_API_KEY:-}" ]]; then
  echo "ERROR: GOOGLE_MAPS_API_KEY is not set. Export it or pass --google-api-key." >&2
  echo "Example: export GOOGLE_MAPS_API_KEY=YOUR_KEY" >&2
  exit 1
fi


test_google_api() {
    echo "🔍 Testing Google API connectivity..."
    
    # Run curl with 3 second timeout and capture output
    local result
    result=$(curl -v https://www.googleapis.com 2>&1)
    local exit_code=$?
    
    # Check if command completed successfully and has the expected end message
    if [ $exit_code -eq 0 ] && echo "$result" | grep -q "Connection #0 to host.*left intact"; then
        echo -e "\033[1;34m✅ Network Test PASSED - Google API is reachable and responding correctly\033[0m"
        return 0
    else
        echo -e "\033[1;31m❌ Network Test FAILED - Unable to connect to Google API within 3 seconds\033[0m"
        echo -e "\033[1;31m💡 Please check your proxy settings or network connection\033[0m"
        return 1
    fi
}
# Run the network test
test_google_api


# 在后台运行 app
export CITY_SMALL_CACHE_DIR="app/geojson_cache"
if ! lsof -i:8001 >/dev/null 2>&1; then
    python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 > app/latest.log 2>&1 &
    sleep 1
else
    echo "[INFO] Port 8001 is occupied, killing the process..." >&2
    lsof -ti:8001 | xargs -r kill -9
    sleep 1
    python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 > app/latest.log 2>&1 &
    sleep 1
fi

: "${APP_BASE_URL:=http://localhost:8001}"


python3 "$SCRIPT_DIR/batch_panorama_1012.py" \
  --app-base-url "$APP_BASE_URL" \
  --google-api-key "$GOOGLE_MAPS_API_KEY" \
  --max-attempts 10 \
  --num_query 500 \
  --batch_out_dir ${MAIN_PROJECT_TEMP}/datasets/google_javascript_maps_diverse_1012 --concurrency 3
  "$@" 
