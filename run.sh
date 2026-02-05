#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GOOGLE_PLACES_API_KEY:-}" ]]; then
  echo "GOOGLE_PLACES_API_KEY is not set."
  echo "Set it with: export GOOGLE_PLACES_API_KEY=\"your_key_here\""
  exit 1
fi

python3 /Users/vilmasoini/Documents/WoM/wom_new_openings.py \
  --city Helsinki \
  --months 6 \
  --use-newer-proxy \
  --google-places \
  --reverse-geocode \
  --output /Users/vilmasoini/Documents/WoM/data/helsinki_openings.csv
