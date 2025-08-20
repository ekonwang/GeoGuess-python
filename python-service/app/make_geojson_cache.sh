#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MAIN_PROJECT_TEMP=$(cd $PROJECT_DIR/../../../.temp && pwd)
# echo ${MAIN_PROJECT_TEMP}

# activate the gpt-researcher conda environment
eval "$(conda shell.bash hook)"
conda activate gpt-researcher
cd ${PROJECT_DIR}

# 设置 SSL 以及代理
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export ALL_PROXY=http://127.0.0.1:7890

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


cd $(dirname $0)/..

# Use -m to run within package context so relative imports work
python -m app.make_geojson_cache --cache-dir app/geojson_cache --concurrency 1