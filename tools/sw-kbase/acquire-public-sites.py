"""Acquire the explicitly selected public sources through a dedicated local proxy."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.acquire import Snapshot
from sw_kbase.public_sites import rpggamer,d6db,seawolf,seawolf_atlas,d6db_library

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--proxy',required=True)
    p.add_argument('--sources',required=True)
    p.add_argument('--min-interval',type=float,default=.2)
    p.add_argument('--robots-override',default='')
    a=p.parse_args()
    if a.min_interval<.1:
        p.error('Use at least 100 ms between requests to each host')
    snapshot=Snapshot(a.output,proxy=a.proxy,min_interval=a.min_interval)
    snapshot.robots_overrides=set(a.robots_override.split(','))
    collectors={'rpggamer':rpggamer,'d6db':d6db,'seawolf':seawolf,'seawolf-atlas':seawolf_atlas,'d6db-library':d6db_library}
    selected=a.sources.split(',')
    if any(name not in collectors for name in selected):
        p.error('Unknown collector')
    reports=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(collectors[name],snapshot):name for name in selected}
        for future in as_completed(futures):
            name=futures[future]
            try:reports.append(future.result())
            except Exception as exc:
                reports.append({'source':name,'status':'failed','error':repr(exc)})
                print(json.dumps(reports[-1]),flush=True)
    (a.output/'public-sites-run.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=False,indent=2),flush=True)
