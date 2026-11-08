#!/usr/bin/env bash
# One-time setup of "Automatic bounding boxes" (method automatic_lithology_cv): creates bbox-final/.venv with the
# detector's packages (CPU PyTorch, ultralytics, OpenVINO, libvips). Separate from .venv so they never clash with the
# app's packages; nothing is installed system-wide. Takes a few minutes and ~1.5 GB.
set -euo pipefail
cd "$(dirname "$0")/../bbox-final"
PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/python -m pip install -r requirements-server.txt
fi
.venv/bin/python -c "import torch, ultralytics, openvino; print('automatic bounding boxes ready')"
