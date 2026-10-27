"""Operational WSL filesystem compatibility; decision worker bytes stay pinned."""
import os,runpy,sys,time
from pathlib import Path

def replace_with_retry(original,source,target,*args,**kwargs):
    if Path(target).name!='worker-state.json':return original(source,target,*args,**kwargs)
    for attempt in range(200):
        try:return original(source,target,*args,**kwargs)
        except PermissionError:
            if attempt==199:raise
            time.sleep(.01)

def main():
    script=Path(sys.argv[1]).resolve()
    if script!=Path(__file__).resolve().with_name('perplexity-worker.py'):raise ValueError('Unexpected worker path')
    original=os.replace
    os.replace=lambda source,target,*a,**kw:replace_with_retry(original,source,target,*a,**kw)
    sys.argv=sys.argv[1:]
    runpy.run_path(str(script),run_name='__main__')

if __name__=='__main__':main()
