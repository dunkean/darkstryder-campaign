"""Run Chandra's vLLM backend locally, without Docker or a hosted API."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
os.environ.setdefault('HF_HOME', CONFIG.get('modelCacheRoot', str(Path.home() / '.cache/darkstryder/huggingface')))
os.environ.setdefault('OMP_NUM_THREADS', '4')

if __name__ == '__main__':
    command = [
        sys.executable, '-m', 'vllm.entrypoints.openai.api_server',
        '--model', 'datalab-to/chandra-ocr-2', '--served-model-name', 'chandra',
        '--host', '127.0.0.1', '--port', '8000', '--dtype', 'bfloat16',
        '--max-model-len', '18000', '--max-num-batched-tokens', '2048',
        '--max-num-seqs', '4', '--gpu-memory-utilization', '0.80',
        '--mm-processor-kwargs', json.dumps({'min_pixels': 3136, 'max_pixels': 6291456}),
    ]
    raise SystemExit(subprocess.call(command + sys.argv[1:]))
