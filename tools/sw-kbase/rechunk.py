"""Rebuild paragraph blocks by algorithms only; never starts a model or server."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.rechunk import rebuild
from sw_kbase.store import Store, process_lock

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--database',type=Path,required=True)
p.add_argument('--previous',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--stream',action='store_true',help='Use less RAM, at the cost of random SQLite reads')
p.add_argument('--resume',action='store_true',help='Continue a committed partial CPU rebuild')
args=p.parse_args()
if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[2]):p.error('Runtime outputs must live outside repository')
with process_lock(args.database.parent):
    main=Store(args.database,readonly=True)
    try:print(json.dumps(rebuild(main,args.previous,args.output,preload=not args.stream,resume=args.resume),ensure_ascii=True))
    finally:main.close()
