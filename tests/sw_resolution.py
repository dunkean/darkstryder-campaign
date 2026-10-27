"""Resolution regressions against temporary SQLite stores, with no model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.store import Store
from packages.sw_kbase.mining import apply_decisions
from packages.sw_kbase.pipeline import invalidate_decisions


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "knowledge.sqlite")
        self.source = self.store.source("fixture", "Fixture")
        self.records = [self.store.ingest(self.source, str(i), "Shared name", body=f"Record {i}")[0] for i in range(3)]
        self.records.sort(key=lambda rid: self.store.record(rid)["entity_id"])
        self.a, self.b, self.c = self.records

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def decision(self, a, b, decision="same_entity", task="identity", predicate=""):
        proposal = self.store.enqueue(task, a, b, predicate=predicate)
        self.store.store_result(proposal, {"accepted": decision != "uncertain", "decision": decision})
        return proposal

    def same(self, a, b):
        return self.store.record(a)["entity_id"] == self.store.record(b)["entity_id"]

    def merge(self):
        proposal = self.decision(self.a, self.b)
        self.assertEqual(self.store.resolve_identity(proposal), "assigned")
        return proposal

    def last_event(self):
        return self.store.db.execute("SELECT max(id) FROM resolutions").fetchone()[0]

    def test_revised_negative_decision_revokes_existing_group(self):
        proposal = self.merge()
        self.store.store_result(proposal, {"accepted": True, "decision": "different_entity"})
        apply_decisions(self.store)
        self.assertFalse(self.same(self.a, self.b), "A newly negative identity decision must revoke the old group")

    def test_uncertain_non_anchor_pair_is_neutral_to_direct_anchor_proofs(self):
        self.merge()
        bc = self.decision(self.b, self.c, 'uncertain')
        ac = self.decision(self.a, self.c)
        self.assertEqual(self.store.resolve_identity(ac), 'assigned')
        self.assertTrue(self.same(self.a, self.c))
        self.store.store_result(bc, {'accepted': False, 'decision': 'uncertain'})
        self.assertTrue(self.same(self.a, self.c))

    def test_retracting_only_anchor_proof_revokes_group(self):
        ab = self.merge()
        self.store.store_result(ab, {'accepted': False, 'decision': 'uncertain'})
        self.assertFalse(self.same(self.a, self.b))

    def test_abstention_keeps_group_when_another_direct_anchor_proof_remains(self):
        ab = self.merge()
        alternative = self.store.enqueue('identity', self.a, self.b, context={'reason': 'other_evidence'})
        self.store.store_result(alternative, {'accepted': True, 'decision': 'same_entity'})
        self.store.store_result(ab, {'accepted': False, 'decision': 'uncertain'})
        self.assertTrue(self.same(self.a, self.b))
        new_question = self.store.enqueue('identity', self.a, self.b, context={'reason': 'new_hypothesis'})
        self.store.store_result(new_question, {'accepted': False, 'decision': 'uncertain'})
        self.assertTrue(self.same(self.a, self.b))

    def test_missing_anchor_proof_is_not_replaced_by_transitive_positives(self):
        self.merge()
        bc = self.decision(self.b, self.c)
        self.decision(self.a, self.c, 'uncertain')
        self.assertEqual(self.store.resolve_identity(bc), 'awaiting_anchor')
        anchor = self.store.db.execute("SELECT id FROM proposals WHERE json_extract(context_json,'$.reason')='direct_anchor_required'").fetchone()[0]
        self.store.store_result(anchor, {'accepted': False, 'decision': 'uncertain'})
        result = apply_decisions(self.store)
        self.assertGreater(result.get('anchor_abstained', 0), 0)
        self.assertFalse(self.same(self.a, self.c))
        self.assertEqual(apply_decisions(self.store), {})
        self.store.store_result(anchor, {'accepted': True, 'decision': 'same_entity'})
        apply_decisions(self.store)
        self.assertTrue(self.same(self.a, self.c))

    def test_unaccepted_negative_is_neutral(self):
        self.merge()
        bc = self.store.enqueue('identity', self.b, self.c)
        self.store.store_result(bc, {'accepted': False, 'decision': 'different_entity'})
        ac = self.decision(self.a, self.c)
        self.assertEqual(self.store.resolve_identity(ac), 'assigned')
        self.assertTrue(self.same(self.a, self.c))

    def test_resolution_policy_migration_requeues_blocked_proofs_without_model_call(self):
        self.merge()
        bc = self.decision(self.b, self.c)
        self.store.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (bc,))
        self.store.db.execute("INSERT OR REPLACE INTO meta VALUES('identity_resolution_policy','legacy')")
        self.store.db.commit();self.store.close()
        self.store = Store(Path(self.tmp.name) / 'knowledge.sqlite')
        self.assertIsNotNone(self.store.db.execute('SELECT 1 FROM application_queue WHERE proposal_id=?', (bc,)).fetchone())
        self.assertEqual(self.store.resolve_identity(bc), 'awaiting_anchor')
        ac = self.decision(self.a, self.c)
        apply_decisions(self.store)
        self.assertTrue(self.same(self.a, self.c))

    def test_manual_split_survives_resolution_and_can_be_undone(self):
        self.merge()
        self.store.detach(self.a, "manual_split")
        split_event = self.last_event()
        apply_decisions(self.store)
        self.assertFalse(self.same(self.a, self.b), "A preserved old positive proof must not undo a manual split")
        self.store.undo(split_event)
        self.assertTrue(self.same(self.a, self.b))

    def test_identity_does_not_close_transitively(self):
        self.merge()
        bc = self.decision(self.b, self.c)
        self.assertEqual(self.store.resolve_identity(bc), "awaiting_anchor")
        self.assertFalse(self.same(self.a, self.c))
        self.decision(self.a, self.c, "different_entity")
        self.assertEqual(self.store.resolve_identity(bc), "known_conflict")
        self.assertFalse(self.same(self.a, self.c))

    def test_blocked_identity_is_retried_when_proof_changes_without_busy_replay(self):
        self.merge()
        self.decision(self.a, self.c)
        bc = self.decision(self.b, self.c, 'different_entity')
        result = apply_decisions(self.store)
        self.assertGreater(result.get('known_conflict', 0), 0)
        self.assertFalse(self.same(self.a, self.c))
        self.assertEqual(apply_decisions(self.store), {})
        self.store.store_result(bc, {'accepted': True, 'decision': 'same_entity'})
        apply_decisions(self.store)
        self.assertTrue(self.same(self.a, self.c))

    def test_mention_only_conflict_blocks_cluster_extension(self):
        self.merge()
        ac = self.decision(self.a, self.c)
        self.decision(self.b, self.c, "mention_only")
        self.assertEqual(self.store.resolve_identity(ac), "known_conflict")
        self.assertFalse(self.same(self.a, self.c))

    def test_changed_anchor_invalidates_proofs_without_breaking_reimport(self):
        proposal = self.merge()
        original = self.store.record(self.a)
        changed_id, changed = self.store.ingest(self.source, original["source_key"], "Changed name", body="New bytes")
        self.assertEqual(changed_id, self.a)
        self.assertTrue(changed)
        self.assertFalse(self.same(self.a, self.b))
        self.assertEqual(self.store.db.execute("SELECT status FROM proposals WHERE id=?", (proposal,)).fetchone()[0], "stale")
        invalidation_event = self.last_event()
        with self.assertRaises(ValueError):
            self.store.undo(invalidation_event)
        apply_decisions(self.store)
        self.assertFalse(self.same(self.a, self.b))
        self.assertEqual(self.store.entity(self.store.record(self.a)["entity_id"])["entity"]["name"], "Changed name")
        before = self.store.db.execute("SELECT count(*) FROM record_versions").fetchone()[0]
        _, changed = self.store.ingest(self.source, original["source_key"], "Changed name", body="New bytes")
        self.assertFalse(changed)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM record_versions").fetchone()[0], before)

    def test_undo_merge_preserves_both_editorial_overlays(self):
        original_entities = [self.store.record(rid)["entity_id"] for rid in (self.a, self.b)]
        for index, eid in enumerate(original_entities):
            self.store.db.execute("INSERT INTO editorial(entity_id,content_json) VALUES(?,?)", (eid, json.dumps({"note": f"Personal {index}"})))
        self.merge()
        merge_event = self.last_event()
        merged = self.store.entity(self.store.record(self.a)["entity_id"])
        self.assertEqual({row["entity_id"] for row in merged["editorial_overlays"]}, set(original_entities))
        self.store.undo(merge_event)
        self.assertFalse(self.same(self.a, self.b))
        for index, eid in enumerate(original_entities):
            self.assertEqual(json.loads(self.store.entity(eid)["editorial"]["content_json"]), {"note": f"Personal {index}"})

    def test_relation_wrong_predicate_is_not_published(self):
        proposal = self.decision(self.a, self.b, "member_of", task="relation", predicate="equipped_with")
        apply_decisions(self.store)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links").fetchone()[0], 0)
        self.store.store_result(proposal, {"accepted": True, "decision": "equipped_with"})
        apply_decisions(self.store)
        self.assertEqual(self.store.db.execute("SELECT predicate FROM links WHERE proposal_id=?", (proposal,)).fetchone()[0], "equipped_with")

    def test_relation_can_be_revalidated_after_contract_invalidation(self):
        proposal = self.decision(self.a, self.b, "equipped_with", task="relation", predicate="equipped_with")
        apply_decisions(self.store)
        invalidate_decisions(self.store)
        self.store.store_result(proposal, {"accepted": True, "decision": "equipped_with"})
        apply_decisions(self.store)
        self.assertEqual(self.store.db.execute("SELECT status FROM links WHERE proposal_id=?", (proposal,)).fetchone()[0], "accepted")

    def test_changed_source_marks_previous_synthesis_stale(self):
        self.merge()
        eid = self.store.record(self.a)["entity_id"]
        self.store.db.execute("INSERT INTO syntheses(id,entity_id,signature,result_json,status) VALUES(?,?,?,?,?)", ("s", eid, "old", "{}", "ready"))
        original = self.store.record(self.a)
        self.store.ingest(self.source, original["source_key"], original["name"], body="Changed source bytes")
        self.assertNotEqual(self.store.db.execute("SELECT status FROM syntheses WHERE id='s'").fetchone()[0], "ready")

    def status(self, proposal):
        return self.store.db.execute("SELECT status FROM proposals WHERE id=?", (proposal,)).fetchone()[0]

    def test_direct_anchor_proofs_cover_only_remaining_internal_pairs(self):
        self.merge()
        pending = self.store.enqueue('identity', self.b, self.c)
        ac = self.decision(self.a, self.c)
        self.assertEqual(self.store.resolve_identity(ac), 'assigned')
        self.assertEqual(self.status(pending), 'covered_by_anchor')
        self.assertIsNone(self.store.db.execute('SELECT decision FROM proposals WHERE id=?', (pending,)).fetchone()[0])
        self.store.undo(self.last_event())
        self.assertEqual(self.status(pending), 'manual_rejected')
        apply_decisions(self.store)
        self.assertFalse(self.same(self.a, self.c))

    def occurrence_jobs(self):
        self.merge()
        context = {'excerpt': 'C sees Shared name.', 'start': 10}
        first = self.store.enqueue('mention', self.c, self.a, context=context)
        other = self.store.enqueue('mention', self.c, self.b, context=context)
        elsewhere = self.store.enqueue('mention', self.c, self.b, context={**context, 'start': 50})
        return first, other, elsewhere, context

    def test_positive_mention_covers_same_occurrence_and_revocation_reopens_it(self):
        first, other, elsewhere, _ = self.occurrence_jobs()
        self.store.store_result(first, {'accepted': True, 'decision': 'refers_to'})
        apply_decisions(self.store)
        self.assertEqual(self.status(other), 'covered_by_resolved_mention')
        self.assertEqual(self.status(elsewhere), 'pending')
        self.store.store_result(first, {'accepted': True, 'decision': 'different_referent'})
        self.assertEqual(self.status(other), 'pending')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links WHERE status='accepted' AND proposal_id=?", (first,)).fetchone()[0], 0)

    def test_negative_mention_does_not_cover_other_profiles(self):
        first, other, _, _ = self.occurrence_jobs()
        self.store.store_result(first, {'accepted': True, 'decision': 'different_referent'})
        apply_decisions(self.store)
        self.assertEqual(self.status(other), 'pending')

    def test_relation_coverage_depends_on_grounding_and_identity(self):
        first, other, _, context = self.occurrence_jobs()
        self.store.store_result(first, {'accepted': True, 'decision': 'refers_to'})
        apply_decisions(self.store)
        relation = self.store.enqueue('relation', self.c, self.a, context={**context, 'mention_proposal_id': first})
        alternative = self.store.enqueue('relation', self.c, self.b, context={**context, 'mention_proposal_id': other})
        self.store.store_result(relation, {'accepted': True, 'decision': 'member_of'})
        apply_decisions(self.store)
        self.assertEqual(self.status(alternative), 'covered_by_resolved_relation')
        self.store.store_result(first, {'accepted': False, 'decision': 'uncertain'})
        self.assertEqual(self.status(relation), 'stale')
        self.assertEqual(self.status(alternative), 'pending')

    def test_changed_target_identity_reopens_covered_occurrences(self):
        first, other, _, _ = self.occurrence_jobs()
        self.store.store_result(first, {'accepted': True, 'decision': 'refers_to'})
        apply_decisions(self.store)
        original = self.store.record(self.a)
        self.store.ingest(self.source, original['source_key'], original['name'], body='Changed identity evidence')
        self.assertEqual(self.status(other), 'pending')
        self.assertFalse(self.same(self.a, self.b))


if __name__ == "__main__":
    unittest.main()
