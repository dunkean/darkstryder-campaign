"""Mechanical equivalence keeps unknown rules and edition/loadout alternatives."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.curation_profiles import normalize_profile, profile_signature, preserve_reasons


class CurationProfileTests(unittest.TestCase):
    def test_provenance_and_subject_names_do_not_create_mechanical_variants(self):
        left = {'Name': 'Pilot', 'id': 'old', 'URL': 'https://a', 'Source': 'Book A',
                'Sheet Index': 3, 'HTML': '<p>navigation</p>', 'DEX': ' 3d + 1 ',
                'description': 'A quiet veteran.'}
        right = {'name': 'Captain Pilot', 'id': 'new', 'url': 'https://b', 'source': 'Book B',
                 'Entry Index': 7, 'dexterity': '3D+1', 'description': 'An experienced pilot.'}
        self.assertEqual(normalize_profile(left), normalize_profile(right))
        self.assertEqual(profile_signature(left), profile_signature(right))

    def test_changed_attribute_is_significant(self):
        self.assertNotEqual(profile_signature({'STR': '3D'}), profile_signature({'strength': '3D+1'}))

    def test_loadout_names_quantity_and_damage_remain_significant(self):
        base = {'weapons': [{'name': 'Blaster pistol', 'damage': '4D', 'quantity': 1}]}
        self.assertNotEqual(profile_signature(base), profile_signature({'weapons': [{'name': 'Blaster rifle', 'damage': '4D', 'quantity': 1}]}))
        self.assertNotEqual(profile_signature(base), profile_signature({'weapons': [{'name': 'Blaster pistol', 'damage': '4D', 'quantity': 2}]}))
        self.assertEqual(normalize_profile(base)['weapons'][0]['name'], 'Blaster pistol')

    def test_unknown_mechanics_and_nested_skills_are_not_dropped(self):
        value = {'experimental rule': {'charges': 7, 'threshold': 12},
                 'skills': {'Starship gunnery': '5d+2'},
                 'special abilities': {'Aquatic': {'description': 'Can breathe underwater.'}}}
        normalized = normalize_profile(value)
        self.assertEqual(normalized['experimentalrule']['charges'], 7)
        self.assertEqual(normalized['skills']['starshipgunnery'], {'dice': 5, 'pips': 2})
        self.assertEqual(normalized['specialabilities']['aquatic']['description'], 'Can breathe underwater.')

    def test_mechanical_description_is_preserved(self):
        result = normalize_profile({'Description': 'Adds +1D to damage.'})
        self.assertIn('description', result)
        self.assertEqual(normalize_profile({'Description': 'A gleaming silver finish.'}), {})

    def test_colliding_keys_preserve_conflicts(self):
        result = normalize_profile({'DEX': '3D', 'Dexterity': '4D'})
        self.assertEqual(len(result['attribute.dexterity']['$conflicting_values']), 2)
        self.assertEqual(result, normalize_profile({'Dexterity': '4D', 'DEX': '3D'}))

    def test_normalization_is_idempotent_and_cover_can_be_mechanical(self):
        value = {'DEX': '3D', 'Dexterity': '4D', 'cover': '+1D', 'model': 'Heavy variant'}
        normalized = normalize_profile(value)
        self.assertEqual(normalize_profile(normalized), normalized)
        self.assertEqual(profile_signature(value), profile_signature(normalized))
        self.assertIn('cover', normalized)
        self.assertIn('model', normalized)
        self.assertEqual(normalize_profile({'cover': 'https://example.invalid/art.png'}), {})

    def test_json_and_malformed_inputs(self):
        self.assertEqual(normalize_profile('{"DEX":"3D"}'), normalize_profile({'DEX': '3D'}))
        self.assertEqual(normalize_profile(None), {})
        self.assertEqual(normalize_profile('not JSON'), {'unparsed_profile': 'not JSON'})

    def test_era_alias_and_case_are_normalized(self):
        self.assertEqual(preserve_reasons({'Era': 'REPUBLIC'}, [{'epoch': ' republic '}]), [])
        self.assertEqual(preserve_reasons({'ERA': 'Empire'}, [{'epoch': 'Republic'}]), ['new_epoch'])

    def test_continuity_is_a_protected_variant(self):
        self.assertEqual(preserve_reasons({'Canon': 'Legends'}, [{'continuity': 'LEGENDS'}]), [])
        self.assertEqual(preserve_reasons({'continuity': 'Modern canon'}, [{'canon': 'Legends'}]), ['distinct_profile'])

    def test_new_combination_is_not_covered_by_separate_documents(self):
        retained = [{'edition': '1E', 'era': 'Old'}, {'edition': '2E', 'epoch': 'New'}]
        self.assertEqual(preserve_reasons({'Edition': '1e', 'epoch': 'new'}, retained), ['distinct_profile'])

    def test_known_scope_is_not_covered_by_missing_values(self):
        self.assertEqual(preserve_reasons({'edition': '2E', 'era': 'Empire'}, [{}]), ['new_edition', 'new_epoch'])
        self.assertEqual(preserve_reasons({}, [{'edition': '2E'}]), [])
        self.assertEqual(preserve_reasons({'loadout': {'armor': 'Heavy'}}, [{'loadout': {'armor': 'Light'}}]), ['distinct_profile'])


if __name__ == '__main__':
    unittest.main()
