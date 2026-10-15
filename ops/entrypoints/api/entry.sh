#!/bin/sh
# API: serve HTTP. Migrations are applied by the one-shot `migrate` compose
# service before this container starts.
set -eu

exec python -m app.entrypoints.api
