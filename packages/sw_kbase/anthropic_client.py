"""Small explicit Anthropic adapter; credentials are never returned or persisted."""
import json
from pathlib import Path
import urllib.error
import urllib.request

API_ROOT = 'https://api.anthropic.com'
PRICING = {'source': 'https://platform.claude.com/docs/en/models/haiku-5-5/overview',
    'verified_date': '2026-10-27', 'currency': 'USD', 'per_million_input': .10,
    'per_million_output': .50, 'per_million_cache_read': .01,
    'per_million_cache_write_5m': .125, 'per_million_cache_write_1h': .20,
    'long_prompt_threshold': 100000, 'long_prompt_multiplier': 5, 'batch_multiplier': .5}


class AnthropicError(RuntimeError):
    def __init__(self, status, error_type, request_id=None):
        self.status = status; self.error_type = error_type; self.request_id = request_id
        super().__init__(f'Anthropic HTTP {status}: {error_type}')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthropicClient:
    def __init__(self, key_file, timeout=180):
        self.key_file = Path(key_file); self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        if not path.startswith('/v1/') or '?' in path or '..' in path or '://' in path:
            raise ValueError('Invalid Anthropic API path')
        key = self.key_file.read_text(encoding='utf-8-sig').strip()
        if not key or '\n' in key or '\r' in key:
            raise ValueError('Invalid API key file format')
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else None
        request = urllib.request.Request(API_ROOT + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                value = json.loads(error.read())
                error_type = value.get('error', {}).get('type', 'http_error')
            except (ValueError, OSError):
                error_type = 'http_error'
            # Never log the server's free text or the request headers.
            raise AnthropicError(error.code, error_type, error.headers.get('request-id')) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AnthropicError(0, 'network_outcome_unknown') from None

    def model(self, model):
        if not model.replace('-', '').isalnum(): raise ValueError('Invalid model ID')
        return self.request('GET', '/v1/models/' + model)

    def count(self, payload):
        keys = ('model', 'system', 'messages', 'thinking', 'output_config')
        return self.request('POST', '/v1/messages/count_tokens', {k: payload[k] for k in keys if k in payload})

    def message(self, payload):
        return self.request('POST', '/v1/messages', payload)

    def submit_batch(self, requests):
        return self.request('POST', '/v1/messages/batches', {'requests': requests})


def usage_cost(usage, *, batch=False, pricing=None):
    """Estimate from provider usage, including billed invalid/refused outputs."""
    p = pricing or PRICING
    inputs = int(usage.get('input_tokens', 0)); outputs = int(usage.get('output_tokens', 0))
    reads = int(usage.get('cache_read_input_tokens', 0)); writes = int(usage.get('cache_creation_input_tokens', 0))
    cache = usage.get('cache_creation') or {}
    hour = int(cache.get('ephemeral_1h_input_tokens', 0)); five = writes - hour
    if min(inputs, outputs, reads, writes, hour, five) < 0: raise ValueError('Invalid usage counters')
    prompt = inputs + reads + writes
    multiplier = p['long_prompt_multiplier'] if prompt > p['long_prompt_threshold'] else 1
    cost = (inputs*p['per_million_input'] + outputs*p['per_million_output'] + reads*p['per_million_cache_read']
            + five*p['per_million_cache_write_5m'] + hour*p['per_million_cache_write_1h']) * multiplier / 1_000_000
    return {'estimated_usd': cost * (p['batch_multiplier'] if batch else 1),
            'prompt_tokens': prompt, 'output_tokens': outputs, 'batch': batch,
            'billing_status': 'estimate_from_provider_usage_not_invoice'}
