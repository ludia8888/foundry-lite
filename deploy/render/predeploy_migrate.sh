#!/bin/sh
set -eu

. /app/deploy/render/normalize_render_environment.sh

exec /opt/foundry-lite-venv/bin/python scripts/operations/run_migrations.py \
  --revision head \
  --lock-timeout-seconds 30 \
  --evidence-output /tmp/foundry-lite-migration-run.json
