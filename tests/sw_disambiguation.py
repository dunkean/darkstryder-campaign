"""Policy tests without an inference server or database writes."""
import math
import unittest

from packages.sw_kbase.disambiguation import DisambiguationLoop, PROMPTS, probabilities


def document(key, body, **extra):
    return {'id': key, 'body': body, **extra}


def answer(yes=.98, no=.01, unknown=.01):
    return {'probabilities': {'yes': yes, 'no': no, 'unknown': unknown}}


class PolicyTests(unittest.TestCase):
    def loop(self, evaluate, **kwargs):
        return DisambiguationLoop({'id': 'entity', 'name': 'Kathol sector'},
                                  document('reference', 'Reference facts.'), evaluate, **kwargs)

    def test_catalog_is_only_disambiguation(self):
        self.assertEqual(set(PROMPTS), {'entity_match', 'source_contribution', 'link_validity'})
        self.assertIn('contradictory', PROMPTS['source_contribution'])

    def test_two_questions_share_one_request_for_ambiguous_source(self):
        calls = []
        def evaluate(request):
            calls.append(request)
            return {'answers': {key: answer() for key in request['questions']}}
        loop = self.loop(evaluate)
        row = loop.source(document('new', 'New facts.', identity_ambiguous=True))
        self.assertEqual(row['merge_selection'], 'retain')
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]['questions']), {'entity_match', 'source_contribution'})
        self.assertEqual(row['entity_score_pct'], 98)
        self.assertEqual(row['adds_information_score_pct'], 98)

    def test_contribution_compares_with_retained_union(self):
        calls = []
        def evaluate(request):
            calls.append(request)
            retained = request['state']['RETAINED_SOURCES']
            return {'answers': {'source_contribution': answer(.005, .99, .005) if retained else answer()}}
        loop = self.loop(evaluate)
        loop.source(document('b', 'Adds the location of a base.'))
        row = loop.source(document('c', 'The base location, paraphrased.'))
        self.assertEqual(row['merge_selection'], 'exclude_redundant')
        self.assertEqual(calls[1]['state']['RETAINED_SOURCES'][0]['id'], 'b')
        self.assertEqual(row['covered_by'], ['reference', 'b'])
        self.assertEqual(loop.plan()['retained_ids'], ['reference', 'b'])

    def test_wrong_identity_never_contaminates_coverage(self):
        def evaluate(request):
            return {'answers': {key: answer(.005, .99, .005) if key == 'entity_match' else answer()
                                for key in request['questions']}}
        loop = self.loop(evaluate)
        row = loop.source(document('wrong', 'Facts about another sector.', identity_ambiguous=True))
        self.assertEqual(row['merge_selection'], 'hold')
        self.assertEqual(row['reason'], 'different_entity')
        self.assertEqual(loop.plan()['retained_ids'], ['reference'])

    def test_exact_text_is_excluded_without_model(self):
        loop = self.loop(lambda request: self.fail('No model needed for exact text'))
        row = loop.source(document('copy', '  REFERENCE   facts.\n'))
        self.assertEqual(row['reason'], 'exact_normalized_text')
        self.assertEqual(row['covered_by'], ['reference'])
        self.assertFalse(loop.plan()['raw_sources_deleted'])

    def test_new_profile_and_conflict_are_never_discarded_as_exact_text(self):
        loop = self.loop(lambda request: self.fail('Known protected information needs no model'))
        for key, metadata in [('profile', {'profile': {'edition': '2e'}}),
                              ('conflict', {'preserve_reasons': ['contradiction']})]:
            row = loop.source(document(key, 'Reference facts.', **metadata))
            self.assertEqual(row['merge_selection'], 'retain')

    def test_low_yes_with_unknown_is_not_redundancy(self):
        loop = self.loop(lambda request: {'answers': {'source_contribution': answer(.005, .005, .99)}})
        row = loop.source(document('unclear', 'Ambiguous added text.'))
        self.assertEqual(row['merge_selection'], 'retain')

    def test_incomplete_or_oversized_evidence_never_discards(self):
        loop = self.loop(lambda request: self.fail('Budget must be checked before inference'),
                         max_request_chars=1000)
        row = loop.source(document('large', 'New text. ' * 1000))
        self.assertEqual(row['merge_selection'], 'retain')
        self.assertIsNone(row['adds_information_score_pct'])
        self.assertFalse(loop.observations[-1]['inference_performed'])

    def test_lexical_links_are_trusted_and_flagged_links_get_one_question(self):
        calls = []
        def evaluate(request):
            calls.append(request)
            return {'answers': {'link_validity': answer()}}
        loop = self.loop(evaluate)
        result = loop.link({'id': 'mention'}, {'text': 'Han Solo carried a heavy blaster.'})
        self.assertEqual(result['selection'], 'trust_text_mining')
        self.assertEqual(calls, [])
        result = loop.link({'id': 'membership', 'needs_review': True, 'predicate': 'member_of',
                            'subject': {'id': 'han'}, 'target': {'id': 'rebellion'}},
                           {'source_id': 'source', 'text': 'Han joined the Rebellion.'})
        self.assertEqual(result['selection'], 'valid')
        self.assertEqual(list(calls[0]['questions']), ['link_validity'])

    def test_native_probabilities_are_validated(self):
        for invalid in [answer(math.nan, .5, .5), answer(.8, .8, .8),
                        {'probabilities': {'yes': 1, 'no': 0}}]:
            with self.assertRaises(ValueError):
                probabilities(invalid)

    def test_failed_inference_can_be_retried(self):
        loop = self.loop(lambda request: {'answers': {}})
        with self.assertRaises(KeyError):
            loop.source(document('retry', 'New facts.'))
        loop.evaluate = lambda request: {'answers': {'source_contribution': answer()}}
        self.assertEqual(loop.source(document('retry', 'New facts.'))['merge_selection'], 'retain')


if __name__ == '__main__':
    unittest.main()
