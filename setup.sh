#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
"$PY" -m venv "$HERE/venv"
"$HERE/venv/bin/python" -m pip install -r "$HERE/requirements.txt"
"$HERE/venv/bin/python" "$HERE/source/app.py" catalog sync
printf '\nInstalled MCP code. To download ALL docsets, run:\n  "%s/venv/bin/python" "%s/source/app.py" download --all --workers 2 --delay 2\n' "$HERE" "$HERE"
if [[ "${1:-}" == "--register-pi" ]]; then
  if command -v pi >/dev/null 2>&1; then
    pi mcp add docsets -- "$HERE/venv/bin/python" "$HERE/source/app.py" serve
    pi mcp list
  else
    echo 'Pi CLI not found; configure your MCP host manually.' >&2
  fi
fi
