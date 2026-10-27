"""CPU preparation only; preserve the plan and require a later explicit go."""
import argparse,hashlib,importlib.metadata,json
from pathlib import Path,PureWindowsPath,PurePosixPath
import sys,time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'packages'))
from sw_kbase.perplexity_jev import MODEL,REVISION,runtime_identity,PerplexityParagraphClient
from sw_kbase.enrichment import atomic_json

def wsl(path):
    p=PureWindowsPath(str(Path(path).resolve()))
    return '/mnt/'+p.drive[0].lower()+'/'+('/'.join(p.parts[1:]))

def main():
    p=argparse.ArgumentParser();p.add_argument('--benchmark',type=Path,required=True);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--selection',type=Path,required=True)
    p.add_argument('--control-base',type=Path,required=True);p.add_argument('--python-windows',required=True)
    p.add_argument('--python-wsl');p.add_argument('--torch-cache-wsl')
    args=p.parse_args();runtime=args.runtime.resolve()
    if runtime.is_relative_to(ROOT):p.error('Runtime must be outside Git')
    runtime.mkdir(parents=True,exist_ok=True);checkpoint=args.benchmark.resolve()/'models/perplexity'
    benchmark_settings=json.loads((args.benchmark/'bench-state.json').read_text(encoding='utf-8'))
    venv=benchmark_settings.get('venv_wsl')
    if not venv and not args.python_wsl:p.error('Supply --python-wsl or a benchmark with venv_wsl metadata')
    python_wsl=args.python_wsl or str(PurePosixPath(venv)/'bin/python')
    torch_cache=args.torch_cache_wsl or str(PurePosixPath(python_wsl).parent.parent.parent/'torch-cache')
    metadata=json.loads((args.benchmark/'perplexity-repository.json').read_text(encoding='utf-8'))
    if metadata['sha']!=REVISION:raise ValueError('Benchmark checkpoint revision changed')
    files=[]
    for row in metadata['siblings']:
        name=row['rfilename']
        if not (name.endswith(('.json','.safetensors','.jinja')) or name.startswith('source/src/') and name.endswith('.py')):continue
        path=checkpoint/name
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        expected=row.get('lfs',{}).get('sha256')
        if expected and digest!=expected:raise ValueError('Model digest differs from published snapshot')
        files.append({'path':name,'size':path.stat().st_size,'sha256':digest})
    manifest=runtime/'model-manifest.json'
    atomic_json(manifest,{'model':MODEL,'revision':REVISION,'files':files})
    worker=ROOT/'tools/sw-kbase/perplexity-worker.py';cfgpath=runtime/'config.json'
    cfg={'backend':'perplexity-flash','database':str(args.database.resolve()),'selection_database':str(args.selection.resolve()),
        'checkpoint':str(checkpoint),'checkpoint_wsl':wsl(checkpoint),'manifest':str(manifest),'manifest_wsl':wsl(manifest),
        'gpu_uuid':'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52','distribution':'Ubuntu-22.04',
        'python_wsl':python_wsl,
        'python_windows':args.python_windows,'worker_script':str(worker),'worker_script_wsl':wsl(worker),
        'config_wsl':wsl(cfgpath),'worker_state':str(runtime/'worker-state.json'),'worker_state_wsl':wsl(runtime/'worker-state.json'),
        'control_root':str(runtime),'wait_marker':str(runtime/'WAIT_FOR_USER_GO'),
        'gpu_request_markers':[str(runtime/'gpu5090.request'),str(args.database.parent/'gpu5090.request')],
        'hf_home_wsl':wsl(args.benchmark/'hf-cache'),
        'torch_cache_wsl':torch_cache,
        'versions':json.loads((args.benchmark/'cpu-validation.json').read_text())['versions'],
        'active_pointer':str(args.control_base/'active-reference-workflow.json'),
        'policy':{'redundant':'no > 80','uncertain':'keep','progressive_coverage':True,'global_vote':False,
            'strata':False,'embeddings':False,'old_votes_reused':False},
        'tokenizer_transformers_version':importlib.metadata.version('transformers')}
    cfg['identity']=runtime_identity(cfg);atomic_json(cfgpath,cfg)
    Path(cfg['wait_marker']).write_text('Prepared only. Await the next explicit user go before removal.\n',encoding='utf-8')
    client=PerplexityParagraphClient(cfg,verify_weights=False)
    cases=json.loads((args.benchmark/'cases.json').read_text(encoding='utf-8'))['cases']
    counts=[]
    for case in cases:
        ids=client.encoder.encode({k:case['wire'][k] for k in ('state','questions')})
        counts.append({'case':case['name'],'tokens':len(ids)})
    atomic_json(runtime/'workflow.json',{'stage':'configured_awaiting_user_go','pid':None,'model':MODEL,
        'gpu_uuid':cfg['gpu_uuid'],'enrichment_output':str(args.selection.parent),'strata_started':False,'inference_started':False})
    atomic_json(runtime/'preparation.json',{'ready':True,'gpu_initialized':False,'model_calls':0,'token_counts':counts,
        'requires_explicit_user_go':True,'created':time.time()})
    print(json.dumps({'config':str(cfgpath),'CPU_preflight':counts,'started':False}))

if __name__=='__main__':main()
