"""Native adapter contract tests: no inference server, database or CUDA required."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from packages.sw_kbase.disambiguation import CRITERIA, PROMPTS, DisambiguationLoop
from packages.sw_kbase.native_jev import (
    GPU_UUID, LoopbackTransport, NativeJevClient, NativeJevContextError,
    NativeJevIdentityError, NativeJevProtocolError, _user_message,
)

MODEL = 'test:27b'


def request(tasks=('entity_match',), state=None):
    return {'state': {'SOURCE': 'complete unit'} if state is None else state,
            'questions': {task: {'type': 'choice', 'instructions': PROMPTS[task],
                                'criteria': CRITERIA.copy()} for task in tasks}}


class FakeServer:
    def __init__(self):
        self.calls = []
        self.version = '0.16.0'
        self.digest = 'a' * 64
        self.device = 'cuda:0'
        self.response_edit = lambda value: value
        self.count = 400

    def __call__(self, method, route, body=None):
        self.calls.append((method, route, copy.deepcopy(body)))
        if route == '/api/version':
            return {'version': self.version}
        if route == '/api/show':
            return {'digest': self.digest}
        if route == '/api/ps':
            return {'models': [{'name': MODEL, 'digest': self.digest, 'device': self.device,
                                'details': {'quantization_level': 'Q8_0'}}]}
        if route == '/api/decide':
            response = {'model': MODEL, 'state_truncated': False,
                        'usage': {'input_tokens': self.count * len(body['questions']), 'output_tokens': 0},
                        'answers': {task: {'type': 'choice', 'choice': 'yes',
                                          'probabilities': {'yes': .9800, 'no': .0099, 'unknown': .01}}
                                    for task in body['questions']}}
            return self.response_edit(response)
        raise AssertionError(route)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.identity = {'runtime_version': self.server.version,
                         'manifest_sha256': self.server.digest, 'quantization': 'Q8_0',
                         'weights_sha256': 'weights-A', 'template_sha256': 'template-A'}
        self.client = NativeJevClient(model=MODEL, identity=self.identity, transport=self.server,
                                     token_counter=lambda req: {key: self.server.count for key in req['questions']})

    def test_callable_normalizes_only_rounding_and_preserves_raw_probabilities(self):
        result = self.client(request())
        values = result['answers']['entity_match']['probabilities']
        self.assertAlmostEqual(sum(values.values()), 1, places=15)
        self.assertAlmostEqual(values['yes'], .98 / .9999)
        self.assertEqual(result['_native_jev']['raw_probabilities']['entity_match']['yes'], .98)
        self.assertTrue(result['_native_jev']['inference_performed'])
        self.assertEqual([x[1] for x in self.server.calls].count('/api/decide'), 1)

    def test_2048_inclusive_2049_rejected_locally_without_http(self):
        self.server.count = 2048
        self.client.evaluate(request())
        self.server.calls.clear()
        self.server.count = 2049
        result = self.client.evaluate(request())
        self.assertFalse(result['inference_performed'])
        self.assertEqual(result['question_tokens'], {'entity_match': 2049})
        self.assertEqual(result['deferred_reason'], 'token_budget_exceeded')
        self.assertNotIn('answers', result)
        self.assertEqual(self.server.calls, [])

    def test_budget_is_per_question_and_whole_request_rejected_before_inference(self):
        self.client.token_counter = lambda req: {'entity_match': 100, 'source_contribution': 2049}
        result = self.client.evaluate(request(('entity_match', 'source_contribution')))
        self.assertEqual(result['deferred_reason'], 'token_budget_exceeded')
        self.assertFalse(self.server.calls)

    def test_incomplete_context_never_becomes_unknown_prediction(self):
        with self.assertRaises(NativeJevContextError):
            self.client(request(state={'body': 'fragment', 'excerpt_only': True}))
        self.assertFalse(self.server.calls)

    def test_legacy_task_changed_prompt_model_or_option_order_rejected(self):
        bad = request()
        bad['questions']['entity_match']['instructions'] += ' importance?'
        reordered = request()
        reordered['questions']['entity_match']['criteria'] = dict(reversed(list(CRITERIA.items())))
        for req in (request(('link_validity',)) | {'model': 'other:27b'},
                    {'state': {}, 'questions': {'importance': {}}}, bad, reordered):
            with self.subTest(req=req), self.assertRaises((ValueError, NativeJevIdentityError)):
                self.client(req)
        self.assertFalse(self.server.calls)

    def test_effective_runtime_and_all_evidence_change_cache_fingerprint(self):
        key = self.client.fingerprint(request())
        for field in ('weights_sha256', 'template_sha256', 'quantization', 'runtime_version'):
            other = copy.deepcopy(self.identity)
            other[field] += '-different'
            client = NativeJevClient(model=MODEL, identity=other, transport=self.server,
                                     token_counter=self.client.token_counter)
            self.assertNotEqual(key, client._fingerprint(client._request(request())))
        self.assertNotEqual(key, self.client.fingerprint(request(state={'SOURCE': 'revised unit', 'revision': 2})))
        exposed = self.client.identity
        exposed['weights_sha256'] = 'mutation'
        self.assertEqual(self.client.identity['weights_sha256'], 'weights-A')

    def test_runtime_drift_and_wrong_device_block_before_inference(self):
        for field, value in (('digest', 'b' * 64), ('version', 'new'), ('device', 'cuda:1')):
            with self.subTest(field=field):
                original = getattr(self.server, field)
                setattr(self.server, field, value)
                with self.assertRaises(NativeJevIdentityError):
                    self.client(request())
                setattr(self.server, field, original)
        self.assertNotIn('/api/decide', [x[1] for x in self.server.calls])

    def test_local_artifact_change_blocks_cache_and_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / 'manifest'
            artifact.write_text('before')
            client = NativeJevClient(model=MODEL, identity=self.identity, transport=self.server,
                                     token_counter=self.client.token_counter, guarded_files=[artifact])
            artifact.write_text('after: changed length')
            with self.assertRaises(NativeJevIdentityError):
                client.fingerprint(request())
        self.assertFalse(self.server.calls)

    def test_truncation_generation_wrong_usage_and_missing_answer_are_errors(self):
        edits = [lambda r: dict(r, state_truncated=True), lambda r: dict(r, state_truncated=None),
                 lambda r: dict(r, model='other'), lambda r: dict(r, answers={}),
                 lambda r: dict(r, usage={'input_tokens': 400, 'output_tokens': 1}),
                 lambda r: dict(r, usage={'input_tokens': 399, 'output_tokens': 0})]
        for edit in edits:
            self.server.response_edit = edit
            with self.subTest(edit=edit), self.assertRaises(NativeJevProtocolError):
                self.client(request())

    def test_invalid_probability_shapes_numbers_and_choice_rejected(self):
        for probabilities in ({'yes': .9, 'no': .1}, {'yes': .9, 'no': .1, 'unknown': .2},
                              {'yes': float('nan'), 'no': 0, 'unknown': 0},
                              {'yes': True, 'no': 0, 'unknown': 0},
                              {'yes': -.1, 'no': .1, 'unknown': 1}):
            def edit(r):
                r['answers']['entity_match']['probabilities'] = probabilities
                return r
            self.server.response_edit = edit
            with self.subTest(probabilities=probabilities), self.assertRaises(NativeJevProtocolError):
                self.client(request())
        self.server.response_edit = lambda r: dict(r, answers={'entity_match': None})
        with self.assertRaises(NativeJevProtocolError):
            self.client(request())

    def test_runtime_swap_during_inference_rejects_response(self):
        def swap(response):
            self.server.digest = 'b' * 64
            return response
        self.server.response_edit = swap
        with self.assertRaises(NativeJevIdentityError):
            self.client(request())

    def test_directly_injectable_into_disambiguation_loop(self):
        loop = DisambiguationLoop({'id': 'e'}, {'id': 'r', 'body': 'Reference.'}, self.client)
        row = loop.source({'id': 's', 'body': 'Other facts.', 'identity_ambiguous': True})
        self.assertEqual(row['merge_selection'], 'retain')
        self.assertEqual(len([c for c in self.server.calls if c[1] == '/api/decide']), 1)
        sent = next(c[2] for c in self.server.calls if c[1] == '/api/decide')
        self.assertEqual(tuple(sent['questions']), ('entity_match', 'source_contribution'))

    def test_deferred_native_budget_keeps_source_and_holds_ambiguous_identity(self):
        self.server.count = 2049
        loop = DisambiguationLoop({'id': 'e'}, {'id': 'r', 'body': 'Reference.'}, self.client)
        row = loop.source({'id': 's', 'body': 'Other facts.', 'identity_ambiguous': True})
        self.assertEqual(row['merge_selection'], 'hold')
        self.assertFalse(loop.observations[0]['inference_performed'])
        self.assertEqual(loop.observations[0]['reason'], 'token_budget_exceeded')
        self.assertFalse(self.server.calls)

    def test_loopback_only_and_no_proxy_or_redirect(self):
        for url in ('https://127.0.0.1:11437', 'http://example.com', 'http://127.0.0.1@evil.test',
                    'http://127.0.0.1:11437/path', 'http://localhost:11437'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LoopbackTransport(url)
        transport = LoopbackTransport('http://127.0.0.1:11437')
        redirect = next(h for h in transport.opener.handlers if isinstance(h, __import__('urllib.request').request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'http://evil.test'))

    def test_http_payload_preserves_criteria_order_and_server_budget_error(self):
        transport = LoopbackTransport('http://127.0.0.1:11437')
        captured = []
        def open_request(req, **kwargs):
            captured.append(json.loads(req.data))
            import io
            raise urllib.error.HTTPError(req.full_url, 422, '', {}, io.BytesIO(json.dumps({
                'code': 'INVALID_REQUEST', 'error': 'prompt: at most 2048 tokens'}).encode()))
        transport.opener.open = open_request
        with self.assertRaises(NativeJevContextError):
            transport('POST', '/api/decide', request())
        self.assertEqual(tuple(captured[0]['questions']['entity_match']['criteria']), tuple(CRITERIA))

    def test_renderer_state_is_canonical_but_options_preserve_order(self):
        rendered = _user_message({'z': 'é', 'a': 1}, request()['questions']['entity_match'])
        self.assertIn('STATE:\n{"a":1,"z":"é"}', rendered)
        self.assertLess(rendered.index('A. yes:'), rendered.index('B. no:'))
        self.assertLess(rendered.index('B. no:'), rendered.index('C. unknown:'))


if __name__ == '__main__':
    unittest.main()
