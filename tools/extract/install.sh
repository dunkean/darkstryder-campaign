#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
configured_python="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8-sig")).get("pythonExecutable", ""))' "$project_root/config.local.json")"
if [[ -x "$configured_python" ]] && "$configured_python" -c 'import torch,docling,rapidocr; assert torch.cuda.is_available(); print("Existing local OCR stack:", torch.__version__, torch.cuda.get_device_name(0))'; then
  exit 0
fi
ocr_venv="${DARKSTRYDER_OCR_VENV:-$project_root/.venv}"
if [[ ! -x "$ocr_venv/bin/python" ]]; then python3 -m venv "$ocr_venv"; fi
"$ocr_venv/bin/python" -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu130
"$ocr_venv/bin/python" -m pip install -r "$project_root/tools/extract/requirements.txt"
"$ocr_venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "CUDA unavailable"; print(torch.cuda.get_device_name(0))'
