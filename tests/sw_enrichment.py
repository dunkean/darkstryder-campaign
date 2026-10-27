"""Synthetic only: no network, real corpus, model or GPU is used."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.document_units import split_document, fragments, choose_reference
from packages.sw_kbase.enrichment import plan, score, connect, decision, rollup
from packages.sw_kbase.native_jev import NativeJevContextError, NativeJevClient
from packages.sw_kbase.rich_merge import merge, export, validate_items, RichWriter, combine
from packages.sw_kbase.store import Store


class FakeJev:
    model = 'synthetic'; identity = {'model': 'synthetic', 'weights': 'fixture'}
    def __init__(self, global_no=False, budget=None):
        self.calls = []; self.global_no = global_no; self.budget = budget
    def validate_runtime(self):
        return self.identity
    def preflight(self, request):
        if self.budget and len(json.dumps(request)) > self.budget:
            raise NativeJevContextError('fixture budget', reason='context_budget')
        return {'source_contribution': 100}
    def __call__(self, request):
        self.calls.append(request)
        state = request['state']
        if state['evaluation']['stage'] == 'whole_document':
            no = self.global_no
        else:
            source = state['SOURCE']['body'].strip()
            texts = [state['REFERENCE'].get('body', '')] + [e['body'] for e in state['RETAINED_SOURCES']]
            no = any(source in text for text in texts)
        return {'answers': {'source_contribution': {'probabilities':
            {'yes': .02 if no else .96, 'no': .96 if no else .02, 'unknown': .02}}}}


class FakeWriter:
    identity = {'model': 'writer-fixture', 'revision': '1'}
    chars = 48000; rounds = 1
    def __init__(self):
        self.calls = []
    def fits(self, context):
        return len(json.dumps(context)) < self.chars
    def __call__(self, context):
        self.calls.append(context)
        if 'evidence' in context:
            claims = [{'kind': 'fact', 'text': e['excerpt'], 'fields': {}, 'evidence_ids': [e['id']]} for e in context['evidence']]
        else:
            return {key: [{k: v for k, v in item.items() if k != 'category'} for item in context['items'] if item['category'] == key]
                    for key in ('claims', 'relations', 'conflicts', 'notes')}
        return {'claims': claims, 'relations': [], 'conflicts': [], 'notes': []}


class UnitsTests(unittest.TestCase):
    def test_markdown_structure_and_exact_offsets(self):
        body = '# Pilot\n\nFirst.\n\n- One\n- Two\n\n```json\n{"x":1}\n\n{"x":2}\n```\n'
        rows = split_document(body)
        self.assertTrue(any(p['kind'] == 'code' and '\n\n' in p['body'] for p in rows))
        self.assertTrue(any(p['kind'] == 'heading' for p in rows))
        for p in rows:
            self.assertEqual(body[p['offset_start']:p['offset_end']], p['body'])
        self.assertEqual([p['label'] for p in rows], [f'P{i:05d}' for i in range(1, len(rows)+1)])
    def test_json_members_have_paths_keys_and_exact_offsets(self):
        body = '{"name":"Pilot", "stats":{"hull":"4D","move":30}, "equipment":["blaster","suit"]}'
        rows = split_document(body)
        self.assertEqual([p['path'] for p in rows], ['/name', '/stats', '/equipment'])
        self.assertIn('"stats":', rows[1]['body'])
        for p in rows:
            self.assertEqual(body[p['offset_start']:p['offset_end']], p['body'])
    def test_html_closing_tags_are_not_artificial_paragraphs(self):
        body = '<div><p>First.</p><p>Second.</p></div>'
        rows = split_document(body)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(p['format'] == 'html' for p in rows))
        for p in rows:
            self.assertEqual(body[p['offset_start']:p['offset_end']], p['body'])
    def test_long_paragraph_is_partitioned_without_loss(self):
        p = split_document('Sentence. ' * 500)[0]
        rows = list(fragments(p, 400))
        self.assertEqual(''.join(r['body'] for r in rows), p['body'])
        self.assertTrue(all(len(r['body']) <= 400 for r in rows))
    def test_reference_priority_and_ties_are_explained(self):
        docs = [{'id': 'a', 'name': 'Pilot', 'body': 'Brief.', 'dataset': 'preferred'},
                {'id': 'b', 'name': 'Pilot', 'body': 'More detailed text.'}]
        reference, ranks = choose_reference(docs, {'source_priorities': {'preferred': 1000}})
        self.assertEqual(reference, 'a'); self.assertEqual(ranks['a']['components']['dataset_priority'], 1000)
    def test_eighty_is_not_redundant_and_derived_score_is_labelled(self):
        self.assertEqual(decision({'no': 80}), 'uncertain')
        self.assertEqual(decision({'no': 80.01}), 'redundant')
        result = rollup([{'scores_pct': {'no': 95}}, {'scores_pct': {}}])
        self.assertEqual(result['decision'], 'uncertain'); self.assertIsNone(result['scores_pct'])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.main = Store(self.root/'main.sqlite'); self.output = self.root/'prepared'
        first = self.main.source('preferred', 'Reference')
        other = self.main.source('other', 'Other')
        self.reference, _ = self.main.ingest(first, 'ref', 'Pilot', 'Pilot flies a ship.', kind='person')
        self.candidate, _ = self.main.ingest(other, 'other', 'Pilot',
            'Pilot owns a hidden base.\n\nPilot owns a hidden base.', kind='person')
        self.main.ingest(other, 'singleton', 'Unique object', 'Only source.', kind='equipment')
        self.main.db.commit(); clean(self.main)
        self.aid = self.main.db.execute('SELECT aggregate_id FROM aggregate_members WHERE record_id=?', (self.reference,)).fetchone()[0]
        self.before = [tuple(r) for r in self.main.db.execute('SELECT * FROM record_versions ORDER BY id')]
    def tearDown(self):
        self.main.close(); self.temp.cleanup()
    def prepare(self):
        return plan(self.main, self.output, config={'source_priorities': {'preferred': 1000}})
    def test_all_entries_including_singletons_without_first_run_or_inference(self):
        result = self.prepare()
        self.assertEqual(result['documents'], 3); self.assertEqual(result['entities'], 2)
        self.assertEqual(result['model_calls'], 0)
        db = connect(self.output)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM requests').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT reference_id FROM entities WHERE id=?', (self.aid,)).fetchone()[0], self.reference)
        finally:
            db.close()
    def test_global_and_all_paragraph_votes_and_progressive_coverage(self):
        self.prepare(); client = FakeJev(); score(self.main, self.output, client)
        db = connect(self.output)
        try:
            rows = db.execute('SELECT status FROM paragraphs WHERE document_id=? ORDER BY number', (self.candidate,)).fetchall()
            self.assertEqual([r[0] for r in rows], ['add', 'skip_redundant'])
            paragraph_calls = [c for c in client.calls if c['state']['evaluation']['stage'] == 'paragraph']
            self.assertEqual(len(paragraph_calls), 2)
            self.assertTrue(any('hidden base' in e['body'] for e in paragraph_calls[1]['state']['RETAINED_SOURCES']))
            self.assertEqual(len(client.calls), 3)
            native = NativeJevClient(model=client.model, identity={}, transport=None, token_counter=lambda request: {'source_contribution': 100})
            for request in client.calls:
                native.preflight(request)  # Genuine adapter contract; no transport/model.
        finally:
            db.close()
    def test_new_information_is_not_added_again_in_a_later_document(self):
        sid = self.main.source('third', 'Third')
        third, _ = self.main.ingest(sid, 'third', 'Pilot',
            'Pilot owns a hidden base.\n\nPilot guards a temple.', kind='person')
        self.main.db.commit(); clean(self.main); self.prepare(); score(self.main, self.output, FakeJev())
        db = connect(self.output)
        try:
            rows = db.execute("SELECT status FROM paragraphs WHERE body='Pilot owns a hidden base.' ORDER BY id").fetchall()
            self.assertEqual([r[0] for r in rows].count('add'), 1)
            self.assertEqual([r[0] for r in rows].count('skip_redundant'), 2)
        finally:
            db.close()
    def test_global_no_does_not_skip_paragraph_questions_and_votes_are_ignored(self):
        self.prepare(); client = FakeJev(global_no=True); score(self.main, self.output, client)
        self.assertEqual(len(client.calls), 3)
        db = connect(self.output)
        try:
            self.assertEqual({r[0] for r in db.execute('SELECT status FROM paragraphs WHERE document_id=?', (self.candidate,))}, {'ignored_by_document_vote'})
            result = json.loads(db.execute('SELECT result_json FROM documents WHERE id=?', (self.candidate,)).fetchone()[0])
            self.assertTrue(result['disagreement']); self.assertEqual(result['paragraph_ids_added_to_coverage'], [])
        finally:
            db.close()
    def test_large_docs_use_bounded_spans_and_document_rollup(self):
        record = self.main.record(self.reference)
        self.main.ingest(record['source_id'], record['source_key'], record['name'], 'Reference. '*900, kind='person')
        self.main.db.commit(); clean(self.main); self.prepare()
        client = FakeJev(budget=2700); score(self.main, self.output, client)
        self.assertTrue(client.calls)
        self.assertTrue(all(len(json.dumps(c)) <= 2700 for c in client.calls))
        db = connect(self.output)
        try:
            result = json.loads(db.execute('SELECT result_json FROM documents WHERE id=?', (self.candidate,)).fetchone()[0])
            self.assertEqual(result['basis'], 'all_document_blocks'); self.assertIsNone(result['scores_pct'])
            self.assertTrue(any(c['state']['evaluation']['stage'] == 'whole_document_block' for c in client.calls))
        finally:
            db.close()
    def test_resume_preserves_cache_and_originals(self):
        self.prepare(); score(self.main, self.output, FakeJev())
        client = FakeJev(); result = score(self.main, self.output, client)
        self.assertFalse(client.calls); self.assertEqual(result['remaining_entities'], 0)
        self.assertEqual(self.before, [tuple(r) for r in self.main.db.execute('SELECT * FROM record_versions ORDER BY id')])
    def test_pause_and_stale_inputs_prevent_all_model_calls(self):
        self.prepare(); pause = self.root/'openjev.pause'; pause.write_text('user pause')
        client = FakeJev()
        with self.assertRaisesRegex(ValueError, 'paused'): score(self.main, self.output, client)
        self.assertFalse(client.calls); pause.unlink()
        record = self.main.record(self.reference)
        self.main.ingest(record['source_id'], record['source_key'], record['name'], 'Edited.', kind='person'); self.main.db.commit()
        with self.assertRaises(ValueError): score(self.main, self.output, client)
        self.assertFalse(client.calls)
    def test_rich_merge_is_cited_exported_and_does_not_touch_originals(self):
        self.prepare(); score(self.main, self.output, FakeJev()); writer = FakeWriter()
        result = merge(self.main, self.output, writer); self.assertEqual(result['entities_available'], 2)
        db = connect(self.output)
        try:
            rich = json.loads(db.execute('SELECT result_json FROM rich_entities WHERE id=?', (self.aid,)).fetchone()[0])
            self.assertEqual(rich['id'], self.aid); self.assertEqual(rich['review_status'], 'unreviewed')
            self.assertTrue(all(e['source_id'] and e['version_id'] and e['excerpt_sha256'] for e in rich['evidence']))
            for e in rich['evidence']:
                self.assertEqual(self.main.record(e['record_id'])['body'][e['offset_start']:e['offset_end']], e['excerpt'])
        finally:
            db.close()
        result = export(self.main, self.output, self.root/'export')
        self.assertEqual(result['entities'], 2)
        count = len(writer.calls); merge(self.main, self.output, writer); self.assertEqual(len(writer.calls), count)
        self.assertEqual(self.before, [tuple(r) for r in self.main.db.execute('SELECT * FROM record_versions ORDER BY id')])
        (self.root/'export'/'entities.jsonl').write_text('user edit')
        with self.assertRaisesRegex(ValueError, 'edited'): export(self.main, self.output, self.root/'export')
    def test_writer_cannot_drop_or_invent_citations(self):
        evidence = {'p1': {'section': 'reference', 'scope': {}, 'targets': []}}
        value = {'claims': [], 'relations': [], 'conflicts': [], 'notes': []}
        with self.assertRaisesRegex(ValueError, 'omitted'): validate_items(value, evidence)
        value['claims'] = [{'kind': 'fact', 'text': 'Invented', 'fields': {}, 'evidence_ids': ['unknown']}]
        with self.assertRaisesRegex(ValueError, 'absent'): validate_items(value, evidence)
        with self.assertRaisesRegex(ValueError, 'disabled'): RichWriter({'writer_enabled': False})
    def test_claims_and_dedup_keep_scenario_and_reference_separate(self):
        evidence = {'p1': {'section': 'reference', 'scope': {}, 'targets': []},
                    'p2': {'section': 'scenario', 'scope': {}, 'targets': []}}
        item = {'kind': 'fact', 'text': 'Same wording.', 'fields': {}, 'evidence_ids': ['p1', 'p2']}
        value = {'claims': [item], 'relations': [], 'conflicts': [], 'notes': []}
        with self.assertRaisesRegex(ValueError, 'mixes'): validate_items(value, evidence)
        values = [{**value, 'claims': [{**item, 'evidence_ids': [eid]}]} for eid in evidence]
        combined = combine(values, evidence)
        self.assertEqual(len(combined['claims']), 2); validate_items(combined, evidence)
    def test_relation_cannot_invent_a_target_and_nonfinite_data_is_rejected(self):
        evidence = {'p1': {'section': 'reference', 'scope': {}, 'targets': []}}
        value = {'claims': [], 'relations': [{'predicate': 'owns', 'target_id': 'unknown', 'text': 'An invention.', 'evidence_ids': ['p1']}], 'conflicts': [], 'notes': []}
        with self.assertRaisesRegex(ValueError, 'target'): validate_items(value, evidence)
        value['relations'] = []
        value['claims'] = [{'kind': 'fact', 'text': 'Value.', 'fields': {'n': float('nan')}, 'evidence_ids': ['p1']}]
        with self.assertRaises(ValueError): validate_items(value, evidence)
    def test_writer_transport_rejects_nonlocal_url_without_a_request(self):
        with self.assertRaisesRegex(ValueError, 'loopback'):
            RichWriter({'writer_enabled': True, 'writer_model': 'fixture', 'writer_revision': '1', 'writer_url': 'https://example.com/v1'})


if __name__ == '__main__':
    unittest.main()
