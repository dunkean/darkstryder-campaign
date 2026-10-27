"""Text-only typing, preserved curation snapshots and reversible overlays."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.enrichment import input_snapshot, plan, connect, verify
from packages.sw_kbase.disambiguation import fingerprint
from packages.sw_kbase.store import Store
from packages.sw_kbase.type_mining import infer, run, source_fingerprint, record_annotation


def classify(name, body='', kind='unknown', categories=()):
    return infer({'name': name, 'body': body, 'kind': kind, 'scope': {'categories': list(categories)}})


class EvidenceTests(unittest.TestCase):
    def test_wiki_ground_vehicle_and_exact_evidence(self):
        body = "[[File:walker.jpg]]\n'''Type''': Assault walker<br />'''Scale''': Walker"
        value = classify('AT-AT', body)
        self.assertEqual(value['kind'], 'vehicle')
        evidence = value['evidence']
        self.assertEqual(body[evidence['offset_start']:evidence['offset_end']], evidence['excerpt'])
    def test_space_boat_is_craft_and_named_speeder_transport_stays_spacecraft(self):
        self.assertEqual(classify('Explorer', "'''Type''': Modified Space Boat")['kind'], 'craft')
        value = classify('Speeder Transport', 'Type: Orbital interface transport\nSkill: Space transports: V-wing', 'craft_model')
        self.assertEqual(value['kind'], 'craft')
    def test_weapon_targeting_a_vehicle_is_not_the_vehicle(self):
        self.assertEqual(classify('Anti-Vehicle Turret', 'Type: Anti-walker laser cannon')['kind'], 'equipment')
        self.assertEqual(classify('Bacta Tank', 'Type: Bacta tank')['kind'], 'equipment')
    def test_planets_systems_routes_and_surface_places_are_separate(self):
        self.assertEqual(classify('Coruscant', '{{Planet|name=Coruscant}}')['kind'], 'system')
        self.assertEqual(classify('Urdur', '{{StarSystem|name=Urdur}}')['subtype'], 'star_system')
        self.assertEqual(classify('Hydian Way', '{{HyperspaceRoute|name=Hydian Way}}')['kind'], 'stellar_route')
        self.assertEqual(classify('500 Republica', 'Planet: Coruscant\nType: Building')['kind'], 'location')
        self.assertIsNone(classify('An unknown place', 'Planet: Coruscant\nSystem: Coruscant'))
    def test_compact_json_and_parent_planet_field(self):
        body = json.dumps({'type': 'Combat landspeeder', 'scale': 'Speeder'})
        self.assertEqual(classify('Transport', body, 'craft_model')['kind'], 'vehicle')
        self.assertEqual(classify('Atlas body', json.dumps({'grid': 'M-13', 'region': 'Colonies', 'col': 11, 'row': 13}), 'location')['kind'], 'system')
    def test_known_person_is_not_retyped_for_mentions_of_vehicles(self):
        self.assertIsNone(classify('Snowspeeder Pilot', 'Type: Airspeeder\nPilot of a vehicle.', 'person'))
    def test_subject_definition_recognizes_a_route_without_typing_a_place_mention(self):
        self.assertEqual(classify('Hydian Way', 'The Hydian Way was a super-hyperroute that began at the Corporate Sector.')['kind'], 'stellar_route')
        self.assertEqual(classify('Corellian Run', "The '''Corellian Run''' was one of the largest hyperspace routes running through the galaxy.")['kind'], 'stellar_route')
        self.assertEqual(classify('Unnamed cantina', 'Travellers on the Hydian Way visit this place.')['kind'], 'location')
        self.assertEqual(classify('Rimma Trade Route', '- **Type:** Hyperspace trade route')['kind'], 'stellar_route')
    def test_droids_and_conflicting_evidence(self):
        self.assertEqual(classify('Huyang', "'''Type''': Architect droid\nGender: Male\nSpecies: Droid")['kind'], 'droid')
        value = classify('Mixed subject', categories=['Planets', 'Droids'])
        self.assertFalse(value['assigned'])
    def test_nested_equipment_type_does_not_type_the_owner_as_equipment(self):
        self.assertIsNone(classify('Unknown owner', json.dumps({'equipment': {'type': 'Blaster pistol'}})))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.main = Store(self.root/'knowledge.sqlite')
        source = self.main.source('fixture', 'Fixture')
        self.vehicle, _ = self.main.ingest(source, 'vehicle', 'Hover transport', 'Type: Combat landspeeder\nScale: Speeder', kind='craft_model')
        self.system, _ = self.main.ingest(source, 'planet', 'Planet Foo', '{{Planet|name=Foo}}', kind='location')
        self.route, _ = self.main.ingest(source, 'route', 'Hydian Way', '{{HyperspaceRoute|name=Hydian Way}}')
        self.unknown, _ = self.main.ingest(source, 'unknown', 'Unclassified note', 'No category supplied.')
        self.main.db.commit(); clean(self.main)
        self.aid = self.main.db.execute('SELECT aggregate_id FROM aggregate_members WHERE record_id=?', (self.vehicle,)).fetchone()[0]
    def tearDown(self):
        self.main.close(); self.temp.cleanup()
    def test_apply_changes_navigation_without_changing_sources_or_paused_plan(self):
        plan(self.main, self.root/'selection')
        snapshot_before = fingerprint(input_snapshot(self.main, self.aid))
        source_before = source_fingerprint(self.main)
        report = run(self.main, apply=True)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(source_before, source_fingerprint(self.main))
        self.assertEqual(snapshot_before, fingerprint(input_snapshot(self.main, self.aid)))
        self.assertEqual(self.main.db.execute('SELECT kind FROM aggregates WHERE id=?', (self.aid,)).fetchone()[0], 'craft')
        self.assertEqual(self.main.db.execute('SELECT kind FROM typed_aggregates WHERE id=?', (self.aid,)).fetchone()[0], 'vehicle')
        self.assertEqual(self.main.record(self.vehicle)['kind'], 'craft_model')
        db = connect(self.root/'selection')
        try: verify(self.main, db)
        finally: db.close()
        second = run(self.main, apply=True)
        self.assertEqual(report['aggregate_types_after'], second['aggregate_types_after'])
        self.assertEqual(self.main.db.execute('SELECT count(*) FROM type_mining_runs').fetchone()[0], 2)
    def test_source_revision_hides_stale_type(self):
        run(self.main, apply=True)
        record = self.main.record(self.vehicle)
        self.main.ingest(record['source_id'], record['source_key'], record['name'], 'A changed untyped source.')
        self.main.db.commit(); record = self.main.record(self.vehicle)
        self.assertIsNone(record_annotation(self.main, self.vehicle, record['current_version']))
        self.assertEqual(self.main.db.execute('SELECT kind FROM typed_aggregates WHERE id=?', (self.aid,)).fetchone()[0], 'craft')


if __name__ == '__main__':
    unittest.main()
