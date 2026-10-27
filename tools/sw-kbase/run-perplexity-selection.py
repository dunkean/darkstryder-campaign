"""Explicit, resumable Perplexity selection controller; default is CPU validation."""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
import sqlite3,subprocess,sys,threading,time,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase import enrichment,grouped_paragraphs
from sw_kbase.store import Store,process_lock
from sw_kbase.perplexity_jev import PerplexityParagraphClient,WorkerTransport
from sw_kbase.gpu_priority import Windows5090Probe,DESKTOP
from sw_kbase.disambiguation import fingerprint

def atomic_json(path,value):
    # Readers outside this controller may briefly hold a non-delete-sharing handle.
    for attempt in range(100):
        try:return enrichment.atomic_json(path,value)
        except PermissionError:
            if attempt==99:raise
            time.sleep(.01)

def read_config(path):
    cfg=json.loads(path.read_text(encoding='utf-8'))
    if cfg['identity']['gpu_uuid']!=cfg['gpu_uuid'] or cfg['gpu_uuid']!='GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52':
        raise ValueError('Only the 5090 is authorized')
    return cfg

def validate(cfg,verify_snapshots=False):
    client=PerplexityParagraphClient(cfg)
    if client.identity!=cfg['identity']:raise ValueError('Prepared runtime identity changed; reconfigure')
    main=Store(Path(cfg['database']),readonly=True)
    db=sqlite3.connect(Path(cfg['selection_database']).resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        settings=enrichment.meta(db)
        if settings['policy']!=grouped_paragraphs.VERSION or settings['units_policy']!='document-sections/2.2-coherent-blocks':
            raise ValueError('Unexpected selection/chunking policy')
        if settings.get('jev_identity') not in (None,client.identity):raise ValueError('Existing decisions belong to another model; no vote reuse')
        if verify_snapshots:enrichment.verify(main,db)
        result={'ready':True,'inference_started':False,'gpu_initialized':False,'model':client.model,
            'documents':[dict(r) for r in db.execute('SELECT role,status,count(*) count FROM documents GROUP BY role,status')],
            'cached_requests':db.execute('SELECT count(*) FROM requests').fetchone()[0],
            'candidate_blocks':db.execute("SELECT count(*) FROM paragraphs p JOIN documents d ON d.id=p.document_id WHERE d.role='candidate'").fetchone()[0],
            'selection_database':cfg['selection_database'],'main_database_modified':False,
            'identity':client.identity,'awaiting_user_go':Path(cfg['wait_marker']).exists(),'snapshot_check':verify_snapshots}
        return result,client
    finally:db.close();main.close()

def command(cfg,session,execute=False):
    scripts=[cfg['launcher_script_wsl'],cfg['worker_script_wsl']] if cfg.get('launcher_script_wsl') else [cfg['worker_script_wsl']]
    args=['wsl.exe','-d',cfg['distribution'],'--exec',cfg['python_wsl'],'-u',*scripts,
        '--config',cfg['config_wsl'],'--session',session]
    if execute:args.append('--execute')
    return args

def stop_worker(cfg,session,transport):
    state=Path(cfg['worker_state'])
    if transport is None:return
    if state.exists():
        if transport.process.poll() is None:
            args=['wsl.exe','-d',cfg['distribution'],'--exec','/usr/bin/python3',cfg['worker_script_wsl'],
                '--stop-owned',cfg['worker_state_wsl'],'--session',session]
            stopped=subprocess.run(args,capture_output=True,text=True,timeout=15)
            if stopped.returncode:raise RuntimeError('Owned worker stop refused: '+stopped.stderr[-1000:])
            try:transport.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced=subprocess.run([*args,'--force'],capture_output=True,text=True,timeout=15)
                if forced.returncode:raise RuntimeError('Owned worker force-stop refused: '+forced.stderr[-1000:])
                transport.process.wait(timeout=10)
    elif transport.process.poll() is None:
        # During CPU startup, closing stdin makes the not-yet-loaded worker exit.
        transport.process.stdin.close();transport.process.wait(timeout=60)

class Controller:
    def __init__(self,cfg,config_path):
        self.cfg=cfg;self.config_path=config_path;self.session=uuid.uuid4().hex
        self.output=Path(cfg['control_root'])/'sessions'/self.session
        self.output.mkdir(parents=True,exist_ok=False)
        self.pause=Path(cfg['database']).parent/'openjev.pause';self.stop=Path(cfg['control_root'])/'stop'
        self.finished=threading.Event();self.pending=None;self.transport=None;self.count=0;self.stop_lock=threading.Lock();self.worker_status={}
        self.probe=Windows5090Probe();self.baseline={};self.write_lock=threading.Lock();self.stage='validating'
    def write(self,**extra):
        atomic_json(self.output/'workflow.json',{'session':self.session,'stage':self.stage,'pid':os.getpid(),
            'utc':time.time(),'model':self.cfg['identity']['model'],'priority_yield':self.pending,
            'enrichment_output':str(Path(self.cfg['selection_database']).parent),'strata_started':False,**extra})
    def yield_(self,reason):
        with self.write_lock:
            if self.pending:return
            self.pending=reason;self.pause.write_text('perplexity_priority_pause\n',encoding='utf-8')
            atomic_json(self.output/'yield.json',reason)
    def check(self):
        if self.stop.exists() or any(Path(p).exists() for p in self.cfg['gpu_request_markers']):self.yield_({'reason':'user_stop_or_gpu_request'})
        if self.pending:raise RuntimeError('Perplexity paused: '+str(self.pending))
        if self.pause.exists():raise RuntimeError('Enrichment paused')
    def stop_worker(self):
        with self.stop_lock:stop_worker(self.cfg,self.session,self.transport)
    def baseline_probe(self):
        snapshot=self.probe.read()
        if snapshot['free_mib']<28500:raise RuntimeError('5090 unavailable; no model started')
        for p in snapshot['processes']:
            if p['name'].casefold() in DESKTOP:continue
            if p.get('engine_percent',0)>=1 or (p['name'].casefold() in {'vmwp.exe','vmmemwsl'} and p['dedicated_bytes']>64*1048576):
                raise RuntimeError('Other work is using the 5090; no model started')
        # Inactive desktop/driver contexts are allowed; activity or growth yields.
        self.baseline={p['pid']:p for p in snapshot['processes']}
    def guard(self):
        while not self.finished.wait(2):
            try:
                self.check()
                # Telemetry uses the existing pipe. Windows must never hold the
                # WSL journal open while Linux atomically replaces that file.
                if self.transport is not None and self.stage!='loading_perplexity_5090' and not self.transport.lock.locked():
                    self.worker_status=self.transport('ping')
                worker=self.worker_status.copy()
                if self.stage=='loading_perplexity_5090':worker['stage']='loading'
                if worker.get('session')!=self.session:worker={}
                if worker.get('yield'):self.yield_(worker['yield'])
                snap=self.probe.read()
                for p in snap['processes']:
                    name=p['name'].casefold()
                    if name in DESKTOP:continue
                    if name in {'vmwp.exe','vmmemwsl'} and worker.get('stage') in {'loading','warming','ready','scoring'}:
                        # WSL worker independently observes foreign GPU PIDs and
                        # total GPU memory minus its own Torch reservation.
                        continue
                    prior=self.baseline.get(p['pid']);growth=p['dedicated_bytes']-(prior['dedicated_bytes'] if prior else 0)
                    if p['engine_percent']>=1 or growth>=64*1048576:self.yield_({'reason':'foreign_5090_activity','pid':p['pid'],'name':p['name']})
                atomic_json(self.output/'gpu-priority.json',{'utc':time.time(),'used_mib':snap['used_mib'],
                    'free_mib':snap['free_mib'],'yield':self.pending,'worker':worker})
                if self.pending:
                    self.stop_worker();return
            except Exception as error:
                self.yield_({'reason':'gpu_guard_or_pause','error':str(error)})
                self.stop_worker();return
    def run(self):
        with process_lock(Path(self.cfg['control_root'])),process_lock(Path(self.cfg['selection_database']).parent):
            initial_pause=self.pause.read_bytes() if self.pause.exists() else None
            log=(self.output/'worker.log').open('a',encoding='utf-8');guard=None
            try:
                self.write();_,client=validate(self.cfg,verify_snapshots=True)
                pointer=Path(self.cfg['active_pointer'])
                previous=json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
                atomic_json(self.output/'previous-pointer.json',previous)
                atomic_json(pointer,{**previous,'control':str(self.output),'launcher_pid':os.getpid(),
                    'enrichment':str(Path(self.cfg['selection_database']).parent),'state':'perplexity_running','backend':'perplexity-flash'})
                if Path(self.cfg['wait_marker']).exists():raise RuntimeError('Awaiting explicit user go; no worker started')
                self.baseline_probe()
                if self.pause.exists():
                    if self.pause.read_bytes()!=initial_pause:raise RuntimeError('A newer pause supersedes this run')
                    (self.output/'previous-pause.txt').write_bytes(initial_pause);self.pause.unlink()
                self.check()
                self.transport=WorkerTransport(command(self.cfg,self.session,True),log,timeout=600)
                hello=self.transport('ping')
                if hello['identity']!=fingerprint(client.identity) or hello.get('model_loaded') is not False:
                    raise ValueError('Unexpected preparation handshake')
                self.worker_status=hello
                guard=threading.Thread(target=self.guard,daemon=True);guard.start()
                self.stage='loading_perplexity_5090';self.write()
                loaded=self.transport('load')
                if loaded.get('identity')!=fingerprint(client.identity):raise ValueError('Loaded model identity mismatch')
                self.worker_status={**self.worker_status,'stage':'ready','model_loaded':True}
                self.check();client.transport=self.transport
                self.stage='selecting_paragraphs';self.write()
                parent=self
                class Monitored:
                    model=client.model;capabilities=client.capabilities
                    @property
                    def identity(self):return client.identity
                    def validate_runtime(self):parent.check();return client.validate_runtime()
                    def preflight(self,request):parent.check();return client.preflight(request)
                    def __call__(self,request):
                        parent.check();start=time.monotonic();response=client(request);parent.count+=1
                        row={'utc':time.time(),'sequence':parent.count,'entity_id':request['state']['ENTITY']['id'],
                            'source_id':request['state']['SOURCE']['id'],'latency_ms':1000*(time.monotonic()-start),
                            'paragraph_scores':len(response['paragraphs']),'tokens':response['_native_jev']['preflight']['total_tokens']}
                        with (parent.output/'inferences.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
                        if parent.count%10==0 or parent.count<=3:parent.write(native_calls=parent.count,last=row)
                        return response
                main=Store(Path(self.cfg['database']),readonly=True)
                try:result=grouped_paragraphs.score(main,Path(self.cfg['selection_database']).parent,Monitored())
                finally:main.close()
                self.stage='complete';self.write(result=result)
            except Exception as error:
                self.stage='paused' if self.pending or self.pause.exists() else 'error';self.write(error=str(error))
                if self.stage=='error':raise
            finally:
                self.finished.set()
                if guard:guard.join(timeout=25)
                self.stop_worker();log.close()
                if not self.pause.exists():self.pause.write_text('perplexity_selection_stopped\n',encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--validate-only',action='store_true');p.add_argument('--verify-snapshots',action='store_true')
    args=p.parse_args();cfg=read_config(args.config)
    if args.execute and args.validate_only:p.error('Choose validation or execution')
    if not args.execute:
        result,_=validate(cfg,args.verify_snapshots);print(json.dumps(result,ensure_ascii=True,indent=2));return
    if Path(cfg['wait_marker']).exists():p.error('Awaiting the next explicit user go; execution remains locked')
    Controller(cfg,args.config).run()

if __name__=='__main__':main()
