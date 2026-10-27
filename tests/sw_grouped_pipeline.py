"""Grouped evidence is reversible; mixed tuples never become negative pairs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from itertools import combinations

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.store import Store
from packages.sw_kbase.grouping import plan_groups
from packages.sw_kbase.grouped_pipeline import register_group, group_job, apply_group, current_group
from packages.sw_kbase.semantic import SemanticClient


class GroupedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'knowledge.sqlite')
        self.source = self.store.source('fixture', 'Fixture')
        self.client = SemanticClient({'semantic_model': 'test', 'semantic_revision': 'fixture'})

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def tuple(self):
        ids = [self.store.ingest(self.source, str(i), 'Alpha', body='The particular vessel Alpha.', kind='craft_individual')[0] for i in range(3)]
        for a, b in combinations(ids, 2):self.store.enqueue('identity', *sorted([a, b]))
        group = next(iter(plan_groups(self.store, tasks=['identity_tuple'])))
        return ids, group

    def execute(self, group, label, accepted=True):
        pid = register_group(self.store, group)
        verdict = {'id': pid, 'task': group['task'], 'accepted': accepted, 'decision': label,
                   'raw_decision': label, 'confidence': .99, 'model': 'test', 'revision': 'fixture'}
        self.store.store_result(pid, verdict)
        result = apply_group(self.store, group, pid, verdict, self.client)
        self.store.db.commit()
        return pid, result

    def test_one_all_same_observation_entails_all_direct_pairs(self):
        ids, group = self.tuple()
        pid, result = self.execute(group, 'all_same')
        self.assertEqual(result['derived_pair_proofs'], 3)
        self.assertEqual(len({self.store.record(rid)['entity_id'] for rid in ids}), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE task='identity' AND status='pending'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM syntheses').fetchone()[0], 0)
        proofs = [json.loads(r[0]) for r in self.store.db.execute("SELECT result_json FROM proposals WHERE task='identity' AND status='accepted'")]
        self.assertTrue(all(p['derived_from_group'] == pid and p['inference_performed'] is False for p in proofs))

    def test_mixed_and_uncertain_tuples_keep_binary_questions(self):
        ids, group = self.tuple()
        self.execute(group, 'mixed')
        self.assertEqual(len({self.store.record(rid)['entity_id'] for rid in ids}), 3)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE task='identity' AND status='pending'").fetchone()[0], 3)

    def test_unpersisted_or_wrong_group_verdict_cannot_make_proofs(self):
        ids, group = self.tuple();pid = register_group(self.store, group)
        verdict = {'id': pid, 'task': 'identity_tuple', 'accepted': True, 'decision': 'all_same'}
        with self.assertRaises(ValueError):apply_group(self.store, group, pid, verdict, self.client)
        self.store.store_result(pid, verdict)
        changed = json.loads(json.dumps(group));changed['context']['family']['name'] = 'Unseen family'
        with self.assertRaises(ValueError):apply_group(self.store, changed, pid, verdict, self.client)

    def test_revised_tuple_revokes_every_derived_proof(self):
        ids, group = self.tuple()
        pid, _ = self.execute(group, 'all_same')
        self.store.store_result(pid, {'accepted': True, 'decision': 'mixed'})
        self.assertEqual(len({self.store.record(rid)['entity_id'] for rid in ids}), 3)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE task='identity' AND status='accepted'").fetchone()[0], 0)

    def test_changed_nonanchor_input_invalidates_tuple(self):
        ids, group = self.tuple()
        pid, _ = self.execute(group, 'all_same')
        member = next(rid for rid in ids if rid != group['anchor_record_id'])
        key = self.store.record(member)['source_key']
        self.store.ingest(self.source, key, 'Alpha', body='Changed evidence about another vessel.', kind='craft_individual')
        self.assertFalse(current_group(self.store, group))
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'stale')
        self.assertEqual(len({self.store.record(rid)['entity_id'] for rid in ids}), 3)

    def test_retired_nonanchor_input_invalidates_tuple(self):
        ids, group = self.tuple();pid, _ = self.execute(group, 'all_same')
        self.store.retire(next(rid for rid in ids if rid != group['anchor_record_id']))
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'stale')

    def test_mention_choice_has_one_positive_and_reversible_coverage(self):
        subject = self.store.ingest(self.source, 'subject', 'Episode', body='Alpha arrived.')[0]
        candidates = [self.store.ingest(self.source, 'target'+str(i), 'Alpha', body='Individual '+str(i))[0] for i in range(2)]
        context = {'mention': 'Alpha', 'passage': 'Alpha arrived.', 'offset': 0, 'field': None}
        original = [self.store.enqueue('mention', subject, rid, context=context) for rid in candidates]
        group = next(iter(plan_groups(self.store, tasks=['mention_choice'])))
        job = group_job(self.store, group, 'test')
        payload = self.client._payload([(job, job['context'], False)])
        self.assertIn('c0', payload['questions']['q0']['criteria'])
        self.assertIn('none', payload['questions']['q0']['criteria'])
        pid, result = self.execute(group, 'c0')
        self.assertEqual(result['derived_pair_proofs'], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links WHERE predicate='mentions' AND status='accepted'").fetchone()[0], 1)
        self.assertTrue(all(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (p,)).fetchone()[0] == 'covered_by_group' for p in original))
        self.store.store_result(pid, {'accepted': False, 'decision': 'uncertain'})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links WHERE status='accepted'").fetchone()[0], 0)
        self.assertTrue(all(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (p,)).fetchone()[0] == 'pending' for p in original))

    def test_mention_abstention_defers_without_assertion_and_source_change_reopens(self):
        subject = self.store.ingest(self.source, 'unclear', 'Episode', body='Alpha arrived.')[0]
        ids = [self.store.ingest(self.source, 'choice'+str(i), 'Alpha', body='Subject '+str(i))[0] for i in range(2)]
        for rid in ids:self.store.enqueue('mention', subject, rid, context={'mention':'Alpha','passage':'Alpha arrived.','offset':0})
        group = next(iter(plan_groups(self.store, tasks=['mention_choice'])))
        pid, result = self.execute(group, 'uncertain', accepted=False)
        self.assertEqual(result['deferred_unresolved'], 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM links').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE status='deferred_by_group'").fetchone()[0], 2)
        self.store.ingest(self.source, 'choice0', 'Alpha', body='New evidence.')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE status='deferred_by_group'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'stale')


if __name__ == '__main__':unittest.main()
