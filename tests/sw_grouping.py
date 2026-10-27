"""Read-only grouped planning; no local model or production database needed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.grouping import plan_groups, analyze_groups
from packages.sw_kbase.store import Store


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'knowledge.sqlite')
        self.source = self.store.source('fixture', 'Fixture')
        self.serial = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, name='Shared Name', **kwargs):
        self.serial += 1
        return self.store.ingest(self.source, str(self.serial), name,
                                 body=kwargs.pop('body', 'Short complete fixture.'),
                                 kind=kwargs.pop('kind', 'person'), universe='star_wars', **kwargs)[0]

    def family(self, size=3):
        records = [self.record() for _ in range(size)]
        ids = [self.store.enqueue('identity', a, b, context={'reason': 'lexical_candidate'})
               for i, a in enumerate(records) for b in records[i + 1:]]
        return records, ids

    def plan(self, task, **kwargs):
        plan = plan_groups(self.store, tasks=[task], **kwargs)
        return list(plan), plan.metrics

    def mention(self, subject, candidates, offset=7, **extra):
        context = {'mention': 'Pilot', 'passage': 'The Pilot arrived.', 'offset': offset, 'field': None, **extra}
        return [self.store.enqueue('mention', subject, target, context=context) for target in candidates]

    def merge(self, a, b):
        proposal = self.store.enqueue('identity', a, b)
        self.store.store_result(proposal, {'accepted': True, 'decision': 'same_entity'})
        self.assertEqual(self.store.resolve_identity(proposal), 'assigned')

    def test_identity_plan_is_readonly_deterministic_and_keeps_provenance(self):
        records, proposals = self.family()
        before = self.store.db.total_changes
        groups, metrics = self.plan('identity_tuple')
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]['record_ids']), set(records))
        self.assertEqual(set(groups[0]['proposal_ids']), set(proposals))
        self.assertEqual(set(groups[0]['source_ids'].values()), {self.source})
        self.assertEqual(groups, self.plan('identity_tuple')[0])
        self.assertEqual(metrics['estimated_questions_saved'], 2)
        self.assertTrue(metrics['complete'])
        self.store.db.commit()
        readonly = Store(self.store.path, readonly=True)
        try:
            self.assertEqual(list(plan_groups(readonly, tasks=['identity_tuple'])), groups)
        finally:
            readonly.close()

    def test_tuple_uses_canonical_anchors_and_does_not_cover_other_edges(self):
        records, _ = self.family(4)
        self.merge(records[0], records[1])
        groups, _ = self.plan('identity_tuple')
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(len(group['record_ids']), 3)
        self.assertEqual(len(group['entity_snapshots']), 3)
        self.assertGreater(len(group['family_proposal_ids']), len(group['proposal_ids']))
        for proposal in group['proposal_ids']:
            a, b = self.store.db.execute('SELECT a,b FROM proposals WHERE id=?', (proposal,)).fetchone()
            self.assertIn(a, group['record_ids']); self.assertIn(b, group['record_ids'])

    def test_conflict_on_nonrepresentative_member_blocks_tuple(self):
        records, _ = self.family(4)
        self.merge(records[0], records[1])
        group = self.plan('identity_tuple')[0][0]
        nonanchor = next(r for r in records if r not in group['record_ids'])
        target = records[2]
        a, b = sorted([nonanchor, target])
        self.store.db.execute('INSERT INTO cannot_links VALUES(?,?,?)', (a, b, 'manual_split'))
        groups, metrics = self.plan('identity_tuple')
        self.assertEqual(groups, [])
        self.assertEqual(metrics['skipped']['identity_manual_conflict'], 1)

    def test_uncertain_is_neutral_but_accepted_negative_blocks(self):
        records, _ = self.family()
        proposal = self.store.enqueue('identity', records[0], records[1], context={'reason': 'extra'})
        self.store.store_result(proposal, {'accepted': False, 'decision': 'uncertain'})
        self.assertEqual(len(self.plan('identity_tuple')[0]), 1)
        self.store.store_result(proposal, {'accepted': True, 'decision': 'mention_only'})
        self.assertEqual(self.plan('identity_tuple')[0], [])

    def test_identity_budget_and_kind_prevent_unsafe_family(self):
        records, _ = self.family()
        old = self.store.record(records[0])
        self.store.ingest(self.source, old['source_key'], old['name'], body='x' * 20000,
                          kind='person', universe='star_wars')
        self.store.enqueue('identity', records[0], records[1])
        groups, metrics = self.plan('identity_tuple')
        self.assertEqual(groups, [])
        self.assertIn('identity_tuple_budget', metrics['skipped'])

    def test_models_and_individuals_are_separate_families(self):
        records = [self.record('Same ship', kind='craft_model') for _ in range(2)]
        records.append(self.record('Same ship', kind='craft_individual'))
        for left, right in zip(records, records[1:]):
            self.store.enqueue('identity', left, right)
        self.assertEqual(self.plan('identity_tuple')[0], [])

    def test_scope_on_candidate_keeps_complete_tuple_and_versions_change_id(self):
        records, _ = self.family()
        old = self.store.record(records[2])
        self.store.ingest(self.source, old['source_key'], old['name'], body='DarkStryder fixture',
                          kind='person', universe='star_wars')
        self.store.enqueue('identity', records[0], records[2])
        groups, _ = self.plan('identity_tuple', scope='DarkStryder')
        self.assertEqual(set(groups[0]['record_ids']), set(records))
        self.assertEqual(self.plan('identity_tuple', scope='Missing scope')[0], [])
        previous = groups[0]['id']
        self.store.ingest(self.source, old['source_key'], old['name'], body='DarkStryder changed',
                          kind='person', universe='star_wars')
        self.store.enqueue('identity', records[0], records[2])
        self.assertNotEqual(previous, self.plan('identity_tuple', scope='DarkStryder')[0][0]['id'])

    def test_occurrences_remain_separate_and_metadata_dependency_deduplicates(self):
        subject = self.record('Subject')
        candidates = [self.record('Candidate ' + str(i)) for i in range(3)]
        first = self.mention(subject, candidates, mention_proposal_id='one')
        second = self.mention(subject, candidates, offset=70)
        duplicate = self.mention(subject, [candidates[0]], mention_proposal_id='two')
        groups, _ = self.plan('mention_choice')
        self.assertEqual(len(groups), 2)
        seven = next(g for g in groups if g['context']['offset'] == 7)
        self.assertEqual(set(seven['proposal_ids']), set(first + duplicate))
        self.assertNotIn('mention_proposal_id', seven['context'])
        self.assertEqual(seven['context']['passage'], 'The Pilot arrived.')
        self.assertFalse(seven['context']['candidate_set_exhaustive'])
        self.assertEqual(len(seven['candidates']), 3)
        self.assertTrue(set(second).isdisjoint(seven['proposal_ids']))

    def test_candidate_entities_coalesce_with_original_profile_versions(self):
        subject = self.record('Subject')
        candidates = [self.record('Pilot') for _ in range(2)]
        self.merge(*candidates)
        proposals = self.mention(subject, candidates)
        groups, _ = self.plan('mention_choice')
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(len(group['candidates']), 1)
        self.assertEqual(set(group['candidates'][0]['member_record_ids']), set(candidates))
        self.assertEqual(set(group['proposal_ids']), set(proposals))
        self.assertTrue(set(candidates) <= set(group['versions']))

    def test_large_candidate_set_is_not_silently_truncated(self):
        subject = self.record('Subject')
        candidates = [self.record('Pilot ' + str(i)) for i in range(9)]
        self.mention(subject, candidates)
        groups, metrics = self.plan('mention_choice')
        self.assertEqual(groups, [])
        self.assertEqual(metrics['skipped']['mention_candidate_size'], 1)

    def test_missing_candidate_and_overbudget_passage_are_explicit(self):
        subject = self.record('Subject')
        candidates = [self.record('Pilot ' + str(i)) for i in range(2)]
        self.mention(subject, candidates)
        self.store.db.execute('UPDATE records SET active=0 WHERE id=?', (candidates[0],))
        groups, metrics = self.plan('mention_choice')
        self.assertEqual(groups, [])
        self.assertIn('mention_missing_candidate', metrics['skipped'])
        self.store.db.execute('UPDATE records SET active=1 WHERE id=?', (candidates[0],))
        self.mention(subject, candidates, offset=99, passage='x' * 13000)
        groups, metrics = self.plan('mention_choice')
        self.assertEqual(len(groups), 1)
        self.assertIn('mention_choice_budget', metrics['skipped'])

    def test_limit_window_analysis_and_bounds(self):
        subject = self.record('Subject')
        candidates = [self.record('Pilot ' + str(i)) for i in range(2)]
        self.mention(subject, candidates)
        self.mention(subject, candidates, offset=99)
        report = analyze_groups(self.store, tasks=['mention_choice'], limit=1, sample_limit=1)
        self.assertEqual(report['metrics']['groups'], 1)
        self.assertTrue(report['metrics']['limit_reached'])
        self.assertFalse(report['metrics']['complete'])
        groups, metrics = self.plan('mention_choice', candidate_window=1)
        self.assertEqual(len(groups), 2, 'Subject boundary must retain complete occurrences')
        self.assertTrue(metrics['window_limited'])
        with self.assertRaises(ValueError):
            plan_groups(self.store, max_members=2)


if __name__ == '__main__':
    unittest.main()
