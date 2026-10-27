import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase import provenance
from packages.sw_kbase.review_workspace import _document
from packages.sw_kbase.store import Store


def parse(dataset='web/d6db-compendium', body='', scope=None, payload=None, locator=None, registry=None):
    return provenance.extract({'source_id': 'source-1', 'dataset': dataset, 'body': body,
        'scope_json': scope or {}, 'payload_json': payload or {}, 'locator_json': locator or {}}, registry)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.registry = provenance.Registry([{'id':'book-1','name':'WEG40209 - Star Wars - Darkstryder Campaign','collection':'darkstryder'}],
            [{'Title':"Cracken's Threat Dossier",'WEG Number':'WEG 40139'}])

    def test_web_origin_and_book_citation_are_distinct(self):
        value = parse(body="Source: Cracken's Threat Dossier (page 76)\nCopy to Homebrew", locator={'url':'https://d6db.com/compendium/ships/example'}, registry=self.registry)
        self.assertEqual(value['acquisition']['label'],'D6DB')
        work = value['works'][0]
        self.assertEqual((work['kind'],work['code'],work['pages']),('book','WEG40139','76'))
        self.assertEqual(work['page_basis'],'cited_page')
        proof = work['evidence'][0]
        self.assertEqual(proof['excerpt'], "Source: Cracken's Threat Dossier (page 76)\nCopy to Homebrew"[proof['offset_start']:proof['offset_end']])

    def test_worksheet_unit_is_not_a_book_page(self):
        value=parse('statbase/Starships',scope={'source':"Cracken's Threat Dossier (page 76)", 'data_source':'Starship Compendium (page 268)'},locator={'unit':6,'line':3703},registry=self.registry)
        self.assertEqual([(w['pages'],w['relationship']) for w in value['works']],[('76','cites'),('268','compilation')])

    def test_local_homemade_has_pdf_pages_and_original_content_title(self):
        value=parse('phase3/src-addon',scope={'source_title':'DS Shintel','collection':'addons'},locator={'source_id':'src-addon','profile_pages':[2,4]},registry=self.registry)
        self.assertEqual(value['acquisition']['type'],'local_extraction')
        self.assertEqual(value['works'][0]['kind'],'homemade')
        self.assertEqual(value['works'][0]['pages'],'2, 4')
        self.assertEqual(value['works'][0]['page_basis'],'pdf_page')

    def test_missing_book_is_not_replaced_by_site_name(self):
        value=parse(body='A blaster. Copy to Homebrew.')
        self.assertEqual(value['status'],'not_stated');self.assertEqual(value['works'],[])
        value=parse(body='Source: Swrpggm.com')
        self.assertEqual(value['status'],'citation_unresolved');self.assertEqual(value['works'][0]['kind'],'web_content')

    def test_ambiguous_abbreviations_remain_unexpanded(self):
        value=parse('web/seawolf-atlas',body='Source: AJ07,AJ15,PSG')
        self.assertEqual(value['works'][0]['kind'],'unresolved_citation')
        self.assertEqual(value['works'][0]['raw_citation'],'AJ07,AJ15,PSG')

    def test_wiki_links_are_not_all_books_and_quotes_do_not_hide_source(self):
        value=parse('d6holocron-2023',body="Lives on [[Coruscant]].\n'''Source:''' [[Cracken's Threat Dossier]] (page 76)",registry=self.registry)
        self.assertEqual(len(value['works']),1)
        self.assertEqual(value['works'][0]['code'],'WEG40139')
        value=parse('web/wookieepedia-darkstryder',body='[[Coruscant]]\n==Sources==\n*[[Darkstryder Campaign]]\n==Equipment==\n*[[Heavy blaster]]',registry=self.registry)
        self.assertEqual(len(value['works']),1);self.assertEqual(value['works'][0]['code'],'WEG40209')

    def test_child_weapon_source_does_not_become_ship_source(self):
        value=parse(payload={'source':'Book A','weapons':[{'source':'Book B'}]})
        self.assertEqual([w['title'] for w in value['works']],['Book A'])

    def test_template_mapping_keeps_exact_proof_even_when_book_already_listed(self):
        body='==Sources==\n*[[Darkstryder Campaign]]\n*{{DarkStryder}}'
        value=parse('web/wookieepedia-darkstryder',body=body,registry=self.registry)
        self.assertEqual(len(value['works']),1)
        self.assertEqual(len(value['works'][0]['evidence']),2)
        for proof in value['works'][0]['evidence']:
            self.assertEqual(body[proof['offset_start']:proof['offset_end']],proof['excerpt'])
        value=parse('web/wookieepedia-darkstryder',body='{{DarkStryder|book=The DarkStryder Campaign|adventure=Feeding Time}}',registry=self.registry)
        self.assertEqual(value['works'][0]['code'],'WEG40209')

    def test_targeted_refresh_preserves_other_datasets(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'knowledge.sqlite')
            for dataset in ['web/first','web/second']:
                s=store.source(dataset,dataset);store.ingest(s,'one','Pilot','Source: Book A')
            store.db.commit();provenance.run(store,apply=True)
            prior=store.db.execute("SELECT p.run_id FROM document_provenance p JOIN records r ON r.id=p.record_id JOIN sources s ON s.id=r.source_id WHERE s.dataset='web/second'").fetchone()[0]
            report=provenance.run(store,apply=True,datasets=['web/first'])
            self.assertEqual(report['documents'],1)
            self.assertEqual(store.db.execute('SELECT count(*) FROM current_document_provenance').fetchone()[0],2)
            self.assertEqual(store.db.execute("SELECT p.run_id FROM document_provenance p JOIN records r ON r.id=p.record_id JOIN sources s ON s.id=r.source_id WHERE s.dataset='web/second'").fetchone()[0],prior)
            store.close()

    def test_unsafe_origin_urls_are_rejected(self):
        for url in ['javascript:alert(1)','https://secret@example.com/']:
            self.assertIsNone(parse(locator={'url':url})['acquisition']['url'])

    def test_overlay_persistence_review_and_version_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'knowledge.sqlite')
            source=store.source('web/d6db-compendium','D6DB import')
            rid,_=store.ingest(source,'one','Pilot','Source: Darkstryder Campaign',locator={'url':'https://d6db.com/pilot'})
            store.db.commit()
            before=provenance.source_fingerprint(store)
            report=provenance.run(store,[{'id':'book-1','name':'WEG40209 - Star Wars - Darkstryder Campaign','collection':'darkstryder'}],True)
            self.assertEqual(report['documents'],1)
            self.assertEqual(before,provenance.source_fingerprint(store))
            self.assertEqual(_document(store,rid)['provenance']['works'][0]['code'],'WEG40209')
            self.assertEqual(tuple(store.db.execute('SELECT acquisition_origin,work_code FROM document_source_citations').fetchone()),('D6DB','WEG40209'))
            store.ingest(source,'one','Pilot','Changed body');store.db.commit()
            self.assertIsNone(_document(store,rid)['provenance'])
            self.assertEqual(store.db.execute('SELECT count(*) FROM document_source_citations').fetchone()[0],0)
            self.assertEqual(store.db.execute('SELECT count(*) FROM provenance_history').fetchone()[0],1)
            store.close()


if __name__=='__main__':unittest.main()
