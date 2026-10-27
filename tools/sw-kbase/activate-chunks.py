"""Point paused local navigation/resume at a verified algorithmic chunk plan."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.rechunk import activate
from sw_kbase.store import Store,process_lock
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
args=p.parse_args()
with process_lock(args.database.parent):
    main=Store(args.database,readonly=True)
    try:print(json.dumps(activate(main,args.output),ensure_ascii=True))
    finally:main.close()
