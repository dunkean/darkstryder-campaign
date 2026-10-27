"""Run document curation, hold after a measured sample, then resume by a local flag.

The sample counts actual native inference requests, not aggregate jobs or cache
hits. No links, historical worker, Strata or embeddings are run. All state and
control files live in the supplied runtime output directory.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.curation_store import execute
from sw_kbase.native_jev import NativeJevClient
from sw_kbase.store import Store,process_lock


def utc():return datetime.now(timezone.utc).isoformat()


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def percentile(values,p):
    values=sorted(values)
    return values[min(len(values)-1,int((len(values)-1)*p))] if values else None


class Paused(RuntimeError):pass


class Monitor:
    def __init__(self,client,output,sample,pause):
        self.client,self.output,self.sample,self.pause=client,output,sample,pause
        self.rows=[];self.started=time.perf_counter();self.gate_seconds=0.;self.job_id=None
        self.jobs=0;self.decisions=Counter();self.sample_written=False;self.deferred=Counter()

    def report(self):
        rows=self.rows[:self.sample or 100]
        latencies=[r['latency_ms'] for r in rows]
        labels=Counter(task+':'+answer['choice'] for row in rows for task,answer in row['answers'].items())
        return {'utc':utc(),'sample_requests':len(rows),'jobs_finished':self.jobs,
            'sample_wall_seconds':rows[-1]['elapsed_seconds'] if rows else None,
            'latency_ms':{'mean':statistics.mean(latencies) if latencies else None,
                          'p50':percentile(latencies,.5),'p95':percentile(latencies,.95),
                          'min':min(latencies) if latencies else None,'max':max(latencies) if latencies else None},
            'input_tokens':sum(sum(r['question_tokens'].values()) for r in rows),
            'labels':dict(labels),'document_selections':dict(self.decisions),
            'deferred_requests':dict(self.deferred),'errors':0,
            'requests':rows,'all_native_requests':len(self.rows),'gate_seconds':self.gate_seconds,
            'active_elapsed_seconds':time.perf_counter()-self.started-self.gate_seconds,
            'all_inference_seconds':sum(row['latency_ms'] for row in self.rows)/1000,
            'raw_documents_modified':False}

    def validate_runtime(self):return self.client.validate_runtime()

    def __call__(self,request):
        if self.sample and len(self.rows)>=self.sample and not (self.output/'continue').exists():
            write(self.output/'progress.json',{'stage':'sample_wait','utc':utc(),'requests':len(self.rows),'jobs':self.jobs})
            started=time.perf_counter()
            while not (self.output/'continue').exists():
                if self.pause.exists() or (self.output/'stop').exists():raise Paused('Paused at sample checkpoint')
                time.sleep(1)
            self.gate_seconds+=time.perf_counter()-started
        if self.pause.exists() or (self.output/'stop').exists():raise Paused('Native curation paused')
        started=time.perf_counter();response=self.client(request)
        elapsed=1000*(time.perf_counter()-started)
        if response.get('deferred_reason'):
            self.deferred[response['deferred_reason']]+=1
            return response
        row={'sequence':len(self.rows)+1,'utc':utc(),'job_id':self.job_id,'latency_ms':elapsed,
             'elapsed_seconds':time.perf_counter()-self.started-self.gate_seconds,
             'question_tokens':response['_native_jev']['question_tokens'],
             'request_fingerprint':response['_native_jev']['request_fingerprint'],
             'answers':{task:{'choice':answer['choice'],'probabilities':answer['probabilities']}
                        for task,answer in response['answers'].items()}}
        self.rows.append(row)
        with (self.output/'requests.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        if len(self.rows)%10==0:
            progress={'stage':'running','utc':utc(),'requests':len(self.rows),'jobs':self.jobs,'last_latency_ms':round(elapsed,2)}
            write(self.output/'progress.json',progress)
            if len(self.rows)<=100 or len(self.rows)%100==0:print(json.dumps(progress),flush=True)
        if self.sample and len(self.rows)==self.sample:
            write(self.output/'sample.json',self.report());self.sample_written=True
            print(json.dumps({'stage':'sample_ready','requests':len(self.rows),'output':str(self.output/'sample.json')}),flush=True)
        return response


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True,type=Path);p.add_argument('--jev-runtime',required=True,type=Path)
    p.add_argument('--run-id',required=True);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--sample',type=int,default=100)
    args=p.parse_args()
    if args.sample<0:raise ValueError('Sample must be nonnegative')
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'progress.json',{'stage':'verifying_model_identity','utc':utc(),'requests':0})
    initialization=time.perf_counter()
    client=NativeJevClient.from_runtime(args.jev_runtime)
    write(args.output/'model-identity.json',client.identity)
    initialization=time.perf_counter()-initialization
    pause=args.database.parent/'openjev.pause'
    with process_lock(args.database.parent):
        store=Store(args.database);monitor=Monitor(client,args.output,args.sample,pause)
        old_pause=pause.read_bytes() if pause.exists() else None
        try:
            run=store.db.execute('SELECT model_identity_json FROM jev_v1_runs WHERE id=?',(args.run_id,)).fetchone()
            if not run or json.loads(run[0])!=client.identity:raise ValueError('Planned and effective model identities differ')
            # Explicit user instruction to start this native run. The process
            # lock excludes another SW writer; no historical worker is invoked.
            pause.unlink(missing_ok=True)
            write(args.output/'started.json',{'utc':utc(),'run_id':args.run_id,'model_verification_seconds':initialization,
                  'restores_pause_on_exit':True,'policy':'document-priority; no link jobs'})
            jobs=store.db.execute("SELECT j.id FROM jev_v1_jobs j WHERE j.run_id=? AND j.kind='aggregate' "
                "AND j.status IN ('planned','prepared','running','error','paused') ORDER BY CASE "
                "WHEN EXISTS(SELECT 1 FROM jev_v1_previews p WHERE p.job_id=j.id AND p.tasks_json='[\"source_contribution\"]' AND p.deferred_reason='awaiting_jev') "
                "THEN CASE WHEN EXISTS(SELECT 1 FROM jev_v1_previews p WHERE p.job_id=j.id AND p.tasks_json='[\"entity_match\"]') THEN 1 ELSE 0 END "
                "ELSE 2 END,j.id",(args.run_id,)).fetchall()
            for job in jobs:
                if pause.exists() or (args.output/'stop').exists():raise Paused('Native curation paused between jobs')
                monitor.job_id=job['id']
                result=execute(store,job['id'],monitor,client.identity,model=client.model)
                monitor.jobs+=1
                monitor.decisions.update(row['merge_selection']+':'+row['reason'] for row in result.get('source_selection',[]))
                if monitor.jobs%100==0:write(args.output/'running-summary.json',monitor.report())
            write(args.output/'final.json',monitor.report())
            write(args.output/'progress.json',{'stage':'complete','utc':utc(),'requests':len(monitor.rows),'jobs':monitor.jobs})
        except Paused as error:
            write(args.output/'stopped.json',{'utc':utc(),'reason':str(error),'report':monitor.report()})
            write(args.output/'progress.json',{'stage':'paused','utc':utc(),'requests':len(monitor.rows),'jobs':monitor.jobs})
        except Exception as error:
            write(args.output/'error.json',{'utc':utc(),'error':str(error),'requests':len(monitor.rows),'jobs':monitor.jobs})
            raise
        finally:
            if not pause.exists():pause.write_bytes(old_pause if old_pause is not None else b'monitored_native_run_finished\n')
            store.close()


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
