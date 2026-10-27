"""CPU cleaning actually groups documents and links text without model calls."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.store import Store
from packages.sw_kbase.cleaning import clean, name_key, one_typo


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'kb.sqlite')
        self.source=self.store.source('fixture','Fixture')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def add(self,key,name,body='',**kw):return self.store.ingest(self.source,key,name,body=body,**kw)[0]
    def aid(self,rid):return self.store.db.execute('SELECT aggregate_id FROM aggregate_members WHERE record_id=?',(rid,)).fetchone()[0]

    def test_doctor_titles_profiles_and_model_numbers(self):
        self.assertEqual(name_key('Dr. Lancer Brunou','person'),name_key('Doctor Lancer Brunou','person'))
        self.assertEqual(name_key('Captain Gilad Pellaeon (9.1 ABY, Sluis Van)','person'),'gilad pellaeon')
        self.assertEqual(name_key('15 Turbolaser Cannons','profile_component'),'turbolaser cannon')
        self.assertFalse(one_typo('Astromech Model R2-D2','Astromech Model R2-D3'))

    def test_cleaning_groups_links_and_reduces_queue_without_touching_model_history(self):
        a=self.add('a','Dr. Lancer Brunou','The doctor lives aboard FarStar.',kind='person')
        b=self.add('b','Doctor Lancer Brunou','A different edition profile.',kind='person')
        ship=self.add('ship','FarStar','Doctor Lancer Brunou carries a Heavy Blaster.',kind='craft_individual',payload={'weapons':'Heavy Blaster'})
        item=self.add('item','Heavy Blaster','A weapon.',kind='equipment_model')
        q=self.store.enqueue('identity',*sorted([a,b]))
        m=self.store.enqueue('mention',ship,b,context={'mention':'Doctor Lancer Brunou','passage':'Doctor Lancer Brunou carries a Heavy Blaster.'})
        r=self.store.enqueue('relation',ship,item,predicate='equipped_with',context={'passage':'weapons: Heavy Blaster'})
        t=self.store.enqueue('type',a);self.store.enqueue('merge_utility',a,b)
        self.store.db.commit()
        with patch('packages.sw_kbase.semantic.SemanticClient.evaluate',side_effect=AssertionError('No model calls')):
            report=clean(self.store)
        self.assertEqual(report['model_calls'],0);self.assertEqual(self.aid(a),self.aid(b))
        self.assertNotEqual(self.store.record(a)['entity_id'],self.store.record(b)['entity_id'])
        for pid,status in [(q,'auto_grouped'),(m,'auto_linked'),(r,'auto_linked'),(t,'auto_typed')]:
            self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?',(pid,)).fetchone()[0],status)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM aggregate_links WHERE kind='has_item'").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("SELECT document_count FROM aggregate_links WHERE kind='has_item'").fetchone()[0],1)
        self.assertTrue(self.store.db.execute("SELECT 1 FROM aggregate_link_evidence WHERE record_id=? AND rule='exact_alias'",(ship,)).fetchone())
        self.assertEqual(report['remaining_questions'],0)
        old_id=self.aid(a);second=clean(self.store);self.assertEqual(self.aid(a),old_id)
        self.assertEqual(second['remaining_questions'],0)

    def test_prior_negative_is_warning_and_kept(self):
        a=self.add('a','Doctor Test Person',kind='person');b=self.add('b','Dr Test Person',kind='person')
        q=self.store.enqueue('identity',a,b);verdict={'accepted':True,'decision':'different_entity','model':'old-model'}
        self.store.store_result(q,verdict);self.store.db.commit();clean(self.store)
        self.assertEqual(self.aid(a),self.aid(b))
        flags=json.loads(self.store.db.execute('SELECT warnings_json FROM aggregates WHERE id=?',(self.aid(a),)).fetchone()[0])
        self.assertIn('prior_model_conflict',flags)
        self.assertEqual(json.loads(self.store.db.execute('SELECT result_json FROM proposals WHERE id=?',(q,)).fetchone()[0]),verdict)

    def test_unknown_type_stays_unknown_instead_of_inventing_a_creature(self):
        rid=self.add('untyped','Unclassified archival entry','No explicit category is supplied.')
        pid=self.store.enqueue('type',rid);self.store.db.commit();report=clean(self.store)
        self.assertEqual(self.store.db.execute('SELECT kind FROM aggregates WHERE id=?',(self.aid(rid),)).fetchone()[0],'unknown')
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?',(pid,)).fetchone()[0],'pending')
        self.assertEqual(report['remaining_questions'],1)

    def test_full_text_is_mined_after_old_sixteen_k_budget_and_longest_alias_wins(self):
        short=self.add('short','Republic',kind='organization');long=self.add('long','New Republic',kind='organization')
        doc=self.add('doc','Some document','x '*9000+'The New Republic arrived.',kind='mixed_document')
        self.store.db.commit();clean(self.store)
        targets={r[0] for r in self.store.db.execute("SELECT object_id FROM aggregate_link_evidence WHERE record_id=? AND kind='mentions'",(doc,))}
        self.assertIn(self.aid(long),targets);self.assertNotIn(self.aid(short),targets)

    def test_alias_chain_and_old_model_membership_cannot_swallow_an_unrelated_ship(self):
        ship=self.add('ship','FarStar','FarStar crew serves the New Republic.',kind='craft_individual',aliases=['FarStar crew'])
        crew=self.add('crew','FarStar crew',kind='organization',aliases=['New Republic'])
        government=self.add('gov','New Republic',kind='organization')
        other=self.add('other','Renegade',kind='craft_individual')
        # A historical semantic mismerge remains in history, but does not
        # automatically reproduce itself in the current lexical aggregates.
        eid=self.store.record(ship)['entity_id']
        self.store.db.execute('UPDATE records SET entity_id=? WHERE id=?',(eid,other))
        self.store.db.commit();clean(self.store)
        self.assertEqual(len({self.aid(r) for r in [ship,crew,government,other]}),4)
        self.assertTrue(self.store.db.execute('SELECT 1 FROM aggregate_links WHERE subject_id=? AND object_id=?',(self.aid(ship),self.aid(crew))).fetchone())

    def test_scenario_separation_text_deduplication_and_exact_offsets(self):
        text='Shared introduction.\n\nDifferent statistics: damage 5D.'
        a=self.add('a','Doctor Example Person',text,kind='person')
        b=self.add('b','Dr Example Person','Shared introduction.\n\nDifferent statistics: damage 6D.',kind='person')
        adventure=self.add('plot','Example Person',text,kind='scenario')
        self.store.db.commit();report=clean(self.store)
        self.assertEqual(self.aid(a),self.aid(b));self.assertNotEqual(self.aid(a),self.aid(adventure))
        self.assertEqual(self.store.db.execute('SELECT section FROM record_sections WHERE record_id=?',(adventure,)).fetchone()[0],'scenario')
        blocks=[r[0] for r in self.store.db.execute('SELECT text FROM aggregate_text_blocks WHERE aggregate_id=?',(self.aid(a),))]
        self.assertEqual(len(blocks),3)
        self.assertTrue(any('5D' in t for t in blocks));self.assertTrue(any('6D' in t for t in blocks))
        for row in self.store.db.execute('SELECT e.offset_start,e.offset_end,b.text,v.body FROM aggregate_text_sources e JOIN aggregate_text_blocks b ON b.aggregate_id=e.aggregate_id AND b.hash=e.hash JOIN record_versions v ON v.id=e.version_id'):
            # Whitespace-equivalent duplicates can share one displayed block;
            # source offsets still recover exactly the original occurrence.
            self.assertEqual(' '.join(row['body'][row['offset_start']:row['offset_end']].split()),' '.join(row['text'].split()))
        self.assertGreater(report['duplicate_text_blocks_collapsed'],0)

    def test_revised_policy_reopens_previously_auto_grouped_wrong_alias(self):
        a=self.add('a','FarStar',kind='craft_individual',aliases=['Lance of Endor'])
        b=self.add('b','Lance of Endor',kind='craft_individual')
        pid=self.store.enqueue('identity',a,b)
        self.store.db.execute("UPDATE proposals SET status='auto_grouped' WHERE id=?",(pid,))
        self.store.db.commit();clean(self.store)
        self.assertNotEqual(self.aid(a),self.aid(b))
        # A weak shared-type collision needs no semantic identity verdict.
        self.assertNotEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?',(pid,)).fetchone()[0],'auto_grouped')


if __name__=='__main__':unittest.main()
