"""Open the active reference SQLite in a local read-only SQL browser."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=project / 'config/sw-kbase.local.json')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--python', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port')
    database = Path(json.loads(args.config.read_text(encoding='utf-8-sig'))['database']).resolve()
    if not database.is_file():
        parser.error('Configured SQLite database is absent')
    executable = args.python or Path(os.environ['LOCALAPPDATA']) / 'darkstryder-runtime/sql-viewer/venv/Scripts/python.exe'
    if not executable.is_file():
        parser.error('Install sqlite-web in its dedicated runtime venv, or pass --python')
    return subprocess.call([str(executable), '-m', 'sqlite_web.sqlite_web', str(database),
                            '--read-only', '--host', '127.0.0.1', '--port', str(args.port), '--no-browser'])


if __name__ == '__main__':
    raise SystemExit(main())
