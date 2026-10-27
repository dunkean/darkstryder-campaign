"""Document/paragraph review over two read-only databases, no models."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.grouped_paragraphs import plan, score
from packages.sw_kbase.service import make_server
from packages.sw_kbase.store import Store


class JointFixture:
    model = 'fixture'; identity = {'model': 'fixture'}
    capabilities = {'joint_paragraph_scores': True}
    def preflight(self, request):
        return {}
    def validate_runtime(self):
        return self.identity
    def __call__(self, request):
        return {'inference_passes': 1, 'paragraphs': {p['id']: {'yes': 96, 'no': 2, 'unknown': 2}
            for p in request['state']['PARAGRAPHS']}}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.database = self.root / 'knowledge.sqlite'; self.output = self.root / 'selection'
        main = Store(self.database)
        source = main.source('phase3/ref', 'Extraction de référence')
        self.reference, _ = main.ingest(source, 'ref', 'Pilot', 'Pilot flies a ship.', kind='person',
            scope={'source_title': 'Livre A'}, locator={'source_id': 'book-A', 'file': 'C:/private/book.pdf', 'profile_pages': [5, 6]})
        source = main.source('phase3/other', 'Autre extraction')
        self.candidate, _ = main.ingest(source, 'other', 'Pilot', 'Pilot flies a ship.\n\nPilot owns a hidden base.',
            kind='person', scope={'source_title': 'Livre A'}, locator={'source_id': 'book-A', 'file': 'C:/private/extraction.md', 'profile_pages': [7]})
        source = main.source('web/d6db-compendium', 'D6DB import')
        self.web, _ = main.ingest(source, 'web', 'Pilot', 'Pilot guards a temple.', kind='person',
            locator={'url': 'https://d6db.com/compendium/npcs/pilot', 'file': 'C:/private/scraped.jsonl'})
        main.db.commit(); clean(main)
        self.aggregate = main.db.execute('SELECT aggregate_id FROM aggregate_members WHERE record_id=?', (self.reference,)).fetchone()[0]
        plan(main, self.output, config={'source_priorities': {'phase3/ref': 1000}})
        score(main, self.output, JointFixture()); main.close()
        import sqlite3
        db = sqlite3.connect(self.output/'enrichment.sqlite')
        # A source can have cached work without a published document selection.
        db.execute("UPDATE documents SET status='pending',result_json=NULL WHERE id=?", (self.web,))
        db.execute("UPDATE paragraphs SET status='pending',result_json=NULL WHERE document_id=?", (self.web,))
        db.execute("UPDATE entities SET status='planned'"); db.commit(); db.close()
        self.server = make_server(self.database, 0, self.output/'enrichment.sqlite')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.database, self.output/'enrichment.sqlite')]
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()
    def get(self, path, expected=200):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            c.request('GET', path); response = c.getresponse(); value = json.loads(response.read())
            self.assertEqual(response.status, expected, value)
            return value
        finally:
            c.close()
    def test_reference_sources_and_stages_are_distinct(self):
        value = self.get('/review/api/workspace/' + self.aggregate + '?limit=100')
        self.assertEqual(value['counts']['records'], 3); self.assertEqual(value['counts']['web_records'], 1)
        self.assertEqual(value['reference']['id'], self.reference)
        docs = {d['id']: d for d in value['documents']['items']}
        self.assertEqual(docs[self.reference]['source_label'], 'Livre A')
        self.assertEqual(docs[self.reference]['page_label'], 'p. 5–6')
        self.assertEqual(docs[self.reference]['source_group_id'], docs[self.candidate]['source_group_id'])
        self.assertEqual(docs[self.web]['source_type'], 'web')
        self.assertTrue(docs[self.web]['source_url'].startswith('https://d6db.com/'))
        self.assertEqual(docs[self.web]['selection']['status'], 'pending')
        self.assertEqual(docs[self.candidate]['selection']['status'], 'scored')
        self.assertNotIn('C:/private', json.dumps(value))
    def test_reference_remains_visible_across_source_pagination(self):
        value = self.get('/review/api/workspace/' + self.aggregate + '?offset=1&limit=1')
        self.assertEqual(len(value['documents']['items']), 1)
        self.assertEqual(value['reference']['id'], self.reference)
        self.assertTrue(value['documents']['has_more'])
    def test_paragraphs_belong_to_their_source_and_keep_actual_decisions(self):
        value = self.get('/review/api/workspace/documents/' + self.candidate + '/paragraphs')
        self.assertEqual([p['status'] for p in value['items']], ['skip_redundant', 'add'])
        self.assertTrue(value['items'][0]['exact_proof'])
        self.assertEqual(value['items'][0]['scores_pct']['no'], 2)  # Native score not fabricated.
        retained = self.get('/review/api/workspace/documents/' + self.candidate + '/paragraphs?status=retained')
        self.assertEqual(len(retained['items']), 1)
        pending = self.get('/review/api/workspace/documents/' + self.web + '/paragraphs')
        self.assertIsNone(pending['items'][0]['scores_pct'])
        self.assertEqual(pending['items'][0]['request_ids'], [])
        request_id = value['items'][1]['request_ids'][0]
        request = self.get('/review/api/workspace/documents/' + self.candidate + '/requests/' + request_id)
        self.assertEqual(request['request']['state']['SOURCE']['id'], self.candidate)
        self.get('/review/api/workspace/documents/' + self.web + '/requests/' + request_id, 404)
    def test_fallback_does_not_invent_a_chosen_reference_or_scores(self):
        self.server.selection_database = None
        value = self.get('/review/api/workspace/' + self.aggregate)
        self.assertIsNone(value['reference']); self.assertFalse(value['selection']['available'])
        value = self.get('/review/api/workspace/documents/' + self.candidate + '/paragraphs')
        self.assertFalse(value['selection_available'])
        self.assertTrue(all(p['status'] == 'pending' and p['scores_pct'] is None for p in value['items']))
    def test_changed_version_is_stale_and_does_not_show_old_scores(self):
        main = Store(self.database); record = main.record(self.candidate)
        main.ingest(record['source_id'], record['source_key'], record['name'], 'Changed source text.', kind='person')
        main.db.commit(); main.close()
        value = self.get('/review/api/workspace/documents/' + self.candidate + '/paragraphs')
        self.assertEqual(value['document']['selection']['status'], 'stale')
        self.assertFalse(value['selection_available']); self.assertEqual(value['items'][0]['body'], 'Changed source text.')
    def test_http_review_never_changes_either_database(self):
        self.get('/review/api/workspace/' + self.aggregate)
        self.get('/review/api/workspace/documents/' + self.reference + '/paragraphs')
        self.get('/review/api/workspace/documents/' + self.web + '/paragraphs?status=bad', 400)
        self.assertEqual(self.hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.database, self.output/'enrichment.sqlite')])


if __name__ == '__main__':
    unittest.main()
