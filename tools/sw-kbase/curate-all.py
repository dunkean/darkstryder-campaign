"""Drain OpenJev queues with campaign priority; never starts Strata or a model server."""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from packages.sw_kbase.store import process_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--priority-scope', default='DarkStryder')
    parser.add_argument('--work-batch', type=int, default=32)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    runtime = Path(config['database']).resolve().parent
    stages = [
        ('campaign_identities', ['--tasks', 'identity', '--scope', args.priority_scope]),
        ('campaign_classes', ['--tasks', 'type,importance,universe', '--scope', args.priority_scope]),
        ('remaining_identities', ['--tasks', 'identity']),
        ('campaign_relations', ['--scope', args.priority_scope]),
        ('remaining_all', []),
    ]
    receipt = {'writer_started': False, 'priority_scope': args.priority_scope,
               'config': str(config_path), 'completed_stages': []}
    def report(state, stage):
        receipt.update(state=state, stage=stage, updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        temporary = runtime / 'curation-plan.json.tmp'
        temporary.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        temporary.replace(runtime / 'curation-plan.json')
    # Separate orchestration lock: each child also takes the normal SQLite writer lock.
    with process_lock(runtime / 'orchestration'):
        for stage, options in stages:
            report('running', stage)
            print('OPENJEV_STAGE ' + stage, flush=True)
            command = [sys.executable, str(PROJECT / 'tools/sw-kbase/cli.py'), 'curate',
                       '--config', str(config_path), '--continuous', '--retry-errors', '--work-batch', str(args.work_batch), *options]
            completed = subprocess.run(command, cwd=PROJECT)
            if completed.returncode:
                report('error', stage)
                return completed.returncode
            progress = json.loads((runtime / 'openjev-progress.json').read_text(encoding='utf-8'))
            if progress['state'] != 'complete':
                report(progress['state'], stage)
                return 0
            receipt['completed_stages'].append(stage)
        report('complete', 'remaining_all')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
