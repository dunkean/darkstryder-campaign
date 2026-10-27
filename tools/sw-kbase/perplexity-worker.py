"""Owned WSL decision worker. CPU-only unless --execute is explicitly supplied."""
from __future__ import annotations
import argparse, contextlib, csv, hashlib, importlib.metadata, json, os
from pathlib import Path
import signal, subprocess, sys, threading, time

MAX_LINE=16*1024*1024

def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(path)

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def stop_owned(path,session,force=False):
    receipt=json.loads(path.read_text());pid=int(receipt['pid']);proc=Path('/proc')/str(pid)
    if receipt['session']!=session:raise ValueError('Worker session changed; refusing to stop')
    if not proc.exists():return
    try:
        ticks=proc.joinpath('stat').read_text().split(') ',1)[1].split()[19]
        argv=[p.decode() for p in proc.joinpath('cmdline').read_bytes().split(b'\0') if p]
    except FileNotFoundError:return
    if ticks!=receipt['start_ticks'] or argv!=receipt['argv'] or str(Path(__file__).resolve()) not in argv or session not in argv:
        raise ValueError('Worker PID/argv/start time mismatch; refusing to stop')
    os.kill(pid,signal.SIGKILL if force else signal.SIGTERM)

class Worker:
    def __init__(self,cfg,session,execute):
        self.cfg=cfg;self.session=session;self.execute=execute
        self.lock=threading.Lock();self.done=threading.Event();self.model=None
        self.identity=cfg['identity'];self.identity_hash=fingerprint(self.identity)
        self.state_path=Path(cfg['worker_state_wsl']);self.calls=0;self.external_baseline=None
        os.environ['CUDA_VISIBLE_DEVICES']=cfg['gpu_uuid'] if execute else ''
        os.environ['CUDA_DEVICE_ORDER']='PCI_BUS_ID';os.environ['HF_HUB_OFFLINE']='1'
        os.environ['HF_HOME']=cfg['hf_home_wsl'];os.environ['TOKENIZERS_PARALLELISM']='false'
        os.environ['TORCHINDUCTOR_CACHE_DIR']=cfg['torch_cache_wsl']
        self.state={'session':session,'pid':os.getpid(),'stage':'cpu_initialization',
            'start_ticks':Path('/proc/self/stat').read_text().split(') ',1)[1].split()[19],
            'argv':[p.decode() for p in Path('/proc/self/cmdline').read_bytes().split(b'\0') if p],
            'gpu_uuid':cfg['gpu_uuid'],'identity':self.identity_hash,'yield':None,'calls':0,'model_loaded':False}
        self.update()
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from transformers import AutoProcessor
            self.torch=torch
            actual={name:importlib.metadata.version(name) for name in cfg['versions']}
            if actual!=cfg['versions']:raise ValueError('Worker dependency versions changed')
            root=Path(cfg['checkpoint_wsl']);manifest=json.loads(Path(cfg['manifest_wsl']).read_text())
            for row in manifest['files']:
                path=(root/row['path']).resolve()
                if not path.is_relative_to(root.resolve()) or path.stat().st_size!=row['size']:raise ValueError('Checkpoint file changed')
            sys.path.insert(0,str(root/'source/src'))
            from pplx_decider.protocol import Encoder
            self.encoder=Encoder(AutoProcessor.from_pretrained(root),json.loads((root/'decision_config.json').read_text())['codes'])
            if torch.cuda.is_initialized() and not execute:raise ValueError('GPU initialized during CPU preparation')
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self.identity['worker_sha256']:
            raise ValueError('Worker code changed since preparation')
        self.update(stage='cpu_ready',gpu_initialized=torch.cuda.is_initialized())

    def update(self,**extra):
        with self.lock:
            self.state.update(extra,utc=time.time(),calls=self.calls)
            save(self.state_path,self.state)

    def monitor(self):
        high_memory_samples=0
        while not self.done.wait(1):
            try:
                sampled=subprocess.run(['/usr/lib/wsl/lib/nvidia-smi','-i',self.cfg['gpu_uuid'],
                    '--query-compute-apps=pid','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=8,check=True)
                foreign=[]
                for row in csv.reader(sampled.stdout.splitlines()):
                    if not row or not row[0].strip().isdigit():continue
                    pid=int(row[0]);cursor=pid;owned=False
                    for _ in range(30):
                        if cursor==os.getpid():owned=True;break
                        try:cursor=int((Path('/proc')/str(cursor)/'stat').read_text().split(') ',1)[1].split()[1])
                        except (FileNotFoundError,ValueError):break
                        if cursor<=1:break
                    if not owned:foreign.append(pid)
                extra={}
                if self.torch.cuda.is_initialized():
                    free,total=self.torch.cuda.mem_get_info();reserved=self.torch.cuda.memory_reserved()/1048576
                    external=(total-free)/1048576-reserved
                    extra={'reserved_mib':reserved,'gpu_used_mib':(total-free)/1048576,'external_mib':external}
                    # CUDA Graphs/Triton allocate driver memory outside Torch's
                    # allocator during warmup. Establish the steady baseline
                    # only after that phase; foreign PIDs remain checked above.
                    if self.external_baseline is None and self.state['stage'] in {'ready','scoring'}:
                        self.external_baseline=external
                    high_memory_samples=high_memory_samples+1 if self.external_baseline is not None and external>self.external_baseline+256 else 0
                    if high_memory_samples>=2:foreign.append('additional_memory')
                if foreign:self.update(**extra,**{'yield':{'reason':'foreign_5090_compute','processes':foreign}});return
                self.update(**extra)
            except Exception as error:
                self.update(**{'yield':{'reason':'gpu_observer_failed','error':str(error)}});return

    def command(self,name,payload):
        if name=='ping':return {**self.state,'gpu_initialized':self.torch.cuda.is_initialized()}
        if name=='encode':
            encoded=self.encoder.encode(payload)
            return {'ids':encoded.input_ids.tolist(),'length':encoded.length,'gpu_initialized':self.torch.cuda.is_initialized()}
        if name=='shutdown':self.done.set();return {'stopped':True}
        if not self.execute:raise ValueError('Inference disabled in CPU-only worker')
        if self.state['yield']:raise ValueError('GPU priority yield: '+json.dumps(self.state['yield']))
        if name=='load':
            if self.model is not None:return {'identity':self.identity_hash,'already_loaded':True}
            self.update(stage='loading')
            self.thread=threading.Thread(target=self.monitor,daemon=True);self.thread.start()
            with contextlib.redirect_stdout(sys.stderr):
                from pplx_decider import Decider,FastOptions
                if self.torch.cuda.device_count()!=1 or '5090' not in self.torch.cuda.get_device_name(0):raise ValueError('Wrong GPU')
                props=self.torch.cuda.get_device_properties(0)
                if str(props.uuid).lower().removeprefix('gpu-')!=self.cfg['gpu_uuid'].lower().removeprefix('gpu-'):
                    raise ValueError('CUDA UUID mismatch')
                free,total=self.torch.cuda.mem_get_info()
                if total-free>2500*1048576:raise ValueError('5090 already occupied')
                self.model=Decider.from_pretrained(self.cfg['checkpoint_wsl'],device='cuda',max_length=4096,
                    fast=FastOptions(compile='layer',cuda_graphs=True,buckets=(4096,),token_budget=4096,fp8=False))
                self.update(stage='warming');self.model.warmup();self.torch.cuda.synchronize()
            self.update(stage='ready',gpu_initialized=True,model_loaded=True)
            if self.state['yield']:raise ValueError('GPU claimed during model warmup')
            return {'identity':self.identity_hash,'gpu_uuid':self.cfg['gpu_uuid'],'precision':'bfloat16','ready':True}
        if name=='decide':
            if self.model is None:raise ValueError('Load must precede inference')
            if payload['identity']!=self.identity_hash:raise ValueError('Caller identity mismatch')
            request=payload['request'];encoded=self.encoder.encode(request)
            if encoded.length>4096:raise ValueError('Complete request exceeds budget; no truncation')
            self.update(stage='scoring')
            with contextlib.redirect_stdout(sys.stderr):response=self.model.decide(request)
            if response['usage']['sequences']!=1:raise ValueError('Unexpected question splitting')
            self.calls+=1;self.update(stage='ready')
            return {'identity':self.identity_hash,'response':response}
        raise ValueError('Unknown worker command')

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path);p.add_argument('--session',required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--stop-owned',type=Path);p.add_argument('--force',action='store_true')
    args=p.parse_args()
    if args.stop_owned:stop_owned(args.stop_owned,args.session,args.force);return
    cfg=json.loads(args.config.read_text());worker=Worker(cfg,args.session,args.execute)
    try:
        while line:=sys.stdin.buffer.readline(MAX_LINE+1):
            if len(line)>MAX_LINE:raise ValueError('Worker request too large')
            row=json.loads(line);answer={'id':row['id']}
            try:answer['result']=worker.command(row['command'],row.get('payload'))
            except Exception as error:
                import traceback
                traceback.print_exc(file=sys.stderr);answer['error']=str(error)
            sys.stdout.write('@PPLX@'+json.dumps(answer,ensure_ascii=False,allow_nan=False)+'\n');sys.stdout.flush()
            if worker.done.is_set():break
    finally:worker.done.set()

if __name__=='__main__':main()
