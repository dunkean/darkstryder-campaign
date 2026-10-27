"""Prepare/apply evidence-based types without loading Jev or changing source snapshots."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))
from sw_kbase.store import Store, process_lock
from sw_kbase.type_mining import run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    if args.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        p.error('Runtime report must live outside the repository')
    with process_lock(args.database.parent):
        main_store = Store(args.database, readonly=True)
        try:
            report = run(main_store, args.apply)
        finally:
            main_store.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
