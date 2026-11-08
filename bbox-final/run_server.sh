#!/usr/bin/env bash
# Start the bounding-box service on this machine. First run creates .venv and installs everything.
#   ./run_server.sh                 # port 8100, reachable from other machines
#   PORT=9000 ./run_server.sh
# Then on the app machine:  LITHO_BBOX_URL=http://<this machine's IP>:8100
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
  echo "creating .venv and installing dependencies (one time, a few minutes)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/python -m pip install -r requirements-server.txt
fi

echo "this machine's addresses: $(hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || echo '?')"
exec .venv/bin/python server.py --host "${HOST:-0.0.0.0}" --port "${PORT:-8100}" "$@"
