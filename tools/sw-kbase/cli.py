"""Unified local SW D6 reference index. Each stage is explicit and resumable."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'packages'))

from sw_kbase.adapters import ingest_all
from sw_kbase.mining import mine, apply_decisions
from sw_kbase.pipeline import classify, compose, export, invalidate_decisions, utility_jobs, curate, continue_contract
from sw_kbase.store import Store, process_lock
from sw_kbase.wiring import wire
from sw_kbase.audit import audit
from sw_kbase.pilot import prepare as prepare_pilot, import_decisions


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['ingest', 'wire', 'mine', 'clean', 'pilot', 'import-decisions', 'classify', 'curate', 'curate-groups', 'plan-groups', 'pause', 'resume', 'resolve', 'utility', 'compose', 'serve', 'audit', 'status', 'search', 'inspect', 'record', 'export', 'split', 'undo', 'invalidate-decisions', 'continue-contract'])
    p.add_argument('--config', type=Path)
    p.add_argument('--previous-config', type=Path)
    p.add_argument('--reason')
    p.add_argument('--database', type=Path)
    p.add_argument('--selection-database', type=Path, help='Read-only saved paragraph selection for the review UI')
    p.add_argument('--external-root', type=Path)
    p.add_argument('--project', type=Path, default=PROJECT)
    p.add_argument('--adapters', help='Comma-separated: derived,foundry,sql,csv,lore,wiki,statbase,local,web')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--neighbors', type=int, default=12)
    p.add_argument('--mentions', type=int, default=24)
    p.add_argument('--scope', help='Restrict records by source/scope/body phrase, e.g. DarkStryder')
    p.add_argument('--tasks', help='Comma-separated semantic tasks')
    p.add_argument('--id')
    p.add_argument('--query')
    p.add_argument('--universe', default='all')
    p.add_argument('--kind')
    p.add_argument('--origin')
    p.add_argument('--importance')
    p.add_argument('--output', type=Path)
    p.add_argument('--input', type=Path)
    p.add_argument('--no-pages', action='store_true')
    p.add_argument('--no-annotations', action='store_true')
    p.add_argument('--reference-only', action='store_true', help='Export usable records/passages/links without the large curation audit streams')
    p.add_argument('--restart', action='store_true')
    p.add_argument('--retry-errors', action='store_true')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--continuous', action='store_true', help='Drain all OpenJev tasks; never starts Strata')
    p.add_argument('--work-batch', type=int, default=100)
    return p


def configuration(args):
    configured = args.config or next((path for path in (args.project / 'config' / 'sw-kbase.local.json', args.project / 'config' / 'phase4.local.json') if path.is_file()), None)
    config = json.loads(configured.read_text(encoding='utf-8-sig')) if configured else {}
    if args.database:
        config['database'] = str(args.database)
    if args.external_root:
        config['external_root'] = str(args.external_root)
    receipt = args.project / 'catalog' / 'sw-d6-research.local.json'
    if not config.get('external_root') and receipt.exists():
        config['external_root'] = json.loads(receipt.read_text(encoding='utf-8-sig'))['download_root']
    local = args.project / 'config.local.json'
    project_config = json.loads(local.read_text(encoding='utf-8-sig')) if local.exists() else {}
    runtime = Path(project_config.get('runtimeRoot', str(args.project.parent / 'darkstryder_runtime')))
    config.setdefault('database', str(runtime / 'sw-kbase' / 'knowledge.sqlite'))
    config.setdefault('external_root', str(runtime / 'external-data' / 'sw-d6-research-2026-10-21'))
    return config


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = parser().parse_args()
    if args.limit < 0:
        raise ValueError('Limit cannot be negative')
    args.project = args.project.resolve()
    config = configuration(args); database = Path(config['database']).resolve()
    if args.command in {'pause', 'resume'}:
        if not database.is_file():raise ValueError('Database absent')
        pause = database.parent / 'openjev.pause'
        if args.command == 'pause':pause.write_text('pause_requested\n', encoding='utf-8')
        else:pause.unlink(missing_ok=True)
        print(json.dumps({'pause_requested': pause.exists(), 'resume': 'curate --continuous resumes saved work'}))
        return
    if args.command == 'serve':
        from sw_kbase.service import serve
        serve(database, port=args.port, selection_database=args.selection_database)
        return
    read_only = args.command in {'status', 'search', 'inspect', 'record', 'export', 'audit', 'pilot', 'plan-groups'}
    if read_only and not database.is_file():
        raise ValueError('Database absent: run ingest first')
    import contextlib
    lock = contextlib.nullcontext() if read_only else process_lock(database.parent)
    with lock:
        store = Store(database, readonly=read_only)
        try:
            if args.command == 'ingest':
                result = ingest_all(store, args.project, config['external_root'], args.adapters.split(',') if args.adapters else None, not args.no_pages, args.limit)
                result['wiring'] = wire(store, config['external_root'])
            elif args.command == 'wire':
                result = wire(store, config['external_root'])
            elif args.command == 'mine':
                result = mine(store, args.limit, args.neighbors, args.mentions, args.scope, args.restart, not args.no_annotations)
            elif args.command == 'clean':
                from sw_kbase.cleaning import clean
                import os
                def progress(stage, **counts):
                    value = {'stage': stage, **counts, 'model_calls': 0}
                    target = database.parent / 'cleaning-progress.json'
                    temporary = target.with_suffix('.json.tmp')
                    with temporary.open('w', encoding='utf-8') as stream:
                        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
                        stream.flush();os.fsync(stream.fileno())
                    temporary.replace(target)
                    print(json.dumps({'stage':stage,**{k:v for k,v in counts.items() if k not in {'before','after','changes'}}},ensure_ascii=False),flush=True)
                result = clean(store, progress)
                if args.output:
                    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            elif args.command == 'pilot':
                if not args.output:raise ValueError('--output required (new pilot directory)')
                result = prepare_pilot(store, args.output, args.limit or 100, args.tasks.split(',') if args.tasks else None, args.scope)
            elif args.command == 'import-decisions':
                if not args.input:raise ValueError('--input required (pilot SQLite)')
                result = import_decisions(store, args.input, config)
            elif args.command == 'classify':
                result = classify(store, config, args.limit, args.tasks.split(',') if args.tasks else None, args.scope, args.retry_errors)
            elif args.command == 'curate':
                result = curate(store, config, args.limit, args.tasks.split(',') if args.tasks else None,
                                args.scope, args.retry_errors, args.continuous, args.work_batch)
            elif args.command == 'plan-groups':
                from sw_kbase.grouping import analyze_groups
                result = analyze_groups(store, scope=args.scope, limit=args.limit, tasks=args.tasks.split(',') if args.tasks else None)
            elif args.command == 'curate-groups':
                from sw_kbase.grouped_pipeline import curate_groups
                result = curate_groups(store, config, limit=0 if args.continuous else args.limit or 100, scope=args.scope,
                                       tasks=args.tasks.split(',') if args.tasks else None)
            elif args.command == 'resolve':
                result = apply_decisions(store)
            elif args.command == 'utility':
                result = {'utility_jobs_considered': utility_jobs(store)}
            elif args.command == 'compose':
                result = compose(store, config, args.limit, args.id)
            elif args.command == 'status':
                result = store.status()
            elif args.command == 'audit':
                result = audit(store)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    with args.output.open('x', encoding='utf-8') as report:
                        report.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            elif args.command == 'search':
                if not args.query:raise ValueError('--query required')
                result = store.search(args.query, args.limit or 20, args.kind, args.universe, args.origin, args.importance)
            elif args.command in {'inspect', 'record'}:
                if not args.id:raise ValueError('--id required')
                result = store.entity(args.id) if args.command == 'inspect' else store.record(args.id)
            elif args.command == 'export':
                if not args.output:raise ValueError('--output required (new directory)')
                result = export(store, args.output, args.reference_only)
            elif args.command == 'split':
                if not args.id:raise ValueError('--id record required')
                result = {'provisional_entity_id': store.detach(args.id)};store.db.commit()
            elif args.command == 'undo':
                if not args.id:raise ValueError('--id event number required')
                store.undo(int(args.id));store.db.commit();result = {'undone': int(args.id)}
            elif args.command == 'continue-contract':
                if not args.previous_config:raise ValueError('--previous-config required')
                previous_config = json.loads(args.previous_config.read_text(encoding='utf-8-sig'))
                result = continue_contract(store, previous_config, config, args.reason)
            else:
                result = invalidate_decisions(store)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
