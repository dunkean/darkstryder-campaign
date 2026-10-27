"""Offline checks of the grouped contract; no GPU/model/network."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.document_sections import split_sections
from packages.sw_kbase.grouped_paragraphs import plan, score, validate_response
from packages.sw_kbase.enrichment import connect, score as legacy_score
from packages.sw_kbase.native_jev import NativeJevContextError
from packages.sw_kbase.store import Store


class JointFixture:
    model = 'joint-fixture'; identity = {'model': 'joint-fixture', 'revision': 1}
    capabilities = {'joint_paragraph_scores': True}
    def __init__(self, budget=100000):
        self.calls = []; self.budget = budget
    def validate_runtime(self):
        return self.identity
    def preflight(self, request):
        if len(json.dumps(request)) > self.budget:
            raise NativeJevContextError('fixture budget', reason='context_budget')
    def __call__(self, request):
        self.calls.append(request)
        state = request['state']; coverage = state['REFERENCE'].get('body', '') + '\n' + '\n'.join(
            p['body'] for p in state['RETAINED_EVIDENCE'])
        output = {}
        for paragraph in state['PARAGRAPHS']:
            no = paragraph['body'] in coverage
            output[paragraph['id']] = {'yes': 2 if no else 96, 'no': 96 if no else 2, 'unknown': 2}
            if not no:
                coverage += '\n' + paragraph['body']
        return {'inference_passes': 1, 'paragraphs': output}


class SectionsTests(unittest.TestCase):
    def test_heading_and_contiguous_list_are_one_passage(self):
        body = '# Skills\n- Pilot 5D\n- Blaster 4D\n- Dodge 3D\n\nA separate description.'
        rows = split_sections(body)
        self.assertEqual(len(rows), 2)
        self.assertIn('# Skills', rows[0]['body']); self.assertIn('Dodge', rows[0]['body'])
        for p in rows:
            self.assertEqual(body[p['offset_start']:p['offset_end']], p['body'])
    def test_stat_json_is_not_one_vote_per_field(self):
        body = '{"name":"Pilot","stats":{"DEX":"3D"},"skills":{"pilot":"5D"}}'
        rows = split_sections(body)
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['body'], body)
        self.assertEqual(len(rows[0]['parser_spans']), 3)
    def test_plain_paragraphs_remain_separate_without_arbitrary_char_splits(self):
        body = 'First. ' * 1000 + '\n\nSecond.'
        rows = split_sections(body)
        self.assertEqual(len(rows), 2); self.assertGreater(len(rows[0]['body']), 1000)
    def test_html_table_rows_and_heading_form_one_passage(self):
        body = '<h2>Skills</h2><table><tr><td>Pilot 5D</td></tr><tr><td>Dodge 4D</td></tr></table><p>Description.</p>'
        rows = split_sections(body)
        self.assertEqual(len(rows), 2)
        self.assertIn('Skills', rows[0]['body']); self.assertIn('Dodge', rows[0]['body'])


class GroupedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.main = Store(self.root/'main.sqlite'); self.output = self.root/'selection'
        sid = self.main.source('ref', 'Reference')
        self.reference, _ = self.main.ingest(sid, 'ref', 'Pilot', 'Pilot flies a ship.', kind='person')
        sid = self.main.source('other', 'Other')
        self.candidate, _ = self.main.ingest(sid, 'other', 'Pilot',
            'Pilot owns a hidden base.\n\nPilot guards a temple.', kind='person')
        self.main.db.commit(); clean(self.main)
        self.before = [tuple(r) for r in self.main.db.execute('SELECT * FROM record_versions ORDER BY id')]
    def tearDown(self):
        self.main.close(); self.temp.cleanup()
    def prepare(self):
        plan(self.main, self.output, config={'source_priorities': {'ref': 1000}})
    def test_one_document_request_returns_all_paragraph_scores_and_no_global(self):
        self.prepare(); client = JointFixture(); result = score(self.main, self.output, client)
        self.assertEqual(len(client.calls), 1); self.assertEqual(result['model_calls'], 1)
        request = client.calls[0]
        self.assertEqual([p['id'] for p in request['state']['PARAGRAPHS']], ['P00001', 'P00002'])
        self.assertNotIn('global', request['output']); self.assertNotIn('document', request['output'])
        db = connect(self.output)
        try:
            verdict = json.loads(db.execute('SELECT result_json FROM documents WHERE id=?', (self.candidate,)).fetchone()[0])
            self.assertFalse(verdict['global_vote_requested']); self.assertEqual(len(verdict['paragraphs']), 2)
            self.assertEqual(db.execute('SELECT count(*) FROM selected_paragraphs').fetchone()[0], 3)
        finally:
            db.close()
        self.assertEqual(self.before, [tuple(r) for r in self.main.db.execute('SELECT * FROM record_versions ORDER BY id')])
    def test_next_document_sees_previous_document_additions(self):
        sid = self.main.source('third', 'Third')
        self.main.ingest(sid, 'third', 'Pilot', 'Pilot owns a hidden base.\n\nPilot guards a temple.', kind='person')
        self.main.db.commit(); clean(self.main); self.prepare()
        client = JointFixture(); score(self.main, self.output, client)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(any('hidden base' in p['body'] for p in client.calls[1]['state']['RETAINED_EVIDENCE']))
        db = connect(self.output)
        try:
            statuses = dict(db.execute('SELECT status,count(*) FROM paragraphs WHERE document_id!=? GROUP BY status', (self.reference,)))
            self.assertEqual(statuses, {'add': 2, 'skip_redundant': 2})
            self.assertEqual(db.execute("SELECT count(*) FROM selected_paragraphs WHERE role='candidate'").fetchone()[0], 2)
        finally:
            db.close()
    def test_serial_backend_and_legacy_loop_are_refused_before_inference(self):
        self.prepare(); client = JointFixture(); client.capabilities = {}
        with self.assertRaisesRegex(ValueError, 'joint'):
            score(self.main, self.output, client)
        with self.assertRaisesRegex(ValueError, 'Grouped'):
            legacy_score(self.main, self.output, client)
        self.assertFalse(client.calls)
    def test_resume_uses_document_results_and_does_not_call_again(self):
        self.prepare(); score(self.main, self.output, JointFixture())
        client = JointFixture(); result = score(self.main, self.output, client)
        self.assertFalse(client.calls); self.assertEqual(result['remaining_entities'], 0)
    def test_missing_extra_and_fake_joint_results_are_rejected(self):
        for response in ({'paragraphs': {'P1': {'yes': 96, 'no': 2, 'unknown': 2}}},
                         {'inference_passes': 1, 'paragraphs': {}},
                         {'inference_passes': 1, 'paragraphs': {'P1': {'yes': float('nan'), 'no': 2, 'unknown': 2}}}):
            with self.assertRaises(ValueError):
                validate_response(response, ['P1'])
    def test_long_document_is_batched_without_losing_passages(self):
        record = self.main.record(self.candidate)
        body = '\n\n'.join(f'Passage {i}: pilot owns a different base number {i}.' for i in range(40))
        self.main.ingest(record['source_id'], record['source_key'], record['name'], body, kind='person')
        self.main.db.commit(); clean(self.main); self.prepare()
        client = JointFixture(budget=4300); score(self.main, self.output, client)
        self.assertGreater(len(client.calls), 1)
        passages = [p for call in client.calls for p in call['state']['PARAGRAPHS']]
        self.assertEqual(len(passages), 40)
        self.assertEqual([p['body'] for p in passages], [p['body'] for p in split_sections(body)])
        self.assertTrue(any(len(c['state']['PARAGRAPHS']) > 1 for c in client.calls))
        self.assertTrue(all(len(json.dumps(c)) <= 4300 for c in client.calls))
        self.assertTrue(client.calls[1]['state']['RETAINED_EVIDENCE'])
    def test_partial_document_replays_exact_cache_before_continuing(self):
        record = self.main.record(self.candidate)
        body = '\n\n'.join(f'Passage {i}: pilot owns a different base number {i}.' for i in range(40))
        self.main.ingest(record['source_id'], record['source_key'], record['name'], body, kind='person')
        self.main.db.commit(); clean(self.main); self.prepare()
        class Interrupted(JointFixture):
            def __call__(self, request):
                if self.calls:
                    raise RuntimeError('fixture stop')
                return super().__call__(request)
        first = Interrupted(budget=4300)
        with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
            score(self.main, self.output, first)
        client = JointFixture(budget=4300); result = score(self.main, self.output, client)
        self.assertEqual(result['cache_hits'], 1)
        self.assertNotEqual(client.calls[0], first.calls[0])
        self.assertEqual(result['remaining_entities'], 0)
    def test_eighty_percent_redundancy_is_still_retained(self):
        self.prepare(); client = JointFixture()
        original = client.__class__.__call__
        class Eighty(JointFixture):
            def __call__(self, request):
                value = original(self, request)
                value['paragraphs'] = {key: {'yes': 18, 'no': 80, 'unknown': 2} for key in value['paragraphs']}
                return value
        result = score(self.main, self.output, Eighty())
        self.assertEqual(result['add_uncertain'], 2)
    def test_exact_copy_is_deduplicated_with_proof_without_inventing_model_confidence(self):
        record = self.main.record(self.candidate)
        self.main.ingest(record['source_id'], record['source_key'], record['name'],
                         'Pilot flies a ship.\n\nPilot guards a temple.', kind='person')
        self.main.db.commit(); clean(self.main); self.prepare()
        class Hesitant(JointFixture):
            def __call__(self, request):
                value = super().__call__(request)
                value['paragraphs'] = {key: {'yes': 24, 'no': 41, 'unknown': 35} for key in value['paragraphs']}
                return value
        result = score(self.main, self.output, Hesitant())
        self.assertEqual(result['skip_redundant'], 1); self.assertEqual(result['add_uncertain'], 1)
        db = connect(self.output)
        try:
            row = db.execute('SELECT result_json FROM paragraphs WHERE document_id=? AND number=1', (self.candidate,)).fetchone()
            vote = json.loads(row[0])['votes'][0]
            self.assertEqual(vote['scores_pct']['no'], 41)
            self.assertEqual(vote['selection_proof']['covered_by']['record_id'], self.reference)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
