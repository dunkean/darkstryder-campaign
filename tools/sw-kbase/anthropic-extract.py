"""Prepare or explicitly run a Haiku pilot of up to 100 articles. Preparation is CPU-only."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sw_kbase import anthropic_extraction as pipeline
from packages.sw_kbase.store import process_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare'); p.add_argument('--config', type=Path, required=True)
    commands.add_parser('report')
    p = commands.add_parser('serve'); p.add_argument('--port', type=int, default=8769)
    commands.add_parser('count', help='Free provider token count; transmits prepared text, no generation')
    p = commands.add_parser('run'); p.add_argument('--execute', action='store_true')
    p = commands.add_parser('batch-file'); p.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    if args.output.resolve().is_relative_to(repository): parser.error('Pilot outputs must stay outside Git')
    if args.command == 'run' and not args.execute: parser.error('Generation requires --execute and user GO')
    if args.command == 'batch-file' and args.destination.resolve().is_relative_to(repository):
        parser.error('Batch payloads must stay outside Git')
    if args.command == 'serve':
        from packages.sw_kbase.anthropic_review import make_server
        config = json.loads((args.output / 'config.local.json').read_text(encoding='utf-8'))
        server = make_server(args.output, config['database'], args.port)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return
    if args.command == 'prepare':
        result = pipeline.prepare(pipeline.read_config(args.config), args.output)
        result = {key: value for key, value in result.items() if key != 'articles'}
    else:
        with process_lock(args.output):
            if args.command == 'report': result = pipeline.report(args.output)
            elif args.command == 'count': result = pipeline.count_tokens(args.output)
            elif args.command == 'run': result = pipeline.run(args.output)
            else: result = pipeline.batch_file(args.output, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
