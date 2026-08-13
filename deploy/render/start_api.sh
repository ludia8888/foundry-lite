#!/bin/sh
set -eu

. /app/deploy/render/normalize_render_environment.sh

case "${PORT:-10000}" in
  ''|*[!0-9]*)
    echo "PORT must be a positive integer" >&2
    exit 64
    ;;
esac

if [ "${PORT:-10000}" -lt 1 ] || [ "${PORT:-10000}" -gt 65535 ]; then
  echo "PORT must be between 1 and 65535" >&2
  exit 64
fi

exec /opt/foundry-lite-venv/bin/uvicorn foundry_lite_api.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --proxy-headers \
  --forwarded-allow-ips '*'
