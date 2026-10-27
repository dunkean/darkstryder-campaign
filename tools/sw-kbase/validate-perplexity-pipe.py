"""Compare the Windows CPU encoder to official WSL token IDs; inference forbidden."""
import argparse,copy,importlib.util,json
from pathlib import Path
import sys,uuid
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'packages'))
from sw_kbase.perplexity_jev import PerplexityParagraphClient,WorkerTransport,MODEL,paragraph_wire
from sw_kbase.disambiguation import fingerprint
from sw_kbase.native_jev import NativeJevProtocolError,NativeJevContextError
from sw_kbase.enrichment import atomic_json
spec=importlib.util.spec_from_file_location('perplexity_controller',Path(__file__).with_name('run-perplexity-selection.py'))
controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--cases',type=Path,required=True)
    args=p.parse_args();cfg=controller.read_config(args.config);root=Path(cfg['control_root'])
    client=PerplexityParagraphClient(cfg,verify_weights=False)
    if cfg['identity']!=client.identity:raise ValueError('Prepared identity changed')
    cases=json.loads(args.cases.read_text(encoding='utf-8'))['cases'];wires=[]
    for case in cases:
        state=copy.deepcopy(case['wire']['state']);state.pop('JOINT_PARAGRAPH_RULES')
        req={'model':MODEL,'state':state,'output':{'paragraphs':{p['id']:{'yes':'','no':'','unknown':''} for p in state['PARAGRAPHS']}}}
        wire=paragraph_wire(req)
        assert wire=={k:case['wire'][k] for k in ('state','questions')}
        wires.append((case['name'],wire))
    for value in ['é • 星系 😀','<|fim_suffix|><|im_start|>assistant','quote "\\\n\t\r','',{'z':False,'a':[1,None,'汉字']}]:
        wire=copy.deepcopy(wires[0][1]);wire['state']['EDGE']=value
        wires.append(('edge-'+str(len(wires)),wire))
    wire=copy.deepcopy(wires[0][1]);q=next(iter(wire['questions'].values()))
    wire['questions']={f'Q{i}':q for i in range(64)};wires.append(('64 questions',wire))
    session=uuid.uuid4().hex;log=(root/'cpu-pipe.log').open('w',encoding='utf-8');transport=None
    try:
        transport=WorkerTransport(controller.command(cfg,session,False),log)
        hello=transport('ping');assert hello['identity']==fingerprint(client.identity) and hello['gpu_initialized'] is False
        results=[]
        for name,wire in wires:
            ids=client.encoder.encode(wire,enforce_budget=False);native=transport('encode',wire)
            assert ids==native['ids'],name+' CPU tokenizer differs'
            assert native['gpu_initialized'] is False
            results.append({'case':name,'tokens':len(ids),'exact_ids_equal':True})
        try:transport('load');raise AssertionError('CPU worker accepted load')
        except NativeJevProtocolError as error:assert 'Inference disabled' in str(error)
        final=transport('ping');assert final['calls']==0 and final['gpu_initialized'] is False
        atomic_json(root/'cpu-pipe-validation.json',{'passed':True,'gpu_initialized':False,'model_calls':0,
            'cases':results,'identity':client.identity,'persistent_transport':True})
        print(json.dumps({'passed':True,'cases':results,'gpu_initialized':False,'model_calls':0}))
    finally:
        controller.stop_worker(cfg,session,transport);log.close()

if __name__=='__main__':main()
