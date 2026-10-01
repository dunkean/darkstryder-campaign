"""Start a single detached local OCR worker; logs and PID stay in runtime."""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from common import write_json

ROOT = Path(__file__).resolve().parents[2]

if __name__ == '__main__':
    config = json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
    runtime = Path(config['runtimeRoot'])
    runtime.mkdir(parents=True, exist_ok=True)
    corpus = '--corpus' in sys.argv
    arguments = [arg for arg in sys.argv[1:] if arg != '--corpus']
    for filename in ['corpus-job.lock', 'ocr-job.lock', 'ocr.lock']:
        with (runtime / filename).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('OCR is already running; see runtime/ocr-status.json')
    logs = runtime / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    logfile = logs / f'ocr-docling-{time.strftime("%Y%m%d-%H%M%S")}.log'
    command = ['nice', '-n', '10', 'ionice', '-c', '2', '-n', '7',
               config.get('pythonExecutable', sys.executable), '-u',
               str(ROOT / ('tools/extract/corpus-job.py' if corpus else 'tools/extract/convert.py')), *arguments]
    environment = os.environ.copy()
    environment.update(OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', TOKENIZERS_PARALLELISM='false')
    with logfile.open('ab', buffering=0) as log:
        worker = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True, env=environment)
    write_json(runtime / ('corpus-job.json' if corpus else 'ocr-job.json'), {'pid': worker.pid, 'log': str(logfile),
               'startedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'command': command})
    print(f'OCR started: PID {worker.pid}; log {logfile}')
