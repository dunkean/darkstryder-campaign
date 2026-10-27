"""Acquire resumable public reference snapshots, CPU and HTTP only."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.acquire import acquire

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sources',help='Comma-separated collector names')
    a=p.parse_args()
    print(json.dumps(acquire(a.output,a.sources.split(',') if a.sources else None),ensure_ascii=False,indent=2))
