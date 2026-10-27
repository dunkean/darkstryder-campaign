"""HTTP review UI with small local fixtures; no model or corpus access."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.service import make_server
from packages.sw_kbase.store import Store, dumps


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / 'review.sqlite'
        store = Store(cls.db)
        cls.source = store.source('fiche', 'Fiches test')
        cls.body = '<script>alert(1)</script>\n' + 'Texte long. ' * 900
        cls.a, _ = store.ingest(cls.source, 'a', '<img src=x onerror=alert(1)>', cls.body, scope={'edition': '2E'})
        cls.b, _ = store.ingest(cls.source, 'b', 'Sujet B', 'Passage B', scope={'edition': 'REUP'})
        cls.c, _ = store.ingest(cls.source, 'c', 'Sujet C', 'Passage C')
        cls.binary = store.enqueue('identity', cls.a, cls.b, context={'evidence': 'Passage exact'})
        group = {'task': 'identity_tuple', 'record_ids': [cls.a, cls.b, cls.c],
                 'versions': {rid: store.record(rid)['current_version'] for rid in (cls.a, cls.b, cls.c)},
                 'context': {'family': {'name': 'test'}}, 'proposal_ids': [cls.binary]}
        cls.group = store.enqueue('identity_tuple', cls.a, context={'group': group})
        choice = {'task': 'mention_choice', 'record_ids': [cls.a, cls.b],
                  'versions': {rid: store.record(rid)['current_version'] for rid in (cls.a, cls.b)},
                  'subject_record_id': cls.a,
                  'candidates': [{'record_id': cls.b, 'entity_id': store.record(cls.b)['entity_id']}],
                  'context': {'occurrence': {'passage': 'Passage exact : Sujet B est cité.'}}}
        cls.choice = store.enqueue('mention_choice', cls.a, context={'group': choice})
        store.db.execute('INSERT INTO proposal_inputs VALUES(?,?,?)', (cls.group, cls.c, store.record(cls.c)['current_version']))
        store.db.execute('INSERT INTO annotations VALUES(?,?,?,?)', (cls.a, 'type', cls.binary, dumps('person')))
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS aggregates(id TEXT PRIMARY KEY,key TEXT UNIQUE,name TEXT,kind TEXT,universe TEXT,anchor_record_id TEXT,warnings_json TEXT,run_id TEXT);
        CREATE TABLE IF NOT EXISTS aggregate_members(record_id TEXT PRIMARY KEY,aggregate_id TEXT,version_id TEXT,match_key TEXT,rule TEXT);
        CREATE TABLE IF NOT EXISTS aggregate_aliases(form TEXT,aggregate_id TEXT,label TEXT,PRIMARY KEY(form,aggregate_id));
        CREATE TABLE IF NOT EXISTS aggregate_links(subject_id TEXT,object_id TEXT,kind TEXT,document_count INTEGER,occurrences INTEGER,flags_json TEXT,PRIMARY KEY(subject_id,object_id,kind));
        CREATE TABLE IF NOT EXISTS aggregate_link_evidence(id TEXT PRIMARY KEY,subject_id TEXT,object_id TEXT,kind TEXT,record_id TEXT,version_id TEXT,offset_start INTEGER,excerpt TEXT,rule TEXT,context_json TEXT);
        ''')
        store.db.execute('INSERT INTO cleaning_runs VALUES(?,?,?,?)', ('run-test', 'fixture', '2026-01-01', dumps({})))
        store.db.executemany('INSERT INTO aggregates VALUES(?,?,?,?,?,?,?,?)', [
            ('agg-doctor', 'doctor a', 'Doctor A', 'person', 'star_wars', cls.a, dumps(['kind_conflict']), 'run-test'),
            ('agg-equipment', 'sensor', 'Sensor', 'equipment', 'star_wars', cls.c, dumps([]), 'run-test')])
        for rid, aid in ((cls.a, 'agg-doctor'), (cls.b, 'agg-doctor'), (cls.c, 'agg-equipment')):
            store.db.execute('INSERT INTO aggregate_members VALUES(?,?,?,?,?)',
                             (rid, aid, store.record(rid)['current_version'], 'doctor a', 'normalized_name'))
        store.db.executemany('INSERT INTO aggregate_aliases VALUES(?,?,?)',
                             [('doctor a', 'agg-doctor', 'Doctor A'), ('dr a', 'agg-doctor', 'Dr A'),
                              ('sensor', 'agg-equipment', 'Sensor')])
        store.db.execute('INSERT INTO aggregate_links VALUES(?,?,?,?,?,?)',
                         ('agg-doctor', 'agg-equipment', 'has_item', 1, 2, dumps(['approximate_equipment'])))
        store.db.execute('INSERT INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                         ('evidence-1', 'agg-doctor', 'agg-equipment', 'has_item', cls.a,
                          store.record(cls.a)['current_version'], 12, '<script>preuve</script>',
                          'exact_mention', dumps({'source': 'fixture'})))
        store.db.executemany('INSERT INTO aggregate_sections VALUES(?,?,?)',
                             [('agg-doctor','reference',2),('agg-equipment','scenario',1)])
        store.db.execute('INSERT INTO aggregate_text_blocks VALUES(?,?,?,?)',('agg-doctor','0'*64,0,cls.body[:20]))
        store.db.execute('INSERT INTO aggregate_text_sources VALUES(?,?,?,?,?,?)',
                         ('agg-doctor','0'*64,cls.a,store.record(cls.a)['current_version'],0,20))
        store.db.commit()
        store.close()
        cls.server = make_server(cls.db, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            connection.close()

    def data(self, path):
        status, _, raw = self.get(path)
        self.assertEqual(status, 200, raw[:300])
        return json.loads(raw)

    def test_static_ui_security_and_escaping(self):
        status, headers, html = self.get('/review')
        self.assertEqual(status, 200)
        self.assertIn(b'Questions', html)
        self.assertIn('default-src', headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.get('/review', {'Origin': 'https://evil.test'})[0], 403)
        js = self.get('/review/ui.js')[2]
        self.assertIn(b'textContent', js)
        self.assertNotIn(b'innerHTML', js)

    def test_questions_filters_and_group_contract(self):
        items = self.data('/review/api/questions?status=pending&task=identity&limit=1')
        self.assertEqual([i['id'] for i in items['items']], [self.binary])
        self.assertFalse(items['has_more'])
        self.assertEqual(len(self.data('/review/api/questions?source_id=' + self.source)['items']), 3)
        self.assertEqual(len(self.data('/review/api/questions?scope=2E')['items']), 3)
        q = self.data('/review/api/questions/' + self.binary)
        self.assertIn('TARGET', q['openjev']['context'])
        self.assertIn('same_entity', q['openjev']['criteria'])
        self.assertEqual(q['documents'][0]['annotations'][0]['value'], 'person')
        group = self.data('/review/api/questions/' + self.group)
        self.assertEqual(group['task'], 'identity_tuple')
        self.assertEqual(len(group['documents']), 3)
        self.assertIn('all_same', group['openjev']['criteria'])
        self.assertEqual(len(group['openjev']['context']['RECORDS']), 3)
        choice = self.data('/review/api/questions/' + self.choice)
        self.assertIn('c0', choice['openjev']['criteria'])
        self.assertEqual(choice['openjev']['context']['MENTION']['passage'], 'Passage exact : Sujet B est cité.')

    def test_document_search_and_long_text(self):
        docs = self.data('/review/api/documents?q=Texte&limit=1')
        self.assertEqual(docs['items'][0]['id'], self.a)
        record = self.data('/review/api/documents/' + self.a)
        self.assertEqual(record['name'], '<img src=x onerror=alert(1)>')
        self.assertEqual(record['scope']['edition'], '2E')
        text, offset = '', 0
        while True:
            page = self.data(f'/passages?record_id={self.a}&offset={offset}&limit=2')
            text += ''.join(p['text'] for p in page['passages'])
            if not page['pagination']['next_url']:
                break
            offset += len(page['passages'])
        self.assertEqual(text, self.body)
        self.assertEqual(self.get('/review/api/documents/' + self.a, {'Host': 'bad.test'})[0], 403)

    def test_entities_connect_documents(self):
        items = self.data('/review/api/entities?q=Sujet&limit=1')
        self.assertEqual(len(items['items']), 1)
        entity_id = items['items'][0]['id']
        entity = self.data('/review/api/entities/' + entity_id)
        self.assertEqual(entity['documents'][0]['entity_id'], entity_id)

    def test_browse_aggregates_aliases_links_and_evidence(self):
        status, _, html = self.get('/review')
        self.assertEqual(status, 200)
        self.assertIn('Naviguer la base'.encode(), html)
        types = self.data('/review/api/browse/types')
        self.assertTrue(types['available'])
        self.assertEqual({r['kind']: r['count'] for r in types['types']}, {'person': 1, 'equipment': 1})
        self.assertEqual(self.data('/review/api/browse/aggregates?q=Dr&kind=person')['items'][0]['id'], 'agg-doctor')
        a = self.data('/review/api/browse/aggregates/agg-doctor?limit=1')
        self.assertEqual(a['key'], 'doctor a')
        self.assertEqual(a['warnings'], ['kind_conflict'])
        self.assertTrue(a['members']['has_more'])
        self.assertEqual(a['outgoing']['items'][0]['other_id'], 'agg-equipment')
        self.assertEqual(a['outgoing']['items'][0]['flags'], ['approximate_equipment'])
        next_members = self.data('/review/api/browse/aggregates/agg-doctor?section=members&offset=1&limit=1')
        self.assertFalse(next_members['members']['has_more'])
        evidence = self.data('/review/api/browse/evidence?subject_id=agg-doctor&object_id=agg-equipment&kind=has_item')
        self.assertEqual(evidence['items'][0]['excerpt'], '<script>preuve</script>')
        self.assertEqual(self.data('/review/api/documents/' + self.a)['aggregate_id'], 'agg-doctor')

    def test_sections_duplicate_filter_merged_text_and_coverage(self):
        scoped=self.data('/review/api/browse/aggregates?section=scenario')
        self.assertEqual([x['id'] for x in scoped['items']],['agg-equipment'])
        duplicates=self.data('/aggregates?duplicates=1')
        self.assertEqual(duplicates['items'][0]['member_count'],2)
        self.assertEqual(len(duplicates['items']),1)
        types=self.data('/review/api/browse/types?section=reference')
        self.assertEqual(types['types'],[{'kind':'person','count':1}])
        merged=self.data('/aggregates/agg-doctor/text?limit=1')
        self.assertEqual(merged['items'][0]['text'],self.body[:20])
        self.assertEqual(merged['items'][0]['sources'][0]['record_id'],self.a)
        self.assertEqual(self.data('/review/api/browse/aggregates/agg-doctor/text')['items'],merged['items'])
        coverage=self.data('/review/api/coverage')
        self.assertEqual(coverage['documents'],3)
        self.assertEqual(coverage['multiple_document_aggregates'],1)

    def test_native_jobs_and_previews_are_separate_from_legacy_results(self):
        from packages.sw_kbase.curation_store import initialize,plan
        store=Store(self.db)
        try:
            initialize(store)
            store.db.execute("INSERT OR REPLACE INTO meta VALUES('active_cleaning_run','run-test')")
            store.db.commit()
            value=plan(store,{'model':'test','runtime_path':'C:/private/runtime'},aggregate_ids=['agg-doctor'])
            job=store.db.execute('SELECT id FROM jev_v1_jobs WHERE run_id=?',(value['run_id'],)).fetchone()[0]
            store.db.execute('INSERT INTO jev_v1_previews VALUES(?,?,?,?,?,?)',
                (job,1,dumps(['source_contribution']),dumps({'state':{'SOURCE':{'body':'Exact source passage.'}},'questions':{}}),dumps({'source_contribution':500}),'awaiting_jev'))
            store.db.commit()
        finally:store.close()
        jobs=self.data('/review/api/curation/jobs?aggregate_id=agg-doctor')
        self.assertEqual(jobs['items'][0]['id'],job)
        self.assertEqual(jobs['items'][0]['model_calls'],0)
        self.assertNotIn('C:/private',json.dumps(jobs))
        self.assertFalse(jobs['legacy_decisions_reused'])
        previews=self.data('/review/api/curation/previews?job_id='+job)
        self.assertEqual(previews['items'][0]['request']['state']['SOURCE']['body'],'Exact source passage.')
        self.assertFalse(previews['items'][0]['inference_performed'])


if __name__ == '__main__':
    unittest.main()
