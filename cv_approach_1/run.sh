#!/usr/bin/env bash
# Start the Lithology Console web app on http://localhost:8000 (PORT=8001 ./run.sh for another port).
# Creates .venv with ./setup_venv.sh if it is missing; installs the web packages into .venv only.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "No .venv yet: running ./setup_venv.sh (Python packages + private Tesseract, no sudo)…"
  ./setup_venv.sh
fi
.venv/bin/python -m pip install -q -r requirements-web.txt
PORT="${PORT:-8000}"
echo "Lithology Console → http://localhost:${PORT}   (Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port "${PORT}"
