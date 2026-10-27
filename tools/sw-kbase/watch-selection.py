"""Read-only document milestones, independent of model request counts."""
import argparse,json,sqlite3,time
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--every',type=int,default=5000)
a=p.parse_args();cfg=json.loads(a.config.read_text(encoding='utf-8'));root=Path(cfg['control_root'])
journal=root/'document-milestones.jsonl';seen=set()
if journal.exists():
    for line in journal.read_text(encoding='utf-8').splitlines():
        try:seen.add(json.loads(line)['milestone'])
        except (ValueError,KeyError):pass
began=time.monotonic();initial=None;last_status=0
while True:
    db=sqlite3.connect(Path(cfg['selection_database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    try:
        rows=dict(db.execute("SELECT status,count(*) FROM documents WHERE role='candidate' GROUP BY status"))
        done=rows.get('scored',0);total=sum(rows.values());requests=db.execute('SELECT count(*) FROM requests').fetchone()[0]
    finally:db.close()
    if initial is None:initial=done
    elapsed=time.monotonic()-began;rate=(done-initial)/elapsed if elapsed>0 else 0
    pointer=json.loads(Path(cfg['active_pointer']).read_text(encoding='utf-8'))
    workflow=Path(pointer['control'])/'workflow.json'
    state=json.loads(workflow.read_text(encoding='utf-8')) if workflow.exists() else {}
    row={'utc':time.time(),'documents':done,'total':total,'requests':requests,'documents_per_second':round(rate,3),
        'eta_seconds':round((total-done)/rate) if rate>0 else None,'stage':state.get('stage')}
    for milestone in range(a.every,(done//a.every)*a.every+1,a.every):
        if milestone in seen:continue
        event={**row,'milestone':milestone}
        with journal.open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')
        seen.add(milestone);print(json.dumps({'type':'milestone',**event}),flush=True)
    if time.monotonic()-last_status>=45:
        print(json.dumps({'type':'status',**row}),flush=True);last_status=time.monotonic()
    if state.get('stage') in {'paused','error','complete'}:
        print(json.dumps({'type':'terminal',**row,'state':state}),flush=True);break
    time.sleep(5)
