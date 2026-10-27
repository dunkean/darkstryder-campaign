"""Isolated, read-only three-document decision benchmark. Artifacts stay in runtime.

Run prepare/download on Windows, hf on WSL, http on Windows. No curation writes.
"""
from pathlib import Path
import argparse, concurrent.futures, hashlib, json, os, sqlite3, statistics
import sys, time, urllib.request, urllib.error

ROOT = Path(__file__).resolve().parents[2]
GPU = 'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52'
MODELS = {
 'perplexity': ('perplexity-ai/pplx-decider-flash-v1.2', '9ff7cf9838e59df461c276b7699d56b244d2af58'),
 'finecat': ('dleemiller/finecat-nli-ultra-l', '22813e158e480614a9604e696695d13b5609fa5e'),
}

def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)

def prepare(out, main_path, selection_path):
    sys.path.insert(0, str(ROOT))
    from packages.sw_kbase.store import Store
    from packages.sw_kbase.enrichment import load_document, paragraph_rows
    from packages.sw_kbase.curation_store import entity
    from packages.sw_kbase.grouped_paragraphs import request_for
    from packages.sw_kbase.joint_jev import JointJevClient
    db = sqlite3.connect('file:' + str(selection_path).replace('\\','/') + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    main = Store(main_path, readonly=True)
    client = JointJevClient.from_runtime(out.parent/'jev-joint', model='clef:flash-fp16', connect=False, verify_weights=False)
    ids = ['rec-00253312-124c-53f8-87ff-5a991985e5b5',
           'rec-003baa61-c440-537a-ab67-85fe497f667b',
           'rec-0154df64-0f4d-5544-9a0a-ecde416ad95f']
    cases = []
    for rid in ids:
        row = db.execute('SELECT d.entity_id,e.reference_id FROM documents d JOIN entities e ON e.id=d.entity_id WHERE d.id=?',(rid,)).fetchone()
        ref, source = load_document(main,row['reference_id']), load_document(main,rid)
        source['_model'] = client.model
        paragraphs = [{**p,'vote_id':f'P{i:02d}'} for i,p in enumerate(paragraph_rows(db,rid),1)]
        request = request_for(entity(main,row['entity_id']),source,ref,[],paragraphs)
        wire, preflight = client._prepare(request)
        case = {'id':str(len(cases)+1),'name':source['name'],'reference':ref,'candidate':source,
                'paragraphs':paragraphs,'wire':wire,'preflight_clef':preflight}
        # Original fragments must be exact spans of the immutable source body.
        for p in paragraphs:
            assert source['body'][p['offset_start']:p['offset_end']] == p['body']
        case['input_sha256'] = hashlib.sha256(json.dumps(wire['state'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cases.append(case)
    save(out/'cases.json', {'cases':cases,'selection_database':str(selection_path),
        'policy':'Complete reference + all coherent candidate blocks. No truncation. No database writes.'})
    print(json.dumps([{'name':c['name'],'blocks':len(c['paragraphs']),'chars':len(c['reference']['body'])+len(c['candidate']['body']), 'clef_tokens':c['preflight_clef']['total_tokens']} for c in cases]),flush=True)

def download(out):
    jobs=[]
    for key,(repo,rev) in MODELS.items():
        with urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true',timeout=40) as r:
            meta=json.load(r)
        save(out/f'{key}-repository.json',meta)
        for item in meta['siblings']:
            name=item['rfilename']
            if name.endswith(('.png','.jpg','.jpeg')) or name=='.gitattributes': continue
            target=out/'models'/key/name
            target.parent.mkdir(parents=True,exist_ok=True)
            jobs.append((repo,rev,name,target,item.get('lfs',{}).get('sha256'),item.get('size')))
    def fetch(job):
        repo,rev,name,target,sha,size=job
        if target.exists() and (not size or target.stat().st_size==size):
            if not sha or hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==sha: return name
        partial=target.with_suffix(target.suffix+'.part')
        if size and size>100_000_000:
            # Explicit finite ranges avoid stalled full-object responses on this network.
            chunks=[(a,min(a+128*1024*1024,size)-1) for a in range(0,size,128*1024*1024)]
            def part(bounds):
                a,b=bounds
                piece=target.with_suffix(target.suffix+f'.range-{a}')
                if piece.exists() and piece.stat().st_size==b-a+1:return piece
                for attempt in range(4):
                    try:
                        req=urllib.request.Request(f'https://huggingface.co/{repo}/resolve/{rev}/{name}',headers={'Range':f'bytes={a}-{b}'})
                        with urllib.request.urlopen(req,timeout=60) as r:
                            if r.status!=206 or r.headers.get('Content-Range','').split('/')[0]!=f'bytes {a}-{b}':raise ValueError('Range not honored')
                            with piece.open('wb') as f:
                                while block:=r.read(1024*1024):f.write(block)
                        if piece.stat().st_size!=b-a+1:raise ValueError('Incomplete range')
                        print(f'range {key}/{name} {a}-{b}',flush=True)
                        return piece
                    except Exception:
                        if attempt==3:raise
                        time.sleep(2)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
                pieces=list(workers.map(part,chunks))
            with partial.open('wb') as f:
                for piece in pieces:
                    with piece.open('rb') as src:
                        while block:=src.read(4*1024*1024):f.write(block)
            if sha and hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=sha:raise ValueError('SHA256 mismatch '+name)
            partial.replace(target)
            print('downloaded '+str(target.relative_to(out)),flush=True)
            return name
        for attempt in range(4):
            try:
                start=partial.stat().st_size if partial.exists() else 0
                req=urllib.request.Request(f'https://huggingface.co/{repo}/resolve/{rev}/{name}',headers={'Range':f'bytes={start}-'} if start else {})
                with urllib.request.urlopen(req,timeout=120) as r:
                    mode='ab' if start and r.status==206 else 'wb'
                    with partial.open(mode) as f:
                        while chunk:=r.read(4*1024*1024): f.write(chunk)
                if size and partial.stat().st_size != size: raise ValueError('File size mismatch '+name)
                if sha and hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=sha: raise ValueError('SHA256 mismatch '+name)
                partial.replace(target)
                print('downloaded '+str(target.relative_to(out)),flush=True)
                return name
            except Exception:
                if attempt==3: raise
                time.sleep(2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(fetch,jobs))

def http(out, model, base_url):
    def call(path,body=None):
        raw=None if body is None else json.dumps(body,ensure_ascii=False).encode()
        request=urllib.request.Request(base_url+path,data=raw,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=900) as r: return json.load(r)
        except urllib.error.HTTPError as error:
            detail=error.read().decode('utf-8',errors='replace')
            save(out/('failure-'+model.replace(':','-')+'.json'),{'status':error.code,'detail':detail})
            print(detail,flush=True)
            raise
    cases=json.loads((out/'cases.json').read_text(encoding='utf-8'))['cases']
    result={'model':model,'backend':'Ollaya native CUDA', 'gpu_uuid':GPU,'cases':[]}
    t=time.perf_counter()
    result['load_probe']=call('/api/decide',{'model':model,'state':'The color is blue.','questions':{'q':{'type':'choice','instructions':'Is the color blue?','criteria':{'yes':'Blue','no':'Other'}}},'keep_alive':-1})
    result['load_seconds']=time.perf_counter()-t
    result['loaded']=call('/api/ps')
    loaded=next((x for x in result['loaded']['models'] if x['name']==model),None)
    if not loaded or loaded.get('device')!='cuda:0':raise RuntimeError('Model is not exclusively on authorized cuda:0')
    for case in cases:
        wire={**case['wire'],'model':model,'keep_alive':-1}
        warm=call('/api/decide',wire)
        runs=[]
        for _ in range(3):
            start=time.perf_counter(); raw=call('/api/decide',wire); ms=1000*(time.perf_counter()-start)
            if raw.get('state_truncated') is not False: raise RuntimeError('Truncated or unattested input')
            if model.startswith('clef:') and raw['usage']['input_tokens']!=case['preflight_clef']['total_tokens']:
                raise RuntimeError('Clef exact token count mismatch')
            runs.append({'ms':ms,'raw':raw})
        result['cases'].append({'id':case['id'],'name':case['name'],'median_ms':statistics.median(r['ms'] for r in runs),'runs':runs})
        save(out/(model.replace(':','-')+'.json'),result)
        print(model,case['name'],round(result['cases'][-1]['median_ms'],2),flush=True)

def hf(out, key, fast_mode):
    os.environ['CUDA_VISIBLE_DEVICES']=GPU
    os.environ['CUDA_DEVICE_ORDER']='PCI_BUS_ID'
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['HF_HOME']=str(out/'hf-cache')
    os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR',str(Path.home()/'.cache/sw-decision-bench/torch'))
    os.environ['TOKENIZERS_PARALLELISM']='false'
    import torch
    torch.set_num_threads(8)
    if torch.cuda.device_count()!=1 or '5090' not in torch.cuda.get_device_name(0): raise RuntimeError('Wrong GPU')
    free,total=torch.cuda.mem_get_info()
    if total-free>2500*1048576:raise RuntimeError('5090 busy; benchmark paused before loading')
    folder=out/'models'/key
    cases=json.loads((out/'cases.json').read_text(encoding='utf-8'))['cases']
    result={'model':key,'revision':MODELS[key][1],'gpu_uuid':GPU,'torch':torch.__version__,'cases':[]}
    started=time.perf_counter()
    if key=='finecat':
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        tokenizer=AutoTokenizer.from_pretrained(folder,trust_remote_code=True)
        model=AutoModelForSequenceClassification.from_pretrained(folder,trust_remote_code=True,dtype=torch.bfloat16).eval().cuda()
        result.update(precision='bfloat16',labels=model.config.id2label)
        def run(case):
            pairs=[(case['reference']['body'],p['body']) for p in case['paragraphs']]
            batch=tokenizer(pairs,padding=True,truncation=False,return_tensors='pt').to('cuda')
            if batch['input_ids'].shape[1]>32768: raise RuntimeError('Input too long')
            with torch.inference_mode(): probs=model(**batch).logits.float().softmax(-1).cpu().tolist()
            return {'probabilities':{p['vote_id']:{str(model.config.id2label[i]):v for i,v in enumerate(row)} for p,row in zip(case['paragraphs'],probs)},'pair_tokens':batch['attention_mask'].sum(-1).cpu().tolist()}
    else:
        sys.path.insert(0,str(folder/'source'/'src'))
        from pplx_decider import Decider,FastOptions
        options=False if fast_mode=='reference' else FastOptions(compile='layer' if fast_mode=='graphs' else None,
            cuda_graphs=fast_mode=='graphs',buckets=(4096,),token_budget=4096,fp8=False)
        model=Decider.from_pretrained(str(folder),device='cuda',fast=options,max_length=4096)
        result.update(precision='bfloat16',backend='upstream '+fast_mode,fast_mode=fast_mode)
        torch.cuda.synchronize();result['load_seconds']=time.perf_counter()-started
        started_warm=time.perf_counter();model.warmup();torch.cuda.synchronize()
        result['compile_warmup_seconds']=time.perf_counter()-started_warm
        def run(case):
            value=model.decide({k:case['wire'][k] for k in ('state','questions')})
            if value['usage']['sequences']!=1:raise RuntimeError('Unexpected question splitting')
            return value
    torch.cuda.synchronize(); result.setdefault('load_seconds',time.perf_counter()-started)
    for case in cases:
        run(case);torch.cuda.synchronize()
        runs=[]
        for _ in range(3):
            torch.cuda.synchronize();start=time.perf_counter();raw=run(case);torch.cuda.synchronize()
            runs.append({'ms':1000*(time.perf_counter()-start),'raw':raw})
        result['cases'].append({'id':case['id'],'name':case['name'],'median_ms':statistics.median(r['ms'] for r in runs),'runs':runs})
        result['peak_allocated_mib']=torch.cuda.max_memory_allocated()/1048576
        save(out/(key+'.json'),result)
        print(key,case['name'],round(result['cases'][-1]['median_ms'],2),flush=True)

def doctor(out):
    # Explicitly hide every GPU before importing any model library.
    os.environ['CUDA_VISIBLE_DEVICES']=''
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['HF_HOME']=str(out/'hf-cache')
    import importlib.metadata
    import torch
    from transformers import AutoTokenizer,AutoConfig,AutoProcessor
    from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5Model
    from torch.nn.attention.varlen import varlen_attn
    cases=json.loads((out/'cases.json').read_text(encoding='utf-8'))['cases']
    folder=out/'models'/'perplexity'
    sys.path.insert(0,str(folder/'source'/'src'))
    from pplx_decider.protocol import Encoder
    encoder=Encoder(AutoProcessor.from_pretrained(folder),json.loads((folder/'decision_config.json').read_text())['codes'])
    tok=AutoTokenizer.from_pretrained(out/'models'/'finecat',trust_remote_code=True)
    cfg=AutoConfig.from_pretrained(out/'models'/'finecat',trust_remote_code=True)
    # Import the exact custom classifier without constructing/loading it.
    from transformers.dynamic_module_utils import get_class_from_dynamic_module
    classifier=get_class_from_dynamic_module('modeling_ultrabert.UltrabertForSequenceClassification',out/'models'/'finecat')
    assert cfg.id2label=={0:'entailment',1:'neutral',2:'contradiction'}
    rows=[]
    for case in cases:
        chunks=encoder.plan({k:case['wire'][k] for k in ('state','questions')},4096)
        assert len(chunks)==1
        batch=tok([(case['reference']['body'],p['body']) for p in case['paragraphs']],padding=False,truncation=False)
        lengths=[len(v) for v in batch['input_ids']]
        assert max(lengths)<32768
        rows.append({'case':case['name'],'perplexity_tokens':chunks[0].length,'perplexity_sequences':len(chunks),'finecat_pair_tokens':lengths})
    report={'cpu_only':True,'cuda_visible_devices':os.environ['CUDA_VISIBLE_DEVICES'],
        'gpu_initialized':torch.cuda.is_initialized(),'versions':{k:importlib.metadata.version(k) for k in ['torch','transformers','flash-linear-attention','triton']},'cases':rows}
    assert report['gpu_initialized'] is False
    save(out/'cpu-validation.json',report)
    print(json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','download','http','hf','doctor'])
    p.add_argument('--out',type=Path,required=True);p.add_argument('--main',type=Path);p.add_argument('--selection',type=Path)
    p.add_argument('--model');p.add_argument('--url',default='http://127.0.0.1:11439')
    p.add_argument('--fast-mode',choices=['reference','eager','graphs'],default='graphs')
    p.add_argument('--authorized-gpu-benchmark',action='store_true')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if a.action in ('http','hf') and (not a.authorized_gpu_benchmark or (a.out/'WAIT_FOR_USER_SIGNAL').exists()):
        p.error('GPU benchmark paused. Explicit user signal and --authorized-gpu-benchmark required.')
    if a.action=='prepare':prepare(a.out,a.main,a.selection)
    elif a.action=='download':download(a.out)
    elif a.action=='http':http(a.out,a.model,a.url)
    elif a.action=='doctor':doctor(a.out)
    else:hf(a.out,a.model,a.fast_mode)
