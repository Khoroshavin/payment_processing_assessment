#!/bin/sh
# Outbox relay: publish outbox events to RabbitMQ.
set -eu

exec python -m app.entrypoints.relay
