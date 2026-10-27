"""Native selection persistence, replay, revision rejection and fail-closed budgets."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.curation_store import plan,execute,status,prepare_documents,revise_plan
from packages.sw_kbase.store import Store


def response(request,*,redundant=False):
    return {'answers':{task:{'probabilities':{'yes':.005 if redundant else .99,'no':.99 if redundant else .005,'unknown':.005}}
                       for task in request['questions']}}


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'knowledge.sqlite');self.ids=[]
        source=self.store.source('test','Test')
        for key,body in [('a','A reference fact.'),('b','A new fact.'),('c','A second new fact.')]:
            rid,_=self.store.ingest(source,key,'Lancer Brunou',body=body,kind='person');self.ids.append(rid)
        self.store.db.commit();clean(self.store)
        self.aid=self.store.db.execute('SELECT id FROM aggregates').fetchone()[0]
        self.identity={'model':'trained-test','weights':'abc','quant':'q8','template':'abc','runtime':'abc'}
    def tearDown(self):self.store.close();self.temp.cleanup()
    def create(self):
        value=plan(self.store,self.identity,aggregate_ids=[self.aid])
        return self.store.db.execute('SELECT id FROM jev_v1_jobs WHERE run_id=?',(value['run_id'],)).fetchone()[0]
    def test_progressive_coverage_persists_without_changing_originals(self):
        job=self.create();requests=[]
        original=[tuple(r) for r in self.store.db.execute('SELECT * FROM records ORDER BY id')]
        def evaluate(request):requests.append(request);return response(request)
        result=execute(self.store,job,evaluate,self.identity,check_pause=False)
        self.assertEqual(len(requests),2)
        self.assertEqual(len({u['record_id'] for u in requests[1]['state']['RETAINED_SOURCES']}),2)
        self.assertEqual(len(result['retained_ids']),3)
        self.assertEqual(original,[tuple(r) for r in self.store.db.execute('SELECT * FROM records ORDER BY id')])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM decision_history').fetchone()[0],0)
        self.assertEqual(status(self.store)['run']['status'],'complete')
    def test_separate_run_reuses_only_exact_native_requests(self):
        first=self.create();calls=[]
        evaluate=lambda request:(calls.append(request) or response(request))
        execute(self.store,first,evaluate,self.identity,check_pause=False)
        second=self.create();result=execute(self.store,second,evaluate,self.identity,check_pause=False)
        self.assertEqual(len(calls),2);self.assertEqual(result['cache_hits'],2)
        self.assertEqual(result['model_calls'],0)
        changed={**self.identity,'quant':'q4'}
        with self.assertRaisesRegex(ValueError,'runtime identity'):execute(self.store,second,evaluate,changed,check_pause=False)
    def test_changed_original_rejects_job_before_inference(self):
        job=self.create();record=self.store.record(self.ids[0])
        self.store.ingest(record['source_id'],record['source_key'],record['name'],body='Revised facts.',kind='person');self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'stale'):execute(self.store,job,lambda request:self.fail('No inference for stale inputs'),self.identity,check_pause=False)
    def test_pause_is_enforced(self):
        job=self.create();(self.root/'openjev.pause').write_text('paused')
        with self.assertRaisesRegex(ValueError,'paused'):execute(self.store,job,lambda request:self.fail('Paused'),self.identity)
    def test_token_budget_deferral_preserves_every_source(self):
        job=self.create()
        result=execute(self.store,job,lambda request:{'deferred_reason':'token_budget_exceeded'},self.identity,check_pause=False)
        self.assertEqual(len(result['retained_ids']),3);self.assertEqual(result['model_calls'],0)
        self.assertTrue(all(not o['inference_performed'] for o in result['observations']))
        self.assertTrue(all(s['merge_selection']=='retain' for s in result['source_selection']))
    def test_crash_replays_cache_and_does_not_publish_partial_selection(self):
        job=self.create();calls=[]
        def fail(request):
            calls.append(request)
            if len(calls)==2:raise RuntimeError('Server stopped')
            return response(request)
        with self.assertRaisesRegex(RuntimeError,'Server stopped'):execute(self.store,job,fail,self.identity,check_pause=False)
        row=self.store.db.execute('SELECT status,result_json FROM jev_v1_jobs WHERE id=?',(job,)).fetchone()
        self.assertEqual(tuple(row),('error',None))
        result=execute(self.store,job,response,self.identity,check_pause=False)
        self.assertEqual(result['cache_hits'],1);self.assertEqual(result['model_calls'],1)
    def test_new_profile_is_kept_even_when_source_text_is_identical(self):
        source=self.store.source('profiles','Profiles')
        self.store.ingest(source,'variant','Lancer Brunou',body='A reference fact.',kind='person',profile={'dexterity':'4D'})
        self.store.db.commit();clean(self.store)
        job=self.create();result=execute(self.store,job,lambda request:response(request,redundant=True),self.identity,check_pause=False)
        self.assertTrue(any(row['reason']=='protected_information' for row in result['source_selection']))

    def test_preparation_never_populates_inference_cache_and_remains_executable(self):
        job=self.create()
        run=self.store.db.execute('SELECT run_id FROM jev_v1_jobs WHERE id=?',(job,)).fetchone()[0]
        prepared=prepare_documents(self.store,run,preflight=lambda request:{task:500 for task in request['questions']})
        self.assertEqual(prepared['model_calls'],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jev_v1_requests').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jev_v1_previews').fetchone()[0],2)
        self.assertEqual(self.store.db.execute('SELECT status FROM jev_v1_jobs WHERE id=?',(job,)).fetchone()[0],'prepared')
        result=execute(self.store,job,response,self.identity,check_pause=False)
        self.assertEqual(result['model_calls'],2)

    def test_revised_plans_archive_old_selections_and_have_distinct_ids(self):
        job=self.create();execute(self.store,job,response,self.identity,check_pause=False)
        previous=self.store.db.execute('SELECT run_id FROM jev_v1_jobs WHERE id=?',(job,)).fetchone()[0]
        first=revise_plan(self.store,previous);second=revise_plan(self.store,previous)
        self.assertNotEqual(first,second)
        self.assertEqual(self.store.db.execute('SELECT status FROM jev_v1_jobs WHERE id=?',(job,)).fetchone()[0],'complete')
        self.assertEqual(self.store.db.execute('SELECT status,result_json FROM jev_v1_jobs WHERE run_id=?',(first,)).fetchone()[:],('prepared',None))


if __name__=='__main__':unittest.main()
