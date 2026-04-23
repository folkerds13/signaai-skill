#!/bin/bash
# SignaAI listener daemon — works on any machine with signaai-worker.json configured.
# Address is derived automatically from the worker passphrase.

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

exec python3 "$SKILL_DIR/scripts/listener.py" --network mainnet
