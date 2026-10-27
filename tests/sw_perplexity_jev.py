"""Protocol/failure tests only; no model, CUDA, database corpus or inference."""
import copy,json,math,importlib.util
from pathlib import Path
import subprocess,sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from packages.sw_kbase.perplexity_jev import PerplexityParagraphClient,MODEL,paragraph_wire
from packages.sw_kbase.native_jev import NativeJevContextError,NativeJevProtocolError,NativeJevIdentityError
from sw_joint_jev import request as fixture

def request(count=4,tokens=500):
    row=fixture(count,tokens=tokens);row['model']=MODEL;return row

class Encoder:
    def encode(self,wire):
        n=wire['state']['__tokens']
        if n>4096:raise NativeJevContextError('over budget',reason='context_budget')
        return [0]*n

class Transport:
    process=SimpleNamespace(poll=lambda:None)
    def __init__(self):self.calls=[];self.edit=lambda x:x
    def __call__(self,command,payload):
        self.calls.append((command,copy.deepcopy(payload)))
        return self.edit({'identity':payload['identity'],'response':{'usage':{'sequences':1,'output_tokens':0,
            'input_tokens':payload['request']['state']['__tokens']},'answers':{qid:{'type':'choice','choice':'yes',
            'probabilities':{'yes':.85,'no':.1,'unknown':.05}} for qid in payload['request']['questions']}}})

class Tests(unittest.TestCase):
    def setUp(self):
        self.client=object.__new__(PerplexityParagraphClient);self.client.files={};self.client._identity={'fixture':'test'}
        self.client.encoder=Encoder();self.client.transport=Transport()
    def test_one_forward_for_whole_document_and_native_probabilities(self):
        source=request(12);before=copy.deepcopy(source);response=self.client(source)
        self.assertEqual(source,before);self.assertEqual(len(self.client.transport.calls),1)
        wire=self.client.transport.calls[0][1]['request'];self.assertEqual(len(wire['questions']),12)
        self.assertNotIn('global',wire['questions']);self.assertEqual(response['inference_passes'],1)
        self.assertTrue(all(v['yes']==85 for v in response['paragraphs'].values()))
    def test_global_budget_inclusive_and_overflow_has_no_transport(self):
        self.assertEqual(self.client.preflight(request(tokens=4096))['total_tokens'],4096)
        with self.assertRaises(NativeJevContextError):self.client(request(tokens=4097))
        self.assertFalse(self.client.transport.calls)
    def test_missing_outputs_duplicate_ids_empty_evidence_rejected(self):
        cases=[];x=request();x['state']['PARAGRAPHS'][1]['id']='P1';cases.append(x)
        x=request();x['state']['PARAGRAPHS'][0]['body']='';cases.append(x)
        x=request();del x['output']['paragraphs']['P1'];cases.append(x)
        for case in cases:
            with self.assertRaises((ValueError,NativeJevContextError)):self.client(case)
        self.assertFalse(self.client.transport.calls)
    def test_split_missing_generated_or_malformed_responses_never_become_votes(self):
        def split(x):x['response']['usage']['sequences']=2;return x
        def generated(x):x['response']['usage']['output_tokens']=1;return x
        def tokens(x):x['response']['usage']['input_tokens']=3;return x
        def missing(x):x['response']['answers'].pop('P1');return x
        def nan(x):x['response']['answers']['P1']['probabilities']['yes']=math.nan;return x
        for edit in [split,generated,tokens,missing,nan]:
            self.client.transport.edit=edit
            with self.assertRaises(NativeJevProtocolError):self.client(request())
    def test_changed_identity_rejected(self):
        def bad(x):x['identity']='wrong';return x
        self.client.transport.edit=bad
        with self.assertRaises(NativeJevIdentityError):self.client(request())
    def test_cpu_only_client_cannot_infer(self):
        self.client.transport=None
        with self.assertRaisesRegex(NativeJevProtocolError,'CPU-only'):self.client(request())
    def test_explicit_execute_still_blocked_by_wait_marker_before_opening_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'WAIT';marker.touch();cfg=root/'config.json'
            cfg.write_text(json.dumps({'gpu_uuid':'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52',
                'identity':{'gpu_uuid':'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52'},'wait_marker':str(marker)}))
            result=subprocess.run([sys.executable,'tools/sw-kbase/run-perplexity-selection.py','--config',str(cfg),'--execute'],capture_output=True,text=True)
            self.assertEqual(result.returncode,2);self.assertIn('Awaiting the next explicit user go',result.stderr)
            self.assertEqual(set(p.name for p in root.iterdir()),{'WAIT','config.json'})

class PriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('selection_controller',Path('tools/sw-kbase/run-perplexity-selection.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
    def controller(self,root,processes,worker):
        c=object.__new__(self.module.Controller);c.session='ours';c.output=root;c.pause=root/'openjev.pause'
        c.pending=None;c.write_lock=__import__('threading').Lock();c.cfg={'worker_state':str(root/'worker.json')}
        (root/'worker.json').write_text(json.dumps(worker));c.baseline={};c.check=lambda:None;c.stopped=False
        c.worker_status=worker;c.transport=None;c.stage='selecting_paragraphs'
        c.stop_worker=lambda:setattr(c,'stopped',True)
        c.probe=SimpleNamespace(read=lambda:{'processes':processes,'free_mib':31000,'used_mib':1000})
        class Once:
            n=0
            def wait(self,n):self.n+=1;return self.n>1
        c.finished=Once();return c
    def test_foreign_compute_yields_and_restores_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.controller(root,[{'pid':7,'name':'python.exe','dedicated_bytes':100*1048576,'engine_percent':50}],{})
            c.guard();self.assertTrue(c.stopped);self.assertTrue(c.pause.exists());self.assertEqual(c.pending['reason'],'foreign_5090_activity')
    def test_wsl_vm_only_exempted_for_the_current_worker_session(self):
        for session,paused in [('ours',False),('other',True)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);c=self.controller(root,[{'pid':7,'name':'vmwp.exe','dedicated_bytes':10000*1048576,'engine_percent':50}],{'session':session,'stage':'ready'})
                c.guard();self.assertEqual(c.stopped,paused)
    def test_worker_foreign_gpu_observation_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=self.controller(root,[],{'session':'ours','stage':'ready','yield':{'reason':'foreign_5090_compute'}})
            c.guard();self.assertTrue(c.stopped);self.assertEqual(c.pending['reason'],'foreign_5090_compute')
    def test_monitor_does_not_open_the_wsl_telemetry_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.controller(Path(tmp),[],{'session':'ours','stage':'ready'})
            with patch.object(Path,'read_text',side_effect=AssertionError('Do not open WSL journal')):c.guard()
            self.assertFalse(c.stopped);self.assertIsNone(c.pending)

class WorkerMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('perplexity_worker',Path('tools/sw-kbase/perplexity-worker.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
    def monitor(self,samples):
        w=object.__new__(self.module.Worker);w.external_baseline=None;w.cfg={'gpu_uuid':'fixture'}
        w.state={'stage':'warming','yield':None};cursor=[-1]
        def wait(_):
            cursor[0]+=1
            if cursor[0]>=len(samples):return True
            w.state['stage']=samples[cursor[0]][0];return False
        w.done=SimpleNamespace(wait=wait)
        w.torch=SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda:True,memory_reserved=lambda:9000*1048576,
            mem_get_info=lambda:((32000-9000-samples[cursor[0]][1])*1048576,32000*1048576)))
        w.update=lambda **values:w.state.update(values)
        with patch.object(self.module.subprocess,'run',return_value=SimpleNamespace(stdout='')):w.monitor()
        return w
    def test_cuda_graph_warmup_growth_is_not_foreign_memory(self):
        w=self.monitor([('warming',700),('warming',1600),('warming',2400),('ready',2400),('scoring',2410)])
        self.assertIsNone(w.state['yield']);self.assertEqual(w.external_baseline,2400)
    def test_growth_after_warmup_yields_after_two_samples(self):
        w=self.monitor([('ready',2400),('scoring',2700),('ready',2710)])
        self.assertEqual(w.state['yield']['reason'],'foreign_5090_compute')

class JournalRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('perplexity_launch',Path('tools/sw-kbase/perplexity-launch.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
    def test_temporary_windows_telemetry_lock_is_retried(self):
        calls=[]
        def replace(a,b):
            calls.append((a,b))
            if len(calls)<3:raise PermissionError('temporary reader')
            return 'ok'
        with patch.object(self.module.time,'sleep'):
            self.assertEqual(self.module.replace_with_retry(replace,'worker-state.tmp','worker-state.json'),'ok')
        self.assertEqual(len(calls),3)
    def test_other_writes_are_not_silently_retried(self):
        with patch.object(self.module.time,'sleep') as sleep:
            with self.assertRaises(PermissionError):self.module.replace_with_retry(lambda a,b:(_ for _ in ()).throw(PermissionError()),'a','other.json')
            sleep.assert_not_called()

if __name__=='__main__':unittest.main()
