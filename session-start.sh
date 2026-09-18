#!/usr/bin/env bash
# Backward-compatible entry point. New installations call the Python hook directly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/superpowers-bootstrap.py" "$@"
