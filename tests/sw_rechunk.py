import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.document_sections import split_sections, _structural_sections, VERSION
from packages.sw_kbase import enrichment
from packages.sw_kbase.rechunk import rebuild,activate
from packages.sw_kbase.store import Store


class ChunkTests(unittest.TestCase):
    def lossless(self,body):
        rows=split_sections(body)
        for row in rows:
            self.assertEqual(body[row['offset_start']:row['offset_end']],row['body'])
        for atom in _structural_sections(body):
            self.assertEqual(sum(r['offset_start']<=atom['offset_start'] and r['offset_end']>=atom['offset_end'] for r in rows),1)
        return rows

    def test_d6db_labels_values_and_dice_form_contextual_blocks(self):
        body="Kl'aal\n\nCivilians\n\nMove\n\n10\n\nForce Pts\n\n3\n\nAttributes & Skills\n\nDexterity4D\n\nBlaster6D\n\ndodge5D+1\n\nDescription\n\nHe serves as scout aboard the FarStar."
        rows=self.lossless(body)
        self.assertLessEqual(len(rows),4)
        self.assertTrue(any('Move\n\n10' in r['body'] and 'Force Pts\n\n3' in r['body'] for r in rows))
        self.assertFalse(any(r['body'] in {'Move','10','3','Kl\'aal','Attributes & Skills'} for r in rows))

    def test_wiki_and_plain_headings_attach_to_their_prose(self):
        for title in ['==Biography==','Background','# Background','<h2>Background</h2>','Kanushka :','Key structural features observed:','[[File:Cybernetic.jpg|thumb]]']:
            rows=self.lossless(title+'\n\nHe served as a scout aboard the expedition ship.')
            self.assertEqual(len(rows),1);self.assertIn(title,rows[0]['body'])

    def test_dice_inside_narrative_does_not_merge_paragraphs(self):
        body='He possesses a rare 3D bonus when acting in complete darkness.\n\nHe abandoned his previous employer after the expedition arrived.'
        self.assertEqual(len(self.lossless(body)),2)

    def test_label_stays_with_definition_after_a_stat_block(self):
        body='Dexterity4D\n\nBlaster6D\n\nPhysical description:\n\nHe wears a long cloak and keeps his face hidden.'
        rows=self.lossless(body)
        self.assertTrue(any('Physical description:' in r['body'] and 'face hidden.' in r['body'] for r in rows))
        body='Move:\n\n10\n\nForce Pts:\n\n3'
        self.assertEqual(len(self.lossless(body)),1)

    def test_size_boundary_never_detaches_scalar_from_label(self):
        body='\n\n'.join('Sensor description '+str(i)+'\n\nMove\n\n10' for i in range(150))
        rows=self.lossless(body);self.assertGreater(len(rows),1)
        self.assertTrue(all(not r['body'].endswith('Move') and not r['body'].startswith('10') for r in rows))

    def test_short_complete_docs_are_kept_not_padded_or_dropped(self):
        rows=self.lossless('Move: 10')
        self.assertEqual(rows[0]['body'],'Move: 10');self.assertTrue(rows[0]['short_complete_document'])

    def test_trailing_title_and_code_preserve_every_source_span(self):
        self.lossless('# Profile\n\nHe flew away.\n\n## Notes')
        self.lossless('# Dice\n\n```python\nprint(3)\n```\n\nEnd.')


class RebuildTests(unittest.TestCase):
    def test_resume_keeps_empty_source_records_without_inventing_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=Store(root/'main.sqlite')
            try:
                s=main.source('empty','empty');main.ingest(s,'empty','Empty source','',kind='document')
                main.db.commit();clean(main)
                old=root/'old';new=root/'new'
                enrichment.plan(main,old,policy='sw-enrichment/3-grouped-paragraphs-no-global',units_policy='document-sections/1')
                rebuild(main,old,new)
                d=sqlite3.connect(new/'enrichment.sqlite');d.execute("UPDATE meta SET value_json='\"planning\"' WHERE key='state'");d.commit();d.close()
                result=rebuild(main,old,new,resume=True)
                self.assertEqual(result['documents'],1);self.assertEqual(result['paragraphs'],0)
            finally:main.close()

    def test_rebuild_archives_all_votes_and_preserves_reference_and_offsets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=Store(root/'main.sqlite')
            for dataset,body in [('ref','Pilot flies the ship.'),('candidate','Move\n\n10\n\nForce Pts\n\n3')]:
                s=main.source(dataset,dataset);main.ingest(s,'pilot','Pilot',body,kind='person')
            main.db.commit();clean(main)
            old=root/'old';new=root/'new'
            enrichment.plan(main,old,config={'source_priorities':{'ref':1000}},paragraph_splitter=_structural_sections,
                policy='sw-enrichment/3-grouped-paragraphs-no-global',units_policy='document-sections/1')
            db=sqlite3.connect(old/'enrichment.sqlite')
            db.execute("update paragraphs set status='add',result_json='{}' where status='pending'")
            db.execute("update documents set status='scored',result_json='{}' where role='candidate'")
            db.execute("insert into requests values('old-request','{}','{}','{}',1)");db.commit()
            reference=db.execute('select reference_id from entities').fetchone()[0];db.close()
            before=hashlib.sha256((old/'enrichment.sqlite').read_bytes()).hexdigest()
            report=rebuild(main,old,new)
            self.assertEqual(report['model_calls'],0);self.assertEqual(report['archived_requests'],1)
            self.assertEqual(before,hashlib.sha256((old/'enrichment.sqlite').read_bytes()).hexdigest())
            db=sqlite3.connect(new/'enrichment.sqlite');db.row_factory=sqlite3.Row
            self.assertEqual(db.execute('select reference_id from entities').fetchone()[0],reference)
            self.assertEqual(db.execute('select count(*) from requests').fetchone()[0],0)
            self.assertEqual(db.execute("select count(*) from documents where role='candidate' and status='pending'").fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from legacy_paragraph_map').fetchone()[0],report['old_paragraphs'])
            self.assertEqual(enrichment.meta(db)['units_policy'],VERSION)
            enrichment.verify(main,db)
            expected=[tuple(r) for r in db.execute('SELECT id,number,body,unit_json FROM paragraphs ORDER BY id')];db.close()
            streamed=root/'streamed';rebuild(main,old,streamed,preload=False)
            stream_db=sqlite3.connect(streamed/'enrichment.sqlite')
            try:
                self.assertEqual(expected,stream_db.execute('SELECT id,number,body,unit_json FROM paragraphs ORDER BY id').fetchall())
            finally:stream_db.close()
            partial=root/'partial';partial.mkdir()
            source_db=sqlite3.connect(new/'enrichment.sqlite');partial_db=sqlite3.connect(partial/'enrichment.sqlite')
            source_db.backup(partial_db);source_db.close()
            partial_db.execute("UPDATE meta SET value_json='\"planning\"' WHERE key='state'")
            partial_db.execute("DELETE FROM legacy_paragraph_map WHERE new_paragraph_id IN (SELECT p.id FROM paragraphs p JOIN documents d ON d.id=p.document_id WHERE d.role='candidate')")
            partial_db.execute("DELETE FROM paragraphs WHERE document_id IN (SELECT id FROM documents WHERE role='candidate')")
            partial_db.execute("DELETE FROM documents WHERE role='candidate'");partial_db.commit();partial_db.close()
            resumed=rebuild(main,old,partial,resume=True)
            self.assertTrue(resumed['cpu_rebuild_resumed']);self.assertEqual(resumed['old_paragraphs'],report['old_paragraphs'])
            partial_db=sqlite3.connect(partial/'enrichment.sqlite')
            try:self.assertEqual(expected,partial_db.execute('SELECT id,number,body,unit_json FROM paragraphs ORDER BY id').fetchall())
            finally:partial_db.close()
            pointer={'enrichment':str(old),'control':str(root/'control')}
            checkpoint={'selection_database':str(old/'enrichment.sqlite'),'cached_requests':1}
            (root/'active-reference-workflow.json').write_text(json.dumps(pointer))
            (root/'pause-checkpoint.json').write_text(json.dumps(checkpoint))
            with self.assertRaisesRegex(ValueError,'paused'):activate(main,new)
            (root/'openjev.pause').write_text('user pause')
            result=activate(main,new);self.assertFalse(result['started'])
            self.assertEqual(json.loads((root/'pause-checkpoint.json').read_text())['cached_requests'],0)
            self.assertEqual(json.loads((new/'control-before'/'active-reference-workflow.json').read_text()),pointer)
            activate(main,new)
            self.assertEqual(json.loads((root/'pause-checkpoint.json').read_text())['archived_selection_database'],str(old/'enrichment.sqlite'))
            self.assertTrue((root/'openjev.pause').exists());main.close()

    def test_changed_source_is_rejected_and_original_plan_survives(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=Store(root/'main.sqlite');s=main.source('ref','ref')
            main.ingest(s,'pilot','Pilot','Original text.');main.db.commit();clean(main)
            old=root/'old';enrichment.plan(main,old,policy='sw-enrichment/3-grouped-paragraphs-no-global',units_policy='document-sections/1')
            main.ingest(s,'pilot','Pilot','Changed text.');main.db.commit()
            with self.assertRaisesRegex(ValueError,'version'):rebuild(main,old,root/'new')
            self.assertTrue((old/'enrichment.sqlite').is_file());main.close()


if __name__=='__main__':unittest.main()
