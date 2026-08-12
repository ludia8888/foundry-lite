#!/bin/sh

# Render's managed Postgres connectionString uses postgresql:// while this
# image intentionally installs psycopg v3 (SQLAlchemy driver name: psycopg).
# Rewrite only the scheme in-memory; never print the credential-bearing URL.
case "${FOUNDRY_LITE_DB_URL:-}" in
  postgresql://*)
    FOUNDRY_LITE_DB_URL="postgresql+psycopg://${FOUNDRY_LITE_DB_URL#postgresql://}"
    export FOUNDRY_LITE_DB_URL
    ;;
esac
