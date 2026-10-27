"""Prepare all-source enrichment; score and local writer are separate explicit actions."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))
from sw_kbase import enrichment
from sw_kbase import grouped_paragraphs
from sw_kbase.native_jev import NativeJevClient
from sw_kbase.rich_merge import RichWriter, merge, export
from sw_kbase.store import Store, process_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan'); p.add_argument('--config', type=Path)
    p = sub.add_parser('score'); p.add_argument('--execute', action='store_true')
    p.add_argument('--jev-runtime', type=Path, required=True); p.add_argument('--limit', type=int, default=0)
    p = sub.add_parser('merge'); p.add_argument('--execute', action='store_true')
    p.add_argument('--config', type=Path, required=True); p.add_argument('--limit', type=int, default=0)
    p = sub.add_parser('export'); p.add_argument('--destination', type=Path, required=True)
    sub.add_parser('status')
    args = parser.parse_args()
    if args.command in ('score', 'merge'):
        if not args.execute:
            parser.error('Inference is disabled without the separate --execute flag')
        if (args.database.parent / 'openjev.pause').exists():
            parser.error('User pause active; no model is loaded and no inference is started')
        if args.limit < 0:
            parser.error('Limit must be nonnegative')
    root = Path(__file__).resolve().parents[2]
    for path in [args.output] + ([args.destination] if args.command == 'export' else []):
        if path.resolve().is_relative_to(root):
            parser.error('Runtime outputs must live outside the repository')
    main_store = Store(args.database, readonly=True)
    try:
        if args.command == 'plan':
            config = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
            result = grouped_paragraphs.plan(main_store, args.output, config=config)
        elif args.command == 'status':
            db = enrichment.connect(args.output)
            try:
                result = {'settings': enrichment.meta(db),
                    'entities': [dict(r) for r in db.execute('SELECT status,count(*) count FROM entities GROUP BY status')],
                    'paragraphs': [dict(r) for r in db.execute('SELECT status,count(*) count FROM paragraphs GROUP BY status')],
                    'rich_entities': db.execute('SELECT count(*) FROM rich_entities').fetchone()[0]}
            finally:
                db.close()
        elif args.command == 'export':
            with process_lock(args.output):
                result = export(main_store, args.output, args.destination)
        else:
            with process_lock(args.output):
                if args.command == 'score':
                    db = enrichment.connect(args.output)
                    try:
                        grouped = enrichment.meta(db)['policy'] == grouped_paragraphs.VERSION
                    finally:
                        db.close()
                    if grouped:
                        from sw_kbase.joint_jev import JointJevClient
                        client = JointJevClient.from_runtime(args.jev_runtime)
                        result = grouped_paragraphs.score(main_store, args.output, client, limit=args.limit)
                    else:
                        client = NativeJevClient.from_runtime(args.jev_runtime)
                        result = enrichment.score(main_store, args.output, client, limit=args.limit)
                else:
                    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
                    result = merge(main_store, args.output, RichWriter(config), limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        main_store.close()


if __name__ == '__main__':
    main()
