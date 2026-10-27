"""Prepare numbered paragraph additions; run requires a separate --execute flag."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.paragraph_curation import plan,run
from sw_kbase.native_jev import NativeJevClient
from sw_kbase.store import Store,process_lock


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('plan');a.add_argument('--document-run-id',required=True)
    a=sub.add_parser('run');a.add_argument('--execute',action='store_true');a.add_argument('--jev-runtime',type=Path,required=True)
    a.add_argument('--limit',type=int,default=0)
    args=p.parse_args()
    if args.command=='run' and not args.execute:p.error('Paragraph inference is disabled without the explicit --execute flag')
    if args.command=='run' and (args.database.parent/'openjev.pause').exists():p.error('Curation paused; paragraph inference not started')
    main=Store(args.database,readonly=True)
    try:
        if args.command=='plan':result=plan(main,args.output,args.document_run_id)
        else:
            client=NativeJevClient.from_runtime(args.jev_runtime)
            with process_lock(args.output):result=run(main,args.output,client,limit=args.limit)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:main.close()


if __name__=='__main__':main()
