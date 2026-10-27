"""Extract acquisition origins and cited works without any model calls."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))
from sw_kbase.store import Store, process_lock
from sw_kbase.provenance import run

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--database', type=Path, required=True)
p.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[2] / 'catalog/sources.json')
p.add_argument('--report', type=Path, required=True)
p.add_argument('--apply', action='store_true')
p.add_argument('--dataset', action='append', help='Only refresh this exact dataset; repeat for several datasets')
args = p.parse_args()
if args.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]): p.error('Runtime report must be outside repository')
with process_lock(args.database.parent):
    store = Store(args.database, readonly=True)
    try: report = run(store, json.loads(args.catalog.read_text(encoding='utf-8-sig')), args.apply, args.dataset)
    finally: store.close()
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k != 'by_dataset'}, ensure_ascii=True))
