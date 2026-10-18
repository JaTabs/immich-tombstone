#!/bin/sh
# Entrada compatible con instalaciones anteriores; la lógica usa solo Python.
set -eu
exec python3 -I "$(dirname "$0")/install.py" "$@"
