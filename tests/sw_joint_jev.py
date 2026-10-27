"""Joint paragraph adapter tests; no GPU, server, model load or database."""
import copy
import unittest

from packages.sw_kbase.joint_jev import CRITERIA, RULES, JointJevClient
from packages.sw_kbase.native_jev import NativeJevContextError, NativeJevIdentityError, NativeJevProtocolError


def request(count=3, *, tokens=500):
    parts = [{'id': f'P{i+1}', 'paragraph': f'P{i+1}', 'body': f'Complete paragraph {i+1}.',
              'offset_start': i * 30, 'offset_end': i * 30 + 21, 'path': 'Description'} for i in range(count)]
    return {'model': 'clef:flash', 'state': {'ENTITY': {'id': 'e'},
        'REFERENCE': {'id': 'r', 'body': 'Reference facts.'}, 'RETAINED_EVIDENCE': [],
        'SOURCE': {'id': 's', 'version_id': 'v1'}, 'PARAGRAPHS': parts,
        'instructions': 'No global vote.', '__tokens': tokens},
        'output': {'paragraphs': {p['id']: {'yes': 'useful', 'no': 'redundant', 'unknown': 'uncertain'} for p in parts}}}


class Encoder:
    def encode(self, wire):
        n = wire['state']['__tokens']
        if n > 4096:
            raise NativeJevContextError('global budget exceeded', token_counts={'total_tokens': n}, reason='context_budget')
        return {'ids': [0] * n, 'state_tokens': n - 100, 'fixed_tokens': 100,
                'schema_tokens': 80, 'state_truncated': False}


class Server:
    def __init__(self):
        self.calls = []
        self.version, self.digest, self.device, self.precision = '0.16.0', 'a' * 64, 'cuda:0', 'F32'
        self.edit = lambda result: result

    def __call__(self, method, route, body=None):
        self.calls.append((method, route, copy.deepcopy(body)))
        if route == '/api/version':
            return {'version': self.version}
        if route == '/api/show':
            return {'digest': self.digest}
        if route == '/api/ps':
            return {'models': [{'name': 'clef:flash', 'digest': self.digest, 'device': self.device,
                                'details': {'quantization_level': self.precision}}]}
        if route == '/api/decide':
            return self.edit({'model': 'clef:flash', 'state_truncated': False,
                'usage': {'input_tokens': body['state']['__tokens'], 'output_tokens': 0},
                'answers': {key: {'type': 'choice', 'choice': 'yes',
                                  'probabilities': {'yes': .8, 'no': .1, 'unknown': .1001}} for key in body['questions']}})
        raise AssertionError(route)


class JointTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.client = JointJevClient(model='clef:flash', identity={
            'runtime_version': '0.16.0', 'manifest_sha256': self.server.digest, 'compute_precision': 'F32'},
            transport=self.server, encoder=Encoder())

    def test_one_inference_for_all_paragraphs_and_no_global_question(self):
        original = request(12)
        saved = copy.deepcopy(original)
        response = self.client(original)
        inference = [c for c in self.server.calls if c[1] == '/api/decide']
        self.assertEqual(len(inference), 1)
        self.assertEqual(set(inference[0][2]['questions']), set(original['output']['paragraphs']))
        self.assertEqual(len(inference[0][2]['questions']), 12)
        self.assertEqual(response['inference_passes'], 1)
        self.assertFalse(response['_native_jev']['global_vote_requested'])
        self.assertEqual(original, saved)
        self.assertTrue(self.client.capabilities['joint_paragraph_scores'])

    def test_percentages_are_native_normalized_probabilities(self):
        response = self.client(request())
        for values in response['paragraphs'].values():
            self.assertEqual(set(values), {'yes', 'no', 'unknown'})
            self.assertAlmostEqual(sum(values.values()), 100)
            self.assertAlmostEqual(values['yes'], 100 * .8 / 1.0001)
        self.assertEqual(response['_native_jev']['raw_probabilities']['P1']['unknown'], .1001)

    def test_full_joint_budget_4096_inclusive_4097_rejected_before_http(self):
        info = self.client.preflight(request(8, tokens=4096))
        self.assertEqual(info['total_tokens'], 4096)
        self.assertEqual(info['padded_tokens'], 4096)
        self.assertEqual(info['questions'], 8)
        self.assertFalse(self.server.calls)
        with self.assertRaises(NativeJevContextError) as error:
            self.client(request(8, tokens=4097))
        self.assertEqual(error.exception.reason, 'context_budget')
        self.assertFalse(self.server.calls)

    def test_one_question_per_supplied_paragraph_uses_shared_evidence_once(self):
        self.client(request())
        sent = next(c[2] for c in self.server.calls if c[1] == '/api/decide')
        self.assertEqual(len(sent['state']['PARAGRAPHS']), 3)
        self.assertEqual(sent['state']['JOINT_PARAGRAPH_RULES'], RULES)
        self.assertIn('earliest', RULES)
        for key, question in sent['questions'].items():
            self.assertIn(key, question['instructions'])
            self.assertNotIn('Complete paragraph', question['instructions'])
            self.assertEqual(question['criteria'], CRITERIA)

    def test_duplicate_missing_extra_ids_or_empty_body_are_not_silently_dropped(self):
        invalid = []
        x = request(); x['state']['PARAGRAPHS'][1]['id'] = 'P1'; invalid.append(x)
        x = request(); del x['output']['paragraphs']['P1']; invalid.append(x)
        x = request(); x['output']['paragraphs']['global'] = {}; invalid.append(x)
        x = request(); x['state']['PARAGRAPHS'][0]['body'] = ''; invalid.append(x)
        for req in invalid:
            with self.subTest(req=req), self.assertRaises(ValueError):
                self.client(req)
        self.assertFalse(self.server.calls)

    def test_wrong_model_or_incomplete_evidence_refused(self):
        x = request(); x['model'] = 'jeb-local:27b-v2.1-q8_0'
        with self.assertRaises(NativeJevIdentityError): self.client(x)
        x = request(); x['state']['SOURCE']['excerpt_only'] = True
        with self.assertRaises(NativeJevContextError): self.client(x)
        self.assertFalse(self.server.calls)

    def test_runtime_drift_or_wrong_device_blocks_inference(self):
        for key, value in [('version', 'new'), ('digest', 'b' * 64), ('device', 'cuda:1'), ('precision', 'F16')]:
            old = getattr(self.server, key)
            setattr(self.server, key, value)
            with self.subTest(key=key), self.assertRaises(NativeJevIdentityError): self.client(request())
            setattr(self.server, key, old)
        self.assertNotIn('/api/decide', [c[1] for c in self.server.calls])

    def test_missing_extra_outputs_generation_wrong_usage_and_truncation_fail(self):
        edits = [lambda r: dict(r, answers={}), lambda r: dict(r, state_truncated=True),
                 lambda r: dict(r, state_truncated=None), lambda r: dict(r, model='other'),
                 lambda r: dict(r, usage={'input_tokens': 500, 'output_tokens': 1}),
                 lambda r: dict(r, usage={'input_tokens': 499, 'output_tokens': 0})]
        for edit in edits:
            self.server.edit = edit
            with self.subTest(edit=edit), self.assertRaises(NativeJevProtocolError): self.client(request())

    def test_invalid_probabilities_or_missing_class_rejected(self):
        for values in [{'yes': True, 'no': 0, 'unknown': 0}, {'yes': float('nan'), 'no': 0, 'unknown': 0},
                       {'yes': .8, 'no': .2, 'unknown': .2}, {'yes': .8, 'no': .2}]:
            def edit(result):
                result['answers']['P1']['probabilities'] = values
                return result
            self.server.edit = edit
            with self.subTest(values=values), self.assertRaises(NativeJevProtocolError): self.client(request())

    def test_effective_identity_and_evidence_change_fingerprint(self):
        a = self.client(request())
        changed = request(); changed['state']['REFERENCE']['body'] += ' Different version.'
        b = self.client(changed)
        self.assertNotEqual(a['_native_jev']['request_fingerprint'], b['_native_jev']['request_fingerprint'])
        exposed = self.client.identity; exposed['runtime_version'] = 'mutated'
        self.assertEqual(self.client.identity['runtime_version'], '0.16.0')


if __name__ == '__main__':
    unittest.main()
