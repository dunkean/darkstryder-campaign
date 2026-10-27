"""Public snapshot admission, completeness and deterministic text extraction."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.acquire import Snapshot, html_text
from packages.sw_kbase.adapters import acquired_web, ingest_all
from packages.sw_kbase.sections import category_kind, section_for
from packages.sw_kbase.store import Store
from packages.sw_kbase.public_sites import OpenD6Cards, AtlasRows, rsc_values, links, seawolf
from packages.sw_kbase.wiring import wire


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'web-test'/'swrpggm';self.folder.mkdir(parents=True)
        self.store=Store(self.root/'kb.sqlite')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def snapshot(self,keys,status='complete'):
        rows=[{'key':key,'name':'Subject '+key,'body':'Damage: 5D.','payload':{'custom_unknown_field':'kept'},
               'categories':['Weapons'],'url':'https://example.test/'+key,'format':'wordpress',
               'receipt':{'retrieved_at':'2026-10-22T00:00:00Z','sha256':'a'*64,'path':'responses/fixture.data'}} for key in keys]
        (self.folder/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        (self.folder/'status.json').write_text(json.dumps({'status':status}),encoding='utf-8')
    def test_native_id_provenance_unknown_fields_and_fan_origin(self):
        self.snapshot(['123']);r=list(acquired_web(self.root))[0]
        self.assertEqual(r['key'],'123');self.assertEqual(r['kind'],'equipment_model')
        self.assertEqual(r['payload']['custom_unknown_field'],'kept')
        self.assertEqual(r['locator']['response_sha256'],'a'*64)
        self.assertEqual(r['origin'],'fan_conversion')
    def test_partial_snapshot_cannot_retire_absent_records(self):
        self.snapshot(['a','b']);ingest_all(self.store,self.root,self.root,['web'])
        self.snapshot(['a'],'partial');ingest_all(self.store,self.root,self.root,['web'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records WHERE active=1').fetchone()[0],2)
        self.snapshot(['a'],'complete');ingest_all(self.store,self.root,self.root,['web'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records WHERE active=1').fetchone()[0],1)
    def test_repeat_import_is_unchanged(self):
        self.snapshot(['a']);ingest_all(self.store,self.root,self.root,['web'])
        self.assertEqual(ingest_all(self.store,self.root,self.root,['web'])['changed'],0)
    def test_html_stats_survive_but_scripts_do_not(self):
        text=html_text('<h1>Blaster</h1><p>Damage: <b>5D</b><br>Cost: 500</p><script>destroy()</script>')
        self.assertIn('Damage: 5D',text);self.assertIn('Cost: 500',text);self.assertNotIn('destroy()',text)
    def test_resume_uses_hash_checked_cache_without_network(self):
        snap=Snapshot(self.root/'cache');url='https://example.test/api';data=b'{"items":[1,2]}'
        path='responses/cached.data';(snap.root/path).write_bytes(data)
        snap.record({'url':url,'status':200,'path':path,'sha256':sha256(data).hexdigest()})
        resumed=Snapshot(snap.root)
        with patch('packages.sw_kbase.acquire.urlopen',side_effect=AssertionError('No network expected')):
            self.assertEqual(resumed.json(url)[0],{'items':[1,2]})
    def test_sections_and_explicit_taxonomy(self):
        self.assertEqual(category_kind(['CR90 corvettes','Legends articles']),'craft_model')
        self.assertEqual(category_kind(['Force Powers']),'force_power')
        self.assertEqual(section_for({'name':'Omens','kind':'scenario'})[0],'scenario')
        self.assertEqual(section_for({'name':'Template:Weapon','kind':'unknown'})[0],'metadata')
    def test_opend6_exact_card_offsets_preserve_nested_tables_and_unicode(self):
        card='<div class="srd-no-break content-box"><h3>Épée</h3><div><table><tr><td>5D</td></tr></table><br/></div></div>'
        raw='<main>\n'+card+'\n'+card+'</main>'
        parser=OpenD6Cards(raw);parser.feed(raw)
        self.assertEqual(len(parser.cards),2)
        self.assertEqual(parser.depth,0)
        for start,end,fragment in parser.cards:
            self.assertEqual(fragment,card)
            self.assertEqual(raw[start:end],card)
    def test_opend6_generic_rules_keep_exact_unit_and_edition(self):
        folder=self.root/'web-test'/'opend6-srd';folder.mkdir()
        row={'key':'weapons/space/blaster pistol','name':'Blaster pistol','body':'Damage: 5D',
             'kind':'equipment_model','format':'html_card','categories':['weapons'],
             'url':'https://opend6.net/srd/weapons','payload':{'source_edition':'space'},
             'scope':{'edition':'space'},'unit':{'kind':'html_card','index':3},
             'receipt':{'retrieved_at':'2026-10-22T00:00:00Z','sha256':'a'*64,'path':'responses/a.data'}}
        (folder/'records.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
        (folder/'status.json').write_text('{"status":"complete_scope"}',encoding='utf-8')
        value=list(acquired_web(self.root))[0]
        self.assertEqual(value['universe'],'generic_d6')
        self.assertEqual(value['origin'],'open_d6_reference')
        self.assertEqual(value['scope']['edition'],'space')
        self.assertEqual(value['locator']['unit']['index'],3)
        self.assertEqual(value['kind'],'equipment_model')
    def test_flight_recovers_complete_grid_and_split_multibyte_text(self):
        description='Équipement: canon\nsecond line with 0:{literal text}'
        text_record='a:T'+format(len(description.encode('utf-8')),'x')+','+description
        grid={'entries':[{'slug':'canon','name':'Canon','data':{'description':'$a','damage':'5D'}} for _ in range(75)]}
        flight=text_record+'0:'+json.dumps(grid,ensure_ascii=False)+'\n'
        # A counted text record has no newline before the next record; network
        # chunks may also split in the middle of a JSON object.
        chunks=[flight[:15],flight[15:81],flight[81:900],flight[900:]]
        raw=''.join('<script>self.__next_f.push('+json.dumps([1,c])+')</script>' for c in chunks)
        parsed=dict(rsc_values(raw))
        self.assertEqual(len(parsed['0']['entries']),75)
        self.assertEqual(parsed['0']['entries'][0]['data']['description'],description)
        self.assertEqual(parsed['0']['entries'][0]['data']['damage'],'5D')
    def test_flight_rejects_incomplete_counted_text(self):
        raw='<script>self.__next_f.push('+json.dumps([1,'a:Tff,short'])+')</script>'
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            list(rsc_values(raw))
    def test_library_contents_links_survive_navigation_and_relative_urls(self):
        raw='<nav><a href="/library/book/chapter-one#npc">Read</a></nav><script>"<a href=\"/fake\">"</script>'
        self.assertEqual(links(raw,'https://d6db.com/library/book'),{'https://d6db.com/library/book/chapter-one'})
    def test_atlas_nested_rows_preserve_exact_units_and_native_relations(self):
        group='<tr id="group-row2-9"><td><h4 class="card-title">Espèce</h4><p>Planet: Aaris</p></td></tr>'
        planet='<tr id="planet-row2-3"><td><h4 class="card-title">Aaris</h4><table>'+group+'</table></td></tr>'
        raw='<table>\n'+planet+'</table>'
        parser=AtlasRows(raw);parser.feed(raw)
        self.assertFalse(parser.active)
        self.assertEqual(len(parser.rows),2)
        for row in parser.rows:self.assertEqual(raw[row['start']:row['end']],row['html'])
        source=self.store.source('web/seawolf-atlas','atlas')
        p,_=self.store.ingest(source,'planet/3','Aaris',payload={'contains_native_keys':['group/9']})
        g,_=self.store.ingest(source,'group/9','Espèce',payload={})
        result=wire(self.store)
        self.assertEqual(result['acquired_web_native_links'],1)
        row=self.store.db.execute('SELECT * FROM document_links').fetchone()
        self.assertEqual((row['subject_record_id'],row['object_record_id'],row['kind']),(p,g,'atlas_contains_reference'))
    def test_d6db_profile_contains_native_stats_and_retains_separate_html_evidence(self):
        folder=self.root/'web-test'/'d6db-compendium';folder.mkdir()
        row={'key':'/compendium/weapons/heavy-blaster','name':'Heavy blaster','body':'Damage: 5D',
             'kind':'equipment_model','format':'html_flight','categories':['weapons'],
             'url':'https://d6db.com/compendium/weapons/heavy-blaster',
             'universe':'star_wars','origin':'fan_compilation',
             'payload':{'html':'<h1>Heavy blaster</h1>','source_data':{'damage':'5D','cost':'1500'},'unknown':'kept'},
             'scope':{'section':'reference'},'unit':{'kind':'main_article_and_public_flight_props'},
             'receipt':{'retrieved_at':'2026-10-22T00:00:00Z','sha256':'a'*64,'path':'responses/a.data'}}
        (folder/'records.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
        (folder/'status.json').write_text('{"status":"partial"}',encoding='utf-8')
        value=list(acquired_web(self.root))[0]
        self.assertEqual(value['profile'],{'damage':'5D','cost':'1500'})
        self.assertEqual(value['payload']['unknown'],'kept')
        self.assertEqual(value['payload']['html'],'<h1>Heavy blaster</h1>')
        self.assertEqual(value['locator']['response_root'],'web-test')
    def test_personal_campaign_role_is_inherited_without_erasing_npc_type(self):
        categories=[{'id':54,'name':'Star Wars d6','parent':0},
                    {'id':207,'name':'Darkstryder','parent':54},
                    {'id':392,'name':'SWDS Characters','parent':207},
                    {'id':306,'name':'SWd6 News','parent':54}]
        def post(native,name,cats):
            return {'id':native,'slug':name,'link':'https://www.theswden.net/'+name,
                    'title':{'rendered':name},'content':{'rendered':'<p>Source text.</p>'},'categories':cats}
        class Fixture:
            def json(self,url,params):
                rows=categories if url.endswith('/categories') else [] if url.endswith('/pages') else [post(1,'Lancer Brunou',[392]),post(2,'Publication news',[306])]
                return rows,{'pages':1,'total':len(rows)}
            def save(self,source,records,**metadata):return list(records)
        rows=seawolf(Fixture());npc,news=rows
        self.assertEqual(npc['kind'],'person')
        self.assertEqual(npc['scope']['section'],'scenario')
        self.assertEqual(npc['scope']['campaign_category_roots'],[207])
        self.assertEqual(npc['origin'],'fan_adaptation')
        self.assertEqual(npc['scope']['edition'],'d6')
        self.assertEqual(news['scope']['section'],'reference')
    def test_library_units_without_toc_links_are_connected_idempotently(self):
        import importlib.util
        from packages.sw_kbase.cleaning import clean
        script=Path(__file__).resolve().parents[1]/'tools/sw-kbase/sync-library-navigation.py'
        spec=importlib.util.spec_from_file_location('library_navigation',script)
        navigation=importlib.util.module_from_spec(spec);spec.loader.exec_module(navigation)
        source=self.store.source('web/d6db-library','Library')
        self.store.ingest(source,'/library/book#index','Book',kind='mixed_document',scope={'section':'metadata'})
        for n in (1,2):
            self.store.ingest(source,'/library/book/chapter-'+str(n),'Book — unit '+str(n),
                kind='mixed_document',payload={'book_slug':'book','website_unit':{'index':n,'total':2}})
        self.store.db.commit();clean(self.store)
        result,_=navigation.sync(self.store)
        self.assertEqual(result['library_units_linked'],2)
        self.assertEqual(result['book_indexes'],1)
        self.assertEqual(result['new_navigation_evidence'],2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM document_links WHERE kind='library_contains_unit'").fetchone()[0],2)
        repeated,_=navigation.sync(self.store)
        self.assertEqual(repeated['new_navigation_evidence'],0)
        self.assertEqual(result['graph_edges'],repeated['graph_edges'])

    def test_preservation_uses_readonly_uri_and_detects_changed_original(self):
        import importlib.util
        import sqlite3
        script=Path(__file__).resolve().parents[1]/'tools/sw-kbase/import-public-followup.py'
        spec=importlib.util.spec_from_file_location('followup_import',script)
        followup=importlib.util.module_from_spec(spec);spec.loader.exec_module(followup)
        source=self.store.source('fixture','Fixture')
        rid,_=self.store.ingest(source,'old','Original',body='Original evidence.')
        self.store.db.commit()
        backup=self.root/'baseline with spaces.sqlite'
        target=sqlite3.connect(backup)
        try:self.store.db.backup(target)
        finally:target.close()
        self.store.ingest(source,'new','New evidence',body='An addition.')
        self.store.db.commit()
        self.assertTrue(followup.preservation(self.store.db,backup)['ok'])
        self.store.db.execute('UPDATE record_versions SET body=? WHERE record_id=?',('Changed evidence.',rid))
        self.store.db.commit()
        result=followup.preservation(self.store.db,backup)
        self.assertEqual(result['differences'],{'record_versions':1})


if __name__=='__main__':unittest.main()
