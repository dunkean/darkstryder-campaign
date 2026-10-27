"""Plan or explicitly run the new three-question native Jev policy.

Planning never calls inference. Running respects openjev.pause and does not
invoke the old worker, Strata, embeddings or any merge of canonical IDs.
Use the isolated Jev validation Python (tokenizers installed there).
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.curation_store import plan,execute,status,prepare_documents
from sw_kbase.native_jev import NativeJevClient
from sw_kbase.store import Store,process_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--jev-runtime',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:11437')
    parser.add_argument('--model',default='jeb-local:27b-v2.1-q8_0')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--aggregate',action='append')
    p.add_argument('--all',action='store_true');p.add_argument('--include-aliases',action='store_true')
    p.add_argument('--include-links',action='store_true');p.add_argument('--limit',type=int,default=100)
    p.add_argument('--prepare',action='store_true',help='Algorithmic document deduplication and exact local token census, no inference')
    p=sub.add_parser('prepare');p.add_argument('--run-id',required=True);p.add_argument('--limit',type=int,default=0)
    p=sub.add_parser('run');p.add_argument('--run-id',required=True);p.add_argument('--limit',type=int,default=1)
    p=sub.add_parser('status');p.add_argument('--run-id')
    args=parser.parse_args()
    if args.command=='status':
        store=Store(args.database,readonly=True)
        try:print(json.dumps(status(store,args.run_id),ensure_ascii=False,indent=2))
        finally:store.close()
        return
    if not args.jev_runtime:parser.error('--jev-runtime is required for the effective model identity')
    if args.limit<0:parser.error('--limit must be nonnegative')
    if args.command=='plan' and not args.aggregate and not args.all:parser.error('Choose --aggregate IDs or --all')
    if args.command=='run' and (args.database.parent/'openjev.pause').exists():parser.error('Curation is paused; no model loaded or inference started')
    client=NativeJevClient.from_runtime(args.jev_runtime,model=args.model,base_url=args.url)
    with process_lock(args.database.parent):
        store=Store(args.database)
        try:
            if args.command=='plan':
                planned=plan(store,client.identity,aggregate_ids=args.aggregate,include_aliases=args.include_aliases,
                    include_links=args.include_links,limit=args.limit)
                print(json.dumps(planned,ensure_ascii=False,indent=2),flush=True)
                if args.prepare:
                    print(json.dumps(prepare_documents(store,planned['run_id'],preflight=client.preflight,model=client.model,
                        progress=lambda value:print(json.dumps(value),flush=True)),ensure_ascii=False,indent=2),flush=True)
            elif args.command=='prepare':
                print(json.dumps(prepare_documents(store,args.run_id,preflight=client.preflight,model=client.model,limit=args.limit,
                    progress=lambda value:print(json.dumps(value),flush=True)),ensure_ascii=False,indent=2),flush=True)
            else:
                rows=store.db.execute("SELECT id FROM jev_v1_jobs WHERE run_id=? AND status IN ('planned','prepared','running','error') "
                    "ORDER BY CASE kind WHEN 'aggregate' THEN 0 WHEN 'alias' THEN 1 ELSE 2 END,id LIMIT ?",
                    (args.run_id,args.limit or -1)).fetchall()
                for row in rows:
                    result=execute(store,row[0],client,client.identity,model=client.model)
                    print(json.dumps({'job_id':row[0],'model_calls':result['model_calls'],'cache_hits':result['cache_hits']},ensure_ascii=False),flush=True)
        finally:store.close()


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
