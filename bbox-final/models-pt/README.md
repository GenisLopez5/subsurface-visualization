# Weights for an NVIDIA GPU

The int8 OpenVINO models in `../models` run only on a CPU or an Intel GPU. To use an NVIDIA GPU, copy the
original trained weights here, named:

- `general.pt`
- `legend.pt`

Their sha256 must match `manifest.json` (copy them as binary files). If CUDA PyTorch is installed
(`run_server.bat` installs it when `nvidia-smi` works), `server.py` uses them on GPU 0. Otherwise it falls back
to OpenVINO on an Intel GPU, then on the CPU.
Check what it picked with `curl http://<host>:8100/health` (`"device"`).
