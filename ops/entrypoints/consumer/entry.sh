#!/bin/sh
# Consumer: process payments.new messages.
set -eu

exec python -m app.entrypoints.consumer
