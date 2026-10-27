"""Pinned native decision client for the three disambiguation tasks only.

No model is started, no CUDA module is imported, and no database is opened.
The caller owns retries, cache storage and the conservative handling of errors.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import ipaddress
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from .disambiguation import CRITERIA, PROMPTS, VERSION as POLICY, complete, fingerprint

VERSION = 'sw-native-jev/1'
MAX_PROMPT_TOKENS = 2048
GPU_UUID = 'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52'
SYSTEM = 'You are a decision function. Answer with the single letter of the best option and nothing else.'
PRE = '<|im_start|>system\n' + SYSTEM + '<|im_end|>\n<|im_start|>user\n'
POST = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
ENDING = 'Answer with the label of one option and nothing else.'


class NativeJevError(RuntimeError):
    """A failed operation is never converted to evidence of redundancy."""


class NativeJevContextError(NativeJevError):
    def __init__(self, message, *, token_counts=None, reason='context_budget'):
        super().__init__(message)
        self.token_counts = token_counts or {}
        self.reason = reason
        self.inference_performed = False


class NativeJevIdentityError(NativeJevError):
    pass


class NativeJevProtocolError(NativeJevError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _stamp(path):
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _user_message(state, question):
    state_text = state if isinstance(state, str) else _json(state)
    options = [f'{chr(65 + i)}. {key}: {" ".join(description.split())}'
               for i, (key, description) in enumerate(question['criteria'].items())]
    return ('STATE:\n' + state_text + '\n\nQUESTION: ' + question['instructions'].strip()
            + '\n\nOPTIONS:\n' + '\n'.join(options) + '\n\n' + ENDING)


class JebadiahTokenCounter:
    """Exact three-piece jebadiah-v1 count with special parsing only in template.

    Uses the Rust tokenizers package, never transformers or torch. The tokenizer
    must be the hash-pinned tokenizer shipped alongside the effective GGUF.
    """
    def __init__(self, tokenizer_file):
        try:
            from tokenizers import Tokenizer
            self.template = Tokenizer.from_file(str(tokenizer_file))
            self.user = Tokenizer.from_file(str(tokenizer_file))
            # Despite its name, True makes special token strings ordinary data.
            self.user.encode_special_tokens = True
        except (ImportError, OSError, AttributeError) as error:
            raise NativeJevContextError('Exact local tokenizer unavailable; refusing inference',
                                        reason='tokenizer_unavailable') from error
        self.overhead = (len(self.template.encode(PRE, add_special_tokens=False).ids)
                         + len(self.template.encode(POST, add_special_tokens=False).ids))

    def __call__(self, request):
        return {key: self.overhead + len(self.user.encode(_user_message(request['state'], question),
                                                        add_special_tokens=False).ids)
                for key, question in request['questions'].items()}


class LoopbackTransport:
    def __init__(self, base_url, timeout=60):
        parsed = urllib.parse.urlsplit(base_url)
        try:
            local = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            local = False
        if (parsed.scheme != 'http' or not local or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Native Jev requires an explicit HTTP loopback IP and no URL credentials/path')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        # Ignore environment/system proxies. Never follow a redirect off loopback.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, method, path, payload=None):
        # Criteria order assigns A/B/C; canonical key sorting is only for state
        # serialization/fingerprints, never for the request envelope.
        raw = None if payload is None else json.dumps(payload, ensure_ascii=False,
                    separators=(',', ':'), allow_nan=False).encode('utf-8')
        request = urllib.request.Request(self.base_url + path, data=raw, method=method,
                                         headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                # All endpoint responses are small; do not accept an unbounded body.
                data = response.read(4 * 1024 * 1024 + 1)
                if len(data) > 4 * 1024 * 1024:
                    raise NativeJevProtocolError('Native server response exceeds 4 MiB')
                return json.loads(data)
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read(65536))
            except (ValueError, OSError):
                detail = {}
            message = str(detail.get('error', 'Native server HTTP error'))
            if error.code in (400, 422) and ('at most 2048' in message or
                    detail.get('code') in ('INPUT_TOO_LONG', 'STATE_TRUNCATED')):
                raise NativeJevContextError(message, reason='server_context_rejection') from error
            raise NativeJevProtocolError(f'Native server HTTP {error.code}: {message}') from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise NativeJevProtocolError('Native server unavailable or invalid JSON; no decision accepted') from error


class NativeJevClient:
    """Callable evaluate(request), compatible with DisambiguationLoop.

    Construct through from_runtime for verified local artifacts. The explicit
    constructor also supports injected transports/counters for offline tests.
    Cache keys must use fingerprint(request), not the policy-only request_id.
    evaluate() returns deferred_reason=token_budget_exceeded with no answers
    when any exact local prompt is longer than 2048 tokens. preflight() instead
    raises NativeJevContextError with those counts for cost/planning callers.
    Missing tokenization, runtime drift and malformed responses always raise.
    """
    def __init__(self, *, model, identity, transport, token_counter, guarded_files=()):
        if not token_counter:
            raise NativeJevContextError('An exact local token counter is required', reason='tokenizer_unavailable')
        self.model = model
        self._identity = copy.deepcopy(identity)
        self.transport, self.token_counter = transport, token_counter
        self._files = {Path(path): _stamp(path) for path in guarded_files}

    @property
    def identity(self):
        return copy.deepcopy(self._identity)

    @property
    def identity_fingerprint(self):
        return fingerprint({'adapter': VERSION, 'effective_runtime': self._identity})

    @classmethod
    def from_runtime(cls, runtime_dir, *, model='jeb-local:27b-v2.1-q8_0',
                     base_url='http://127.0.0.1:11437', timeout=60, verify_weights=True):
        """Read only: attest the isolated install, manifest, weights and tokenizer.

        Full weight hashing runs once on construction by default (~29 GB).
        Setting verify_weights=False trusts the previously verified content-
        addressed blob; this weaker attestation gets a separate cache identity.
        """
        root = Path(runtime_dir).resolve()
        if not re.fullmatch(r'[a-z0-9_-]+:[a-z0-9_.-]+', model):
            raise ValueError('Expected an explicit local model:tag')
        name, tag = model.split(':', 1)
        manifest_path = root / 'model-store' / 'manifests' / 'ollaya.dev' / 'library' / name / tag
        manifest_raw = manifest_path.read_bytes()
        manifest = json.loads(manifest_raw)
        guarded = [manifest_path]

        def blob(descriptor, verify=True):
            digest = descriptor.get('digest', '')
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise NativeJevIdentityError('Invalid content-addressed model descriptor')
            path = root / 'model-store' / 'blobs' / digest.replace(':', '-')
            if path.stat().st_size != descriptor['size'] or (verify and _sha(path) != digest[7:]):
                raise NativeJevIdentityError('Model artifact digest or size mismatch')
            guarded.append(path)
            return path

        config = json.loads(blob(manifest['config']).read_text(encoding='utf-8'))
        layers = {item['mediaType']: item for item in manifest['layers']}
        decision = json.loads(blob(layers['application/vnd.ollaya.decision']).read_text(encoding='utf-8'))
        calibration_desc = layers['application/vnd.ollaya.calibration']
        calibration = json.loads(blob(calibration_desc).read_text(encoding='utf-8'))
        weight_desc = layers['application/vnd.ollaya.weights.gguf']
        blob(weight_desc, verify=verify_weights)
        if (decision.get('layout') != 'jebadiah-v1' or decision.get('max_prompt_tokens') != MAX_PROMPT_TOKENS
                or config.get('family') != 'jebadiah' or decision['llama']['plan'] != 'cold'):
            raise NativeJevIdentityError('Unsupported native model layout, context budget or evaluation plan')
        if decision['gguf']['sha256'] != weight_desc['digest'][7:]:
            raise NativeJevIdentityError('Decision metadata and weight descriptor disagree')
        tokenizer_dir = root / 'upstream-v2.1'
        contract_path = tokenizer_dir / 'prompt_contract.json'
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        template_path = tokenizer_dir / 'chat_template.jinja'
        tokenizer_path = tokenizer_dir / 'tokenizer.json'
        if (_sha(template_path) != contract['chat_template_sha256'] or
                contract['prompt_source_sha256'] != decision['upstream']['prompt_source_sha256']):
            raise NativeJevIdentityError('Tokenizer template and native prompt contract disagree')
        counter = JebadiahTokenCounter(tokenizer_path)
        expected_labels = decision['labels']
        if any(counter.template.encode(label, add_special_tokens=False).ids != [token]
               for label, token in zip(expected_labels['strings'], expected_labels['ids'])):
            raise NativeJevIdentityError('Tokenizer labels differ from effective GGUF readout')
        runtime_files = {'ollaya': root / 'install' / 'bin' / 'ollaya.exe',
                         'runner': root / 'install' / 'lib' / 'ollaya' / 'ollaya-cuda-runner.exe',
                         'llama': root / 'install' / 'lib' / 'ollaya' / 'llama' / 'llama.dll',
                         'cuda': root / 'install' / 'lib' / 'ollaya' / 'cuda_v13' / 'ggml-cuda.dll',
                         'cublas': root / 'install' / 'lib' / 'ollaya' / 'cuda_v13' / 'cublas64_13.dll',
                         'cublas_lt': root / 'install' / 'lib' / 'ollaya' / 'cuda_v13' / 'cublasLt64_13.dll'}
        # Include the shipped CPU/host and CUDA dependencies as well: replacing
        # a math library must not reuse a calibrated result from another build.
        for folder in ('llama', 'cuda_v13'):
            for path in sorted((root / 'install' / 'lib' / 'ollaya' / folder).glob('*.dll')):
                runtime_files[f'{folder}/{path.name}'] = path
        guarded.extend([contract_path, template_path, tokenizer_path, Path(__file__), *runtime_files.values()])
        launcher = root / 'start-server.ps1'
        launcher_text = launcher.read_text(encoding='utf-8')
        if f"$env:CUDA_VISIBLE_DEVICES='{GPU_UUID}'" not in launcher_text:
            raise NativeJevIdentityError('Isolated launcher does not pin the authorized 5090 UUID')
        guarded.extend([launcher, root / 'server.pid'])
        transport = LoopbackTransport(base_url, timeout)
        version = transport('GET', '/api/version')['version']
        identity = {'adapter': VERSION, 'adapter_sha256': _sha(Path(__file__)), 'model': model,
                    'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
                    'weights_sha256': weight_desc['digest'][7:], 'weights_fully_hashed': verify_weights,
                    'quantization': decision['gguf']['quantization'], 'source': config['source'],
                    'template_sha256': contract['chat_template_sha256'],
                    'tokenizer_sha256': _sha(tokenizer_path), 'layout': decision['layout'],
                    'tokenizers_version': importlib.metadata.version('tokenizers'),
                    'prompt_source_sha256': contract['prompt_source_sha256'],
                    'renderer_sha256': fingerprint({'pre': PRE, 'post': POST, 'ending': ENDING}),
                    'max_prompt_tokens': MAX_PROMPT_TOKENS, 'evaluation_plan': decision['llama'],
                    'calibration_sha256': calibration_desc['digest'][7:], 'calibration': calibration,
                    'runtime_version': version, 'runtime_sha256': {key: _sha(path) for key, path in runtime_files.items()},
                    'launcher_sha256': _sha(launcher),
                    'device': 'cuda:0', 'configured_cuda_uuid': GPU_UUID,
                    'gpu_uuid_attestation': 'isolated pinned launcher; HTTP API exposes only cuda:0'}
        client = cls(model=model, identity=identity, transport=transport, token_counter=counter,
                     guarded_files=guarded)
        client._check_runtime()
        return client

    def _check_files(self):
        try:
            if any(_stamp(path) != stamp for path, stamp in self._files.items()):
                raise NativeJevIdentityError('Pinned local runtime artifact changed; reconstruct the native client')
        except OSError as error:
            raise NativeJevIdentityError('Pinned local runtime artifact disappeared') from error

    def _check_runtime(self):
        self._check_files()
        version = self.transport('GET', '/api/version')
        shown = self.transport('POST', '/api/show', {'model': self.model})
        status = self.transport('GET', '/api/ps')
        if not all(isinstance(value, dict) for value in (version, shown, status)) or not isinstance(status.get('models'), list):
            raise NativeJevProtocolError('Invalid native runtime metadata response')
        active = status['models']
        if any(not isinstance(row, dict) for row in active):
            raise NativeJevProtocolError('Invalid loaded-model metadata')
        loaded = next((row for row in active if row.get('name') == self.model), None)
        if (version.get('version') != self._identity['runtime_version'] or
                shown.get('digest') != self._identity['manifest_sha256'] or not loaded or
                loaded.get('digest') != shown['digest'] or loaded.get('device') != 'cuda:0' or
                loaded.get('details', {}).get('quantization_level') != self._identity['quantization']):
            raise NativeJevIdentityError('Native server/model/device identity changed or the pinned model is not hot')

    def validate_runtime(self):
        """Revalidate before a caller replays its own persistent cache."""
        self._check_runtime()
        return self.identity

    def _request(self, request):
        request = copy.deepcopy(request)
        if set(request) - {'model', 'state', 'questions'} or 'state' not in request:
            raise ValueError('Expected only state, questions and optional model')
        if request.get('model', self.model) != self.model:
            raise NativeJevIdentityError('Request model differs from the pinned runtime')
        questions = request.get('questions')
        if not isinstance(questions, dict) or not questions or set(questions) - set(PROMPTS):
            raise ValueError('Only entity_match, source_contribution and link_validity are supported')
        for task, q in questions.items():
            if (q != {'type': 'choice', 'instructions': PROMPTS[task], 'criteria': CRITERIA}
                    or tuple(q['criteria']) != tuple(CRITERIA)):
                raise ValueError('Native request must use the exact disambiguation prompt and yes/no/unknown criteria')
        if not complete(request['state']):
            raise NativeJevContextError('Incomplete evidence; refusing native inference', reason='incomplete_context')
        if not isinstance(request['state'], (str, dict, list)):
            raise ValueError('Native state must be a string, object or array')
        _json(request)  # Reject NaN, infinity and non-JSON values before HTTP.
        request['model'] = self.model
        return request

    def preflight(self, request):
        request = self._request(request)
        counts = self.token_counter(request)
        if set(counts) != set(request['questions']) or any(type(n) is not int or n < 1 for n in counts.values()):
            raise NativeJevContextError('Invalid exact token counter output', reason='tokenizer_unavailable')
        if any(n > MAX_PROMPT_TOKENS for n in counts.values()):
            raise NativeJevContextError('Prompt exceeds 2048 complete tokens per question; no truncation performed',
                                        token_counts=counts)
        return counts

    def _fingerprint(self, request):
        return fingerprint({'policy': POLICY, 'adapter': VERSION, 'effective_runtime': self._identity,
                            'request': request})

    def fingerprint(self, request):
        request = self._request(request)
        self._check_runtime()
        return self._fingerprint(request)

    def evaluate(self, request):
        request = self._request(request)
        try:
            counts = self.preflight(request)
        except NativeJevContextError as error:
            if error.reason != 'context_budget':
                raise
            self._check_files()
            return {'deferred_reason': 'token_budget_exceeded', 'model': self.model,
                    'max_prompt_tokens': MAX_PROMPT_TOKENS, 'question_tokens': error.token_counts,
                    'inference_performed': False, '_native_jev': {
                        'request_fingerprint': self._fingerprint(request),
                        'identity_fingerprint': self.identity_fingerprint,
                        'effective_runtime': self.identity, 'inference_performed': False}}
        self._check_runtime()
        response = self.transport('POST', '/api/decide', {**request, 'keep_alive': -1})
        if not isinstance(response, dict) or response.get('state_truncated') is not False or response.get('model') != self.model:
            raise NativeJevProtocolError('Native response model mismatch or missing/non-false truncation flag')
        usage = response.get('usage', {})
        if not isinstance(usage, dict) or usage.get('output_tokens') != 0 or usage.get('input_tokens') != sum(counts.values()):
            raise NativeJevProtocolError('Native generation/token usage differs from the exact local preflight')
        answers = response.get('answers')
        if not isinstance(answers, dict) or set(answers) != set(request['questions']):
            raise NativeJevProtocolError('Native answer set differs from requested tasks')
        raw = {}
        normalized = {}
        for task, answer in answers.items():
            values = answer.get('probabilities') if isinstance(answer, dict) else None
            if not isinstance(answer, dict) or answer.get('type') != 'choice' or not isinstance(values, dict) or set(values) != set(CRITERIA):
                raise NativeJevProtocolError('Expected choice probabilities yes/no/unknown from native readout')
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                   or not 0 <= x <= 1 for x in values.values()):
                raise NativeJevProtocolError('Native probabilities are invalid')
            total = sum(values.values())
            if total <= 0 or abs(total - 1) > .0002:
                raise NativeJevProtocolError('Native probabilities do not sum to one within API rounding tolerance')
            choice = answer.get('choice')
            if choice not in CRITERIA or values[choice] < max(values.values()):
                raise NativeJevProtocolError('Native choice disagrees with its probabilities')
            raw[task] = copy.deepcopy(values)
            probs = {key: values[key] / total for key in CRITERIA}
            normalized[task] = {**answer, 'probabilities': probs,
                                'confidence': max(0., (3 * max(probs.values()) - 1) / 2)}
        self._check_runtime()  # Do not accept an in-flight model/catalog replacement.
        return {**response, 'answers': normalized, '_native_jev': {
            'request_fingerprint': self._fingerprint(request), 'identity_fingerprint': self.identity_fingerprint,
            'effective_runtime': self.identity, 'question_tokens': counts, 'raw_probabilities': raw,
            'probability_normalization': 'divide by sum; only native four-decimal rounding repaired',
            'inference_performed': True}}

    __call__ = evaluate
