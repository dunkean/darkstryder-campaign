"""Bound host RAM by sequentially recycling the OCR worker at page checkpoints.

Native PDF/image libraries retain buffers beyond Python GC. A fresh worker every
96 committed pages releases them completely. No concurrent GPU workers or retries
after errors; stopping either supervisor or worker stops the job.
"""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import write_json

ROOT = Path(__file__).resolve().parents[2]


def run():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--limit-pages', type=int, default=0)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--worker-pages', type=int, default=96)
    args, forwarded = parser.parse_known_args()
    if args.limit_pages or '--help' in forwarded or '-h' in forwarded:
        sys.argv = [sys.argv[0], *forwarded, '--limit-pages', str(args.limit_pages)]
        if args.runtime_root:
            sys.argv += ['--runtime-root', str(args.runtime_root)]
        from docling_backend import main
        main()
        return 0
    if args.worker_pages < 1:
        parser.error('--worker-pages must be positive')
    config = json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
    runtime = args.runtime_root or Path(config['runtimeRoot'])
    runtime.mkdir(parents=True, exist_ok=True)
    job_lock = (runtime / 'ocr-job.lock').open('a')
    try:
        fcntl.flock(job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another OCR supervisor owns this output directory')
    job_lock.seek(0)
    job_lock.truncate()
    job_lock.write(str(os.getpid()))
    job_lock.flush()
    if args.runtime_root:
        forwarded += ['--runtime-root', str(args.runtime_root)]
    command = [sys.executable, '-u', str(ROOT / 'tools/extract/convert.py'),
               '--ocr-worker', *forwarded, '--limit-pages', str(args.worker_pages)]
    environment = os.environ.copy()
    environment['DARKSTRYDER_OCR_STARTED_AT'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    environment['DARKSTRYDER_OCR_SUPERVISOR_PID'] = str(os.getpid())
    worker = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if worker is not None and worker.poll() is None:
            worker.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        worker = subprocess.Popen(command, cwd=ROOT, env=environment)
        exit_code = worker.wait()
        status_file = runtime / 'ocr-status.json'
        status = json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
        if stopping:
            if status.get('state') not in ['complete', 'interrupted']:
                status.update(state='interrupted', error='Supervisor stopped',
                              updatedAt=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
                write_json(status_file, status)
            return 0
        if exit_code:
            if status.get('state') != 'failed':
                status.update(state='failed', error=f'OCR worker exited with code {exit_code}; see job log',
                              updatedAt=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
                runtime.mkdir(parents=True, exist_ok=True)
                write_json(status_file, status)
            return exit_code
        if stopping or status.get('state') in ['complete', 'failed', 'interrupted']:
            return 0 if status.get('state') != 'failed' else 1
        if not status.get('processedPagesThisRun', 0):
            # An intentionally partial page range has no remaining work.
            return 0
        print(f'Checkpoint: {status.get("completedPages")}/{status.get("totalPages")}; recycling worker to release host buffers', flush=True)
    return 0
