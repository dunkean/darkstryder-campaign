import unittest
from packages.sw_kbase.document_curation import DocumentCoverageLoop,units


def doc(key,body,**values):return {'id':key,'version_id':'version-'+key,'body':body,'name':'Subject',**values}
def no(request):return {'answers':{task:{'probabilities':{'yes':.005,'no':.99,'unknown':.005}} for task in request['questions']}}
def yes(request):return {'answers':{task:{'probabilities':{'yes':.99,'no':.005,'unknown':.005}} for task in request['questions']}}


class DocumentTests(unittest.TestCase):
    def loop(self,reference,evaluate):return DocumentCoverageLoop({'id':'entity','name':'Subject'},reference,evaluate)
    def test_subset_covered_by_cumulative_retained_documents_needs_no_model(self):
        loop=self.loop(doc('a','Has a base.\n\nOwns a ship.'),lambda request:self.fail('Exact coverage'))
        loop.source(doc('b','Unrelated information about an artifact.'))
        result=loop.source(doc('c','Owns a ship.\n\nUnrelated information about an artifact.'))
        self.assertEqual(result['reason'],'exact_unit_coverage')
        self.assertEqual(result['covered_by'],['a','b'])
    def test_full_text_copy_with_different_paragraph_wrapping(self):
        loop=self.loop(doc('a','Lives on Aaris. Has a ship.'),lambda request:self.fail('Whole text exact'))
        result=loop.source(doc('b','Lives on Aaris.\n\nHas a ship.'))
        self.assertEqual(result['reason'],'exact_normalized_text')
    def test_entire_document_exclusion_requires_every_unit(self):
        calls=[]
        def evaluate(request):calls.append(request);return no(request) if len(calls)==1 else yes(request)
        loop=self.loop(doc('a','The pilot operates a base.\n\nThe pilot owns a ship.'),evaluate)
        result=loop.source(doc('b','The pilot commands the base.\n\nThe pilot has an unusual ship.'))
        self.assertEqual(len(calls),2);self.assertEqual(result['merge_selection'],'retain')
        self.assertEqual(len(result['unit_proofs']),2)
    def test_uncertain_or_oversized_unit_cannot_exclude_document(self):
        loop=self.loop(doc('a','The pilot has a ship.'),lambda request:{'deferred_reason':'token_budget_exceeded'})
        result=loop.source(doc('b','The pilot commands a ship.'))
        self.assertEqual(result['merge_selection'],'retain')
        self.assertFalse(loop.plan()['observations'][0]['inference_performed'])
    def test_new_numbers_protect_conflicting_stat_without_model(self):
        loop=self.loop(doc('a','Hull 4D.'),lambda request:self.fail('New stats preserved'))
        result=loop.source(doc('b','Hull 5D.'))
        self.assertEqual(result['reason'],'new_numeric_or_dice_information')
    def test_distinct_profile_and_nontext_source_are_kept(self):
        loop=self.loop(doc('a','Same text.',profile={'hull':'4D'}),lambda request:self.fail('Protected'))
        self.assertEqual(loop.source(doc('b','Same text.',profile={'hull':'5D'}))['merge_selection'],'retain')
        self.assertEqual(loop.source(doc('c','',unit={'physical_page':3,'has_image':True}))['merge_selection'],'retain')
    def test_partition_retains_every_nonwhitespace_character_with_exact_offsets(self):
        source=doc('a','A long sentence. '*250+'\n\nSecond paragraph.')
        parts=units(source)
        self.assertEqual(''.join(p['body'] for p in parts),source['body'])
        for part in parts:self.assertEqual(part['body'],source['body'][part['offset_start']:part['offset_end']])

    def test_user_threshold_is_strictly_above_eighty_percent(self):
        for probability,expected in [(.80,'retain'),(.8001,'exclude_redundant')]:
            def evaluate(request):return {'answers':{'source_contribution':{'probabilities':{'yes':1-probability,'no':probability,'unknown':0}}}}
            loop=self.loop(doc('a','The pilot has a ship.'),evaluate)
            self.assertEqual(loop.source(doc('b','The pilot commands a ship.'))['merge_selection'],expected)


if __name__=='__main__':unittest.main()
