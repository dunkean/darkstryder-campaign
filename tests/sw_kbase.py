"""Offline integration tests for unified imports, candidate mining and portable exports."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase import adapters, mining, pipeline
from packages.sw_kbase.store import Store, digest
from packages.sw_kbase.wiring import wire
from packages.sw_kbase.audit import audit
from packages.sw_kbase import pilot
from packages.sw_kbase.semantic import SemanticClient


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'db' / 'knowledge.sqlite')
        self.source = self.store.source('fixture', 'Fixture')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.root / relative;path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_array_reordering_preserves_native_and_content_identities(self):
        path = self.write('derived-json/SW-WEG-D6-Toolkit/NOTABLE_NPCS.json', json.dumps([{'id': 7, 'name': 'Jessa'}, {'name': 'Adrim'}]))
        initial = list(adapters.derived(self.root))
        path.write_text(json.dumps([{'name': 'Adrim'}, {'id': 7, 'name': 'Jessa'}]), encoding='utf-8')
        reordered = list(adapters.derived(self.root))
        self.assertEqual({r['name']: r['key'] for r in initial}, {r['name']: r['key'] for r in reordered})
        self.assertEqual(reordered[0]['locator']['pointer'], '/0')
        self.assertTrue(reordered[0]['key'].startswith('sha256:'))
        self.assertEqual(adapters.item('fixture', 'hash-key', 'Blaster', kind='skill')['name'], 'Blaster')

    def test_snapshot_removal_retires_old_record_without_deleting_history(self):
        path = self.write('derived-json/SW-WEG-D6-Toolkit/NOTABLE_NPCS.json', json.dumps([{'id': 1, 'name': 'Jessa'}, {'id': 2, 'name': 'Adrim'}]))
        adapters.ingest_all(self.store, self.root, self.root, ['derived'])
        path.write_text(json.dumps([{'id': 1, 'name': 'Jessa'}]), encoding='utf-8')
        result = adapters.ingest_all(self.store, self.root, self.root, ['derived'])
        self.assertEqual(result['adapters']['derived']['retired'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records').fetchone()[0], 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records WHERE active=1').fetchone()[0], 1)
        self.assertEqual(self.store.search('Adrim', universe='all'), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM record_versions').fetchone()[0], 2)
        path.write_text('[]', encoding='utf-8')
        result = adapters.ingest_all(self.store, self.root, self.root, ['derived'])
        self.assertEqual(result['adapters']['derived']['retired'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records WHERE active=1').fetchone()[0], 0)

    def test_import_is_idempotent_and_preserves_unknown_raw_fields(self):
        record = adapters.item('fixture', '1', {'name': 'Heavy blaster', 'damage': '5D', 'mystery': {'nested': True}}, kind='equipment_model')
        record.pop('dataset')
        rid, changed = self.store.ingest(self.source, **record)
        self.assertTrue(changed)
        self.assertFalse(self.store.ingest(self.source, **record)[1])
        self.assertEqual(self.store.record(rid)['payload']['mystery'], {'nested': True})
        value = self.store.db.execute("SELECT value_json FROM profile_values WHERE field='damage'").fetchone()[0]
        self.assertEqual(json.loads(value)['total_pips'], 15)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM record_versions').fetchone()[0], 1)
        self.assertTrue(audit(self.store)['ok'])
        self.store.db.execute("UPDATE record_versions SET body='tampered'")
        self.assertFalse(audit(self.store)['ok'])

    def test_source_change_replaces_current_fts_without_losing_old_passages(self):
        rid = self.store.ingest(self.source, 'x', 'Brol', 'Redfish')[0]
        self.store.ingest(self.source, 'x', 'Brol', 'Bluefish')
        self.assertEqual(self.store.search('Redfish', universe='all'), [])
        self.assertEqual(self.store.search('Bluefish', universe='all')[0]['record_id'], rid)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM passages').fetchone()[0], 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM passages_fts').fetchone()[0], 1)
        self.assertTrue(audit(self.store)['ok'])

    def test_fts_mapping_backfills_an_existing_import(self):
        rid = self.store.ingest(self.source, 'x', 'Brol', 'Oldbody')[0]
        self.store.db.execute('DROP TABLE fts_passage_rows')
        self.store.db.execute('DROP TABLE fts_record_rows')
        self.store.db.execute("DELETE FROM meta WHERE key='fts_row_mapping'")
        self.store.db.commit();path = self.store.path;self.store.close()
        self.store = Store(path)
        self.store.ingest(self.source, 'x', 'Brol', 'Newbody')
        self.assertEqual(self.store.search('Oldbody', universe='all'), [])
        self.assertEqual(self.store.search('Newbody', universe='all')[0]['record_id'], rid)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM fts_record_rows').fetchone()[0], 1)

    def test_statbase_unicode_cell_break_and_weapon_parent_are_preserved(self):
        self.write('content/transcriptions/src-6469212ce4f6d450/book.md',
                   '## Feuille 1\n# Starships\n| Sheet Index | Name | Sensors | Hull |\n| S0001 | FarStar | Passive\u2028Scan | 4D |\n'
                   '## Feuille 2\n# Starship Weapons\n| Starship Weapon Sheet Index | Starship Sheet Index | Weapon | Weapon Damage |\n| SW0001 | S0001 | Turbolaser | 5D |\n')
        records = list(adapters.statbase(self.root))
        self.assertEqual(len(records), 2)
        self.assertNotIn('_parse_warning', records[0]['payload'])
        self.assertEqual(records[0]['payload']['Hull'], '4D')
        for record in records:
            source = self.store.source(record.pop('dataset'), 'Statbase')
            self.store.ingest(source, **record)
        self.assertEqual(wire(self.store)['document_links_written'], 1)
        self.assertEqual(self.store.db.execute('SELECT kind FROM document_links').fetchone()[0], 'profile_component_of')

    def test_sql_adapter_reads_copy_without_executing_sql(self):
        self.write('swd6/pgdata/initial.sql', 'DROP TABLE records;\nCOPY race (id, name, description) FROM stdin;\n1\tWookiee\tTall\\nStrong\n\\.\n')
        records = list(adapters.sql_copy(self.root))
        self.assertEqual(records[0]['payload']['description'], 'Tall\nStrong')
        self.assertEqual(records[0]['kind'], 'species')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM records').fetchone()[0], 0)

    def test_wiki_keeps_latest_revision_and_labels_foreign_universes(self):
        self.write('d6holocron-wikiteam/history.xml', '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">'
                   '<page><title>Enterprise</title><ns>0</ns><id>7</id><revision><id>1</id><text>Old</text></revision></page>'
                   '<page><title>Enterprise</title><ns>0</ns><id>7</id><revision><id>2</id><text>[[Category:Star Trek]] New</text></revision></page>'
                   '<page><title>Image</title><ns>6</ns><id>8</id><revision><id>3</id><text>Excluded</text></revision></page></mediawiki>')
        records = list(adapters.wiki(self.root))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['locator']['revision_id'], 2)
        self.assertEqual(records[0]['universe'], 'crossover')

    def test_document_citations_are_wired_without_claiming_verified_truth(self):
        unit_source = self.store.source('transcript/book-id', 'Original units')
        unit = self.store.ingest(unit_source, '1', 'Page 1', 'Brol lives on Kathol.', kind='mixed_document')[0]
        doc = self.store.ingest(self.source, 'brol', 'Brol', 'Brol lives on Kathol.', origin='phase3_extraction',
                                locator={'source_id': 'book-id', 'profile_pages': [1], 'role_pages': [2]})[0]
        result = wire(self.store)
        self.assertEqual(result['document_links_written'], 1)
        self.assertEqual(result['unresolved_unit_references'], 1)
        link = self.store.db.execute('SELECT * FROM document_links').fetchone()
        self.assertEqual((link['subject_record_id'], link['object_record_id']), (doc, unit))
        self.assertIn('not_reverified', link['context_json'])

    def test_native_wiki_and_foreign_keys_link_records_without_semantic_acceptance(self):
        wiki = self.store.source('d6holocron-2023', 'Wiki')
        self.store.ingest(wiki, '1', 'Human', 'A species.')
        self.store.ingest(wiki, '2', 'Homo Sapiens', '#REDIRECT [[Human]]')
        self.store.ingest(wiki, '3', 'Jack', 'Jack is a [[Human|human]].')
        planets = self.store.source('swapi/planets', 'Planets')
        self.store.ingest(planets, 'p', 'Tatooine', payload={'url': 'https://swapi.info/api/planets/1'})
        people = self.store.source('swapi/people', 'People')
        self.store.ingest(people, 'l', 'Luke', payload={'url': 'https://swapi.info/api/people/1', 'homeworld': 'https://swapi.info/api/planets/1/'})
        sql_planet = self.store.source('swd6-sql/planet', 'SQL planets')
        self.store.ingest(sql_planet, '1', 'Kashyyyk', payload={'id': '1'})
        sql_race = self.store.source('swd6-sql/race', 'SQL species')
        self.store.ingest(sql_race, '2', 'Wookiee', payload={'id': '2', 'planet_id': '1'})
        self.write('swd6/pgdata/initial.sql', 'ALTER TABLE ONLY race\n ADD CONSTRAINT race_planet FOREIGN KEY (planet_id) REFERENCES planet(id);')
        result = wire(self.store, self.root)
        self.assertEqual(result['wiki_links'], 2)
        self.assertEqual(result['wiki_redirect_candidates'], 1)
        self.assertEqual(result['swapi_foreign_keys'], 1)
        self.assertEqual(result['sql_foreign_keys'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM document_links').fetchone()[0], 4)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE status='accepted'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE task='identity'").fetchone()[0], 1)

    def test_mining_is_resumable_and_does_not_accept_lexical_matches(self):
        self.store.ingest(self.source, 'a', 'Jessa Dajus', 'Jessa Dajus carries a Heavy Blaster.', kind='person', payload={'equipment': 'Heavy Blaster'})
        self.store.ingest(self.source, 'b', 'Jessa Dajus', 'DarkStryder FarStar crew.', kind='person')
        self.store.ingest(self.source, 'c', 'Heavy Blaster', 'A ranged weapon.', kind='equipment_model')
        self.store.db.commit()
        first = mining.mine(self.store, limit=1)
        rest = mining.mine(self.store)
        self.assertEqual(first['processed'] + rest['processed'], 3)
        self.assertTrue(rest['complete'])
        self.assertTrue(mining.mine(self.store)['already_complete'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM proposals WHERE status!='pending'").fetchone()[0], 0)
        self.assertGreater(self.store.db.execute("SELECT count(*) FROM proposals WHERE task='identity'").fetchone()[0], 0)
        self.assertGreater(self.store.db.execute("SELECT count(*) FROM proposals WHERE predicate='equipped_with'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM links').fetchone()[0], 0)

    def test_short_proper_names_are_mined_but_json_attribute_keys_are_not(self):
        self.store.ingest(self.source, 'k', 'Kathol', kind='location')
        self.store.ingest(self.source, 's', 'Strength', kind='skill')
        names = mining.Names(self.store)
        occurrences = list(names.occurrences('{"Strength":"2D","description":"Kathol requires Strength"}'))
        self.assertEqual([m['text'] for m in occurrences], ['Kathol', 'Strength'])

    def test_revised_relation_and_annotation_are_revoked_immediately(self):
        a = self.store.ingest(self.source, 'a', 'Brol')[0]
        b = self.store.ingest(self.source, 'b', 'Kathol')[0]
        pid = self.store.enqueue('relation', a, b, predicate='located_in')
        self.store.store_result(pid, {'accepted': True, 'decision': 'located_in'})
        mining.apply_decisions(self.store)
        self.store.store_result(pid, {'accepted': True, 'decision': 'none'})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links WHERE status='accepted'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM decision_history').fetchone()[0], 2)

    def test_relation_depending_on_revoked_mention_becomes_stale(self):
        a = self.store.ingest(self.source, 'a', 'Brol')[0]
        b = self.store.ingest(self.source, 'b', 'Kathol')[0]
        mention = self.store.enqueue('mention', a, b, context={'passage': 'Brol is on Kathol.'})
        self.store.store_result(mention, {'accepted': True, 'decision': 'refers_to'})
        mining.apply_decisions(self.store)
        relation = self.store.db.execute("SELECT id FROM proposals WHERE task='relation'").fetchone()[0]
        self.store.store_result(relation, {'accepted': True, 'decision': 'located_in'})
        mining.apply_decisions(self.store)
        self.store.store_result(mention, {'accepted': True, 'decision': 'different_referent'})
        self.assertEqual(self.store.db.execute("SELECT status FROM proposals WHERE id=?", (relation,)).fetchone()[0], 'stale')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM links WHERE status='accepted'").fetchone()[0], 0)

    def test_reactivated_source_requeues_old_job_and_restarts_mining(self):
        a = self.store.ingest(self.source, 'a', 'Brol')[0]
        b = self.store.ingest(self.source, 'b', 'Brol')[0]
        pid = self.store.enqueue('identity', a, b)
        mining.mine(self.store)
        self.store.retire(a)
        self.store.ingest(self.source, 'a', 'Brol')
        self.assertEqual(self.store.enqueue('identity', a, b), pid)
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'pending')
        self.assertEqual(mining.mine(self.store)['processed'], 2)

    def test_undo_identity_is_not_automatically_replayed(self):
        a = self.store.ingest(self.source, 'a', 'Brol')[0]
        b = self.store.ingest(self.source, 'b', 'Brol')[0]
        pid = self.store.enqueue('identity', a, b)
        self.store.store_result(pid, {'decision': 'same_entity', 'accepted': True})
        self.assertEqual(self.store.resolve_identity(pid), 'assigned')
        event = self.store.db.execute('SELECT max(id) FROM resolutions').fetchone()[0]
        self.store.undo(event)
        mining.apply_decisions(self.store)
        self.assertNotEqual(self.store.record(a)['entity_id'], self.store.record(b)['entity_id'])
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'manual_rejected')

    def test_classifier_batches_resume_and_model_contract_changes_revoke_decisions(self):
        for i in range(5):
            rid = self.store.ingest(self.source, str(i), 'NPC ' + str(i))[0]
            self.store.enqueue('type', rid)
        class Client:
            batch_size = 2
            def __init__(self, config):self.revision = config['revision']
            def signature(self, job):return digest([job, self.revision])
            def evaluate(self, jobs):
                return [{'id': job['id'], 'decision': 'person', 'accepted': True} for job in jobs]
        with patch.object(pipeline, 'SemanticClient', Client):
            self.assertEqual(pipeline.classify(self.store, {'revision': 'a'}, limit=3)['classified'], 3)
            self.assertEqual(pipeline.classify(self.store, {'revision': 'a'}, limit=0)['classified'], 2)
            self.assertEqual(pipeline.classify(self.store, {'revision': 'a'}, limit=0)['classified'], 0)
            mining.apply_decisions(self.store)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM annotations').fetchone()[0], 5)
            with self.assertRaises(ValueError):pipeline.classify(self.store, {'revision': 'b'}, limit=0)
            pipeline.invalidate_decisions(self.store)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM annotations').fetchone()[0], 0)
            self.assertEqual(pipeline.classify(self.store, {'revision': 'b'}, limit=0)['classified'], 5)

    def test_runtime_transition_preserves_decisions_and_records_both_revisions(self):
        old = {'semantic_revision': 'int8-original'}
        new = {**old, 'semantic_revision': 'nf4-5090', 'semantic_batch_size': 2}
        self.store.db.execute("INSERT INTO meta VALUES('semantic_contract',?)", (pipeline.classifier_signature(SemanticClient(old)),))
        rid = self.store.ingest(self.source, 'runtime', 'NPC')[0]
        pid = self.store.enqueue('type', rid)
        self.store.store_result(pid, {'accepted': True, 'decision': 'person', 'revision': 'int8-original'})
        mining.apply_decisions(self.store)
        before = self.store.db.execute('SELECT status,result_json FROM proposals WHERE id=?', (pid,)).fetchone()[:]
        with self.assertRaises(ValueError):pipeline.classify(self.store, new)
        with self.assertRaises(ValueError):pipeline.continue_contract(self.store, old, {**new, 'semantic_min_score': .5}, 'Invalid threshold change')
        event = pipeline.continue_contract(self.store, old, new, 'Recover on one GPU')
        self.assertTrue(event['existing_decisions_retained'])
        self.assertEqual(self.store.db.execute('SELECT status,result_json FROM proposals WHERE id=?', (pid,)).fetchone()[:], before)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM annotations').fetchone()[0], 1)
        self.assertEqual(pipeline.classify(self.store, new)['classified'], 0)
        self.assertEqual(json.loads(self.store.db.execute("SELECT value FROM meta WHERE key='semantic_contract_history'").fetchone()[0])[0]['previous_model_revision'], 'int8-original')

    def test_scoped_classification_includes_second_record(self):
        a = self.store.ingest(self.source, 'a', 'Shared')[0]
        self.assertEqual(pipeline.pending_count(self.store, scope='DarkStryder'), 0)
        b = self.store.ingest(self.source, 'b', 'Shared', 'DarkStryder campaign')[0]
        self.store.enqueue('identity', a, b)
        class Client:
            batch_size = 2
            def __init__(self, config):pass
            def signature(self, job):return digest(job)
            def evaluate(self, jobs):return [{'decision': 'different_entity', 'accepted': True} for job in jobs]
        with patch.object(pipeline, 'SemanticClient', Client):
            self.assertEqual(pipeline.pending_count(self.store, scope='DarkStryder'), 1)
            self.assertEqual(pipeline.classify(self.store, {}, scope='DarkStryder')['classified'], 1)
            self.assertEqual(pipeline.pending_count(self.store, scope='DarkStryder'), 0)

    def test_search_can_use_validated_type_and_campaign_importance(self):
        rid = self.store.ingest(self.source, 'a', 'Jessa', kind='unknown')[0]
        for task, decision in [('type', 'person'), ('importance', 'central')]:
            pid = self.store.enqueue(task, rid)
            self.store.store_result(pid, {'decision': decision, 'accepted': True})
        mining.apply_decisions(self.store)
        result = self.store.search('Jessa', kind='person', importance='central', universe='all')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['kind'], 'unknown')
        self.assertEqual(result[0]['resolved_kind'], 'person')
        self.assertEqual(result[0]['importance'], 'central')

    def test_continuous_curator_pauses_resumes_and_never_starts_writer(self):
        for i in range(7):
            rid = self.store.ingest(self.source, str(i), 'Person ' + str(i))[0]
            self.store.enqueue('type', rid)
        pause = self.store.path.parent / 'openjev.pause'
        class Client:
            batch_size = 2
            should_pause = True
            def __init__(self, config):pass
            def signature(self, job):return digest(job)
            def evaluate(self, jobs):
                if self.should_pause:pause.write_text('pause', encoding='utf-8')
                return [{'decision': 'person', 'accepted': True} for job in jobs]
        with patch.object(pipeline, 'SemanticClient', Client), patch.object(pipeline, 'WriterClient', side_effect=AssertionError('Strata must stay stopped')):
            first = pipeline.curate(self.store, {}, continuous=True, work_batch=7)
            self.assertEqual(first['state'], 'paused')
            self.assertEqual(first['classified'], 2)
            pause.unlink();Client.should_pause = False
            second = pipeline.curate(self.store, {}, continuous=True, work_batch=2)
            self.assertEqual(second['state'], 'complete')
            self.assertEqual(second['classified'], 5)
            self.assertFalse(second['writer_started'])
            self.assertEqual(mining.apply_decisions(self.store), {})
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM application_queue').fetchone()[0], 0)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM annotations').fetchone()[0], 7)
            progress = json.loads((self.store.path.parent / 'openjev-progress.json').read_text(encoding='utf-8'))
            self.assertEqual(progress['pending'], 0)

    def test_parallel_pilot_preserves_ids_and_imports_only_matching_decisions(self):
        a = self.store.ingest(self.source, 'a', 'Brol', 'A Kathol resident.')[0]
        b = self.store.ingest(self.source, 'b', 'Brol', 'A different profile.')[0]
        pid = self.store.enqueue('identity', a, b)
        target = self.root / 'pilot'
        receipt = pilot.prepare(self.store, target, limit=1, tasks=['identity'])
        self.assertEqual(receipt['jobs'], 1)
        config = {'semantic_revision': 'fixture'};client = SemanticClient(config)
        other = Store(target / 'knowledge.sqlite')
        row = other.db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
        self.assertEqual(pipeline.make_job(other, row), pipeline.make_job(self.store, self.store.db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()))
        other.store_result(pid, {'decision': 'same_entity', 'accepted': True, 'fingerprint': client.signature(pipeline.make_job(other, row))})
        other.db.execute("INSERT INTO meta VALUES('semantic_contract',?)", (pipeline.classifier_signature(client),));other.db.commit();other.close()
        self.assertEqual(self.store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0], 'pending')
        with self.assertRaises(ValueError):pilot.import_decisions(self.store, target / 'knowledge.sqlite', {'semantic_revision': 'wrong'})
        self.assertEqual(pilot.import_decisions(self.store, target / 'knowledge.sqlite', config)['imported'], 1)
        mining.apply_decisions(self.store)
        self.assertEqual(self.store.record(a)['entity_id'], self.store.record(b)['entity_id'])
        self.assertEqual(pilot.import_decisions(self.store, target / 'knowledge.sqlite', config)['imported'], 0)

    def test_parallel_pilot_does_not_import_decisions_for_changed_sources(self):
        a = self.store.ingest(self.source, 'a', 'Brol', 'Original.')[0]
        b = self.store.ingest(self.source, 'b', 'Brol', 'Profile.')[0]
        pid = self.store.enqueue('identity', a, b)
        target = self.root / 'pilot';pilot.prepare(self.store, target, limit=1)
        config = {'semantic_revision': 'fixture'};client = SemanticClient(config)
        other = Store(target / 'knowledge.sqlite');row = other.db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
        other.store_result(pid, {'decision': 'same_entity', 'accepted': True, 'fingerprint': client.signature(pipeline.make_job(other, row))})
        other.db.execute("INSERT INTO meta VALUES('semantic_contract',?)", (pipeline.classifier_signature(client),));other.db.commit();other.close()
        self.store.ingest(self.source, 'a', 'Brol', 'Changed source.')
        result = pilot.import_decisions(self.store, target / 'knowledge.sqlite', config)
        self.assertEqual(result['imported'], 0)
        self.assertNotEqual(self.store.record(a)['entity_id'], self.store.record(b)['entity_id'])

    def test_export_has_distinct_record_version_ids_and_verified_file_hashes(self):
        rid = self.store.ingest(self.source, 'x', 'Tanquilla Beach', 'A repair station.')[0]
        self.store.db.commit()
        output = self.root / 'export'
        manifest = pipeline.export(self.store, output)
        record = json.loads((output / 'records.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(record['record_id'], rid)
        self.assertNotEqual(record['record_id'], record['version_id'])
        self.assertIsNone(manifest['embedding'])
        for filename, metadata in manifest['files'].items():
            self.assertEqual(hashlib.sha256((output / filename).read_bytes()).hexdigest(), metadata['sha256'])
        with self.assertRaises(FileExistsError):pipeline.export(self.store, output)


if __name__ == '__main__':
    unittest.main()
