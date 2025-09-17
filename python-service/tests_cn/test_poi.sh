export ALL_PROXY=http://127.0.0.1:7890
export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"

rm -r ./sh_photos
python test_poi.py \
  --center "31.2304,121.4737" \
  --radius 10000 \
  --limit 100 \
  --maxwidth 8000 \
  --outdir "./sh_photos" --debug
