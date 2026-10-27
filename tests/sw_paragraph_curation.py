"""Synthetic paragraph fixtures only: no real corpus, GPU or inference server."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.curation_store import plan as plan_documents,execute
from packages.sw_kbase.paragraph_curation import plan,run,paragraphs,metadata_only,fact_numbers
from packages.sw_kbase.store import Store


class FakeClient:
    model='fixture';identity={'model':'fixture','weights':'fixture'}
    def __init__(self):self.calls=[]
    def validate_runtime(self):return self.identity
    def __call__(self,request):
        self.calls.append(request)
        return {'answers':{task:{'probabilities':{'yes':.005,'no':.99,'unknown':.005}} for task in request['questions']}}


class ParagraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'main.sqlite')
        sid=self.store.source('fixture','Fixture');self.records=[]
        for key,body in [('a','The pilot has a ship.\n\nThe pilot guards a base.'),('b','The pilot commands a ship.\n\nThe pilot guards a base.\n\n<< Previous Page')]:
            rid,_=self.store.ingest(sid,key,'Pilot',body,kind='person');self.records.append(rid)
        self.store.db.commit();clean(self.store)
        self.aid=self.store.db.execute('SELECT id FROM aggregates').fetchone()[0]
        self.client=FakeClient()
        self.document_plan=plan_documents(self.store,self.client.identity,aggregate_ids=[self.aid])
        self.job=self.store.db.execute('SELECT id FROM jev_v1_jobs').fetchone()[0]
        # Ensure both documents remain available for the isolated paragraph test.
        def retain(request):return {'answers':{task:{'probabilities':{'yes':.99,'no':.005,'unknown':.005}} for task in request['questions']}}
        execute(self.store,self.job,retain,self.client.identity,check_pause=False)
        self.output=self.root/'paragraph-plan'
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_plan_numbers_exact_paragraphs_without_inference(self):
        value=plan(self.store,self.output,self.document_plan['run_id'])
        self.assertEqual(value['model_calls'],0);self.assertFalse(self.client.calls)
        db=sqlite3.connect(self.output/'paragraphs.sqlite')
        try:
            for rid,start,end,text in db.execute('SELECT document_id,offset_start,offset_end,body FROM paragraphs'):
                self.assertEqual(self.store.record(rid)['body'][start:end],text)
            self.assertEqual(db.execute('SELECT count(*) FROM requests').fetchone()[0],0)
        finally:db.close()
    def test_unfinished_document_pass_cannot_start_next_stage(self):
        self.store.db.execute("UPDATE jev_v1_runs SET status='prepared'");self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Finish'):plan(self.store,self.output,self.document_plan['run_id'])
        self.assertFalse(self.output.exists())
    def test_explicit_fixture_run_selects_paragraphs_and_preserves_originals(self):
        plan(self.store,self.output,self.document_plan['run_id'])
        before=[tuple(r) for r in self.store.db.execute('SELECT * FROM record_versions ORDER BY id')]
        result=run(self.store,self.output,self.client)
        self.assertEqual(result['remaining_entities'],0)
        data=json.loads((self.output/'additions.json').read_text(encoding='utf-8'))
        choices=[p['selection'] for d in data['documents'] for p in d['paragraphs']]
        self.assertIn('skip_redundant',choices)
        self.assertEqual(before,[tuple(r) for r in self.store.db.execute('SELECT * FROM record_versions ORDER BY id')])
        for request in self.client.calls:
            self.assertIn('paragraph_number',request['state']['SOURCE']['source_unit'])
    def test_changed_source_rejects_execution_before_inference(self):
        plan(self.store,self.output,self.document_plan['run_id'])
        record=self.store.record(self.records[0])
        self.store.ingest(record['source_id'],record['source_key'],record['name'],body='Changed.',kind='person');self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'revision'):run(self.store,self.output,self.client)
        self.assertFalse(self.client.calls)

    def test_completed_plan_cannot_be_exported_as_current_after_source_edit(self):
        plan(self.store,self.output,self.document_plan['run_id']);run(self.store,self.output,self.client)
        record=self.store.record(self.records[0])
        self.store.ingest(record['source_id'],record['source_key'],record['name'],body='Edited after completion.',kind='person');self.store.db.commit()
        before=len(self.client.calls)
        with self.assertRaisesRegex(ValueError,'revision'):run(self.store,self.output,self.client)
        self.assertEqual(len(self.client.calls),before)
    def test_numbering_and_explicit_navigation_metadata(self):
        rows=list(paragraphs('  First.\n\nSecond.  '))
        self.assertEqual([r['label'] for r in rows],['P00001','P00002'])
        self.assertEqual([r['body'] for r in rows],['First.','Second.'])
        self.assertTrue(metadata_only('<< Previous Page','Pilot'))
        self.assertFalse(metadata_only('The pilot moved to another planet.','Pilot'))

    def test_citation_pages_do_not_look_like_new_mechanics(self):
        self.assertEqual(fact_numbers('Hull 4D, Move 30. (pp. 12, 16–18)'),{'4d','30'})
        self.assertEqual(fact_numbers('The attack occurred in 42 ABY (p. 99).'),{'42'})
        self.assertIn('4',fact_numbers('Page 4 of the map was stolen.'))


if __name__=='__main__':unittest.main()
