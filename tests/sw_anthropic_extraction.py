"""Offline contracts, billing, gates, resume and exact-span regressions."""
from copy import deepcopy
import html
import json
import http.client
from pathlib import Path
import sqlite3
import re
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.anthropic_client import usage_cost
from packages.sw_kbase.anthropic_extraction import prepare, count_tokens, run, pilot_db, report, batch_file
from packages.sw_kbase.cleaning import clean
from packages.sw_kbase.grouped_paragraphs import plan, score
from packages.sw_kbase.selected_spans import selected_spans
from packages.sw_kbase.store import Store
from packages.sw_kbase.structured_article import VERSION, PROMPT, article_schema, validate_article, article_html
from packages.sw_kbase.anthropic_review import make_server
from packages.sw_kbase.anthropic_compare import render_compare


def response_for(context):
    return {'type': 'person', 'subtype': 'pilot', 'sections': [
        {'title': 'Background', 'subtitle': '', 'content': '\n\n'.join(e['text'] for e in context['texts']),
         'type': 'description', 'stats': {}}]}


class Jev:
    model = 'fixture'; identity = {'model': 'fixture'}
    capabilities = {'joint_paragraph_scores': True}
    def preflight(self, request): return {}
    def validate_runtime(self): return self.identity
    def __call__(self, request):
        return {'inference_passes': 1, 'paragraphs': {p['id']: {'yes': 96, 'no': 2, 'unknown': 2}
            for p in request['state']['PARAGRAPHS']}}


class Client:
    def __init__(self, invalid=False, crash=False, fenced=False): self.calls = 0; self.invalid = invalid; self.crash = crash; self.fenced = fenced
    def model(self, model): return {'id': model, 'capabilities': {'structured_outputs': {'supported': True}}}
    def count(self, payload): return {'input_tokens': 2500}
    def message(self, payload):
        self.calls += 1
        if self.crash: raise TimeoutError('fixture timeout')
        context = json.loads(payload['messages'][0]['content']); value = response_for(context)
        if self.invalid: value['links'] = []
        return {'model': 'claude-haiku-5-5', 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 2500, 'output_tokens': 500},
                'content': [{'type': 'text', 'text': '```json\n'+json.dumps(value)+'\n```' if self.fenced else json.dumps(value)}]}


class ContractTests(unittest.TestCase):
    def test_short_closed_contract_has_no_links(self):
        serialized = json.dumps(article_schema())
        self.assertLess(len(PROMPT.split()), 140)
        for instruction in ['omit no information', 'faithful reformulation', 'Invent nothing', 'fill no gaps']:
            self.assertIn(instruction, PROMPT)
        for key in ['"links"', '"link_ids"', '"target_id"', '"url"', '"id"', '"evidence_ids"', '"confidence"']:
            self.assertNotIn(key, serialized)
        path = Path(__file__).resolve().parents[1] / 'config/sw-rich-article.schema.json'
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), article_schema())
    def test_exact_subfragments_exclude_discarded_text(self):
        p = {'id': 'p', 'label': 'P1', 'status': 'add', 'body': 'OLD NEW', 'offset_start': 10, 'offset_end': 17,
             'result_json': json.dumps({'votes': [
                 {'decision': 'redundant', 'fragment': {'body': 'OLD ', 'offset_start': 10, 'offset_end': 14}},
                 {'decision': 'useful', 'fragment': {'body': 'NEW', 'offset_start': 14, 'offset_end': 17}}]})}
        self.assertEqual([s['body'] for s in selected_spans(p)], ['NEW'])
        p['result_json'] = p['result_json'].replace('"offset_start": 14', '"offset_start": 13')
        with self.assertRaises(ValueError): list(selected_spans(p))
    def test_price_threshold_and_cache_counters(self):
        self.assertAlmostEqual(usage_cost({'input_tokens': 100000, 'output_tokens': 1000})['estimated_usd'], .0105)
        self.assertAlmostEqual(usage_cost({'input_tokens': 100001, 'output_tokens': 1000})['estimated_usd'], .0525005)
        self.assertAlmostEqual(usage_cost({'input_tokens': 100000, 'output_tokens': 1000}, batch=True)['estimated_usd'], .00525)
        value = usage_cost({'input_tokens': 1000, 'output_tokens': 1000, 'cache_read_input_tokens': 1000,
                            'cache_creation_input_tokens': 1000})
        self.assertAlmostEqual(value['estimated_usd'], .000735)
    def test_simple_sections_escape_text_and_reject_engine_fields(self):
        context = {'texts': [{'text': '<script>bad</script>'}]}
        value = response_for(context); validate_article(value, context)
        self.assertNotIn('<script>', article_html(value))
        invalid = deepcopy(value); invalid['links'] = []
        with self.assertRaises(ValueError): validate_article(invalid, context)
        invalid = deepcopy(value); invalid['sections'] = []
        with self.assertRaises(ValueError): validate_article(invalid, context)
        invalid = deepcopy(value); invalid['sections'][0]['id'] = 'not-the-models-job'
        with self.assertRaises(ValueError): validate_article(invalid, context)
    def test_new_names_free_stats_and_conflicts_need_no_special_objects(self):
        value = {'type': 'new_category', 'subtype': 'new_subtype', 'sections': [
            {'title': 'Profile A', 'subtitle': '', 'content': '', 'type': 'stats', 'stats': {'Odd stat name': '4D+2'}},
            {'title': 'Different source', 'subtitle': 'Alternative profile', 'content': 'Another source gives a different value.',
             'type': 'conflict', 'stats': {'Odd stat name': '5D'}}]}
        validate_article(value)
        self.assertIn('4D+2', article_html(value)); self.assertIn('class="conflict"', article_html(value))
        invalid = deepcopy(value); invalid['sections'][0]['stats']['Odd stat name'] = {'dice': 4, 'pips': 2}
        with self.assertRaises(ValueError): validate_article(invalid)
        empty_subtype = deepcopy(value); empty_subtype['subtype'] = ''
        validate_article(empty_subtype)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.database = self.root / 'knowledge.sqlite'; self.selection = self.root / 'selection'
        main = Store(self.database); sid = main.source('test', 'Book')
        self.rid, _ = main.ingest(sid, 'pilot', 'Pilot', 'Pilot flies a ship and owns a blaster.', kind='person')
        main.db.commit(); clean(main); plan(main, self.selection); score(main, self.selection, Jev()); main.close()
        self.config = {'model': 'claude-haiku-5-5', 'sample_size': 1, 'quotas': {'person': 1},
            'api_key_file': str(self.root / 'secret'), 'database': str(self.database),
            'selection_database': str(self.selection / 'enrichment.sqlite'), 'min_evidence_chars': 1,
            'max_output_tokens': 12000, 'spend_limit_usd': 1}
        self.config['api_key_file'] = str(self.root / 'credential')
        (self.root / 'credential').write_text('FAKE_PRIVATE_KEY_DO_NOT_COPY', encoding='utf-8')
        self.output = self.root / 'pilot'; prepare(self.config, self.output)
        self.originals = [p.read_bytes() for p in (self.database, self.selection / 'enrichment.sqlite')]
    def tearDown(self): self.temp.cleanup()
    def test_preparation_and_count_are_not_generation_and_gate_is_enforced(self):
        client = Client(); value = count_tokens(self.output, client)
        self.assertEqual(value['counted_articles'], 1); self.assertEqual(client.calls, 0)
        with self.assertRaisesRegex(ValueError, 'GO'): run(self.output, client)
        self.assertEqual(client.calls, 0)
        for path in self.output.rglob('*'):
            if path.is_file(): self.assertNotIn(b'FAKE_PRIVATE_KEY_DO_NOT_COPY', path.read_bytes())
    def test_previous_prompt_cannot_be_counted_or_sent(self):
        db = pilot_db(self.output)
        row = db.execute('SELECT id,request_json FROM jobs').fetchone()
        payload = json.loads(row['request_json']); payload['system'] = 'Old complicated prompt'
        db.execute('UPDATE jobs SET request_json=? WHERE id=?', (json.dumps(payload), row['id']))
        db.commit(); db.close()
        client = Client()
        with self.assertRaisesRegex(ValueError, 'Obsolete'): count_tokens(self.output, client)
        (self.output / 'WAIT_FOR_USER_GO').unlink()
        with self.assertRaisesRegex(ValueError, 'Obsolete'): run(self.output, client)
        self.assertEqual(client.calls, 0)
    def test_valid_results_resume_without_rebilling_and_keep_original_databases(self):
        client = Client(); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        value = run(self.output, client); self.assertEqual(value['by_status'], {'complete': 1})
        self.assertEqual(client.calls, 1); self.assertGreater(value['estimated_actual_cost_usd'], 0)
        run(self.output, client); self.assertEqual(client.calls, 1)
        exported = json.loads(next(p for p in (self.output / 'articles').glob('*.json') if not p.name.endswith('.metadata.json')).read_text(encoding='utf-8'))
        self.assertEqual(set(exported), {'type', 'subtype', 'sections'})
        metadata = json.loads(next((self.output / 'articles').glob('*.metadata.json')).read_text(encoding='utf-8'))
        self.assertIn('evidence', metadata)
        self.assertNotIn('links', exported); self.assertNotIn('link_ids', json.dumps(exported))
        self.assertEqual(self.originals, [p.read_bytes() for p in (self.database, self.selection / 'enrichment.sqlite')])
    def test_invalid_response_is_saved_and_counted_as_billable(self):
        client = Client(invalid=True); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        value = run(self.output, client); self.assertEqual(value['by_status'], {'invalid': 1})
        self.assertGreater(value['estimated_actual_cost_usd'], 0)
        run(self.output, client); self.assertEqual(client.calls, 1)
    def test_json_fence_is_parsed_without_a_second_call(self):
        client = Client(fenced=True); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        value = run(self.output, client)
        self.assertEqual(value['by_status'], {'complete': 1}); self.assertEqual(client.calls, 1)
    def test_received_response_recovers_without_another_api_call(self):
        client = Client(); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        run(self.output, client)
        db = pilot_db(self.output)
        db.execute("UPDATE jobs SET status='received',article_json=NULL"); db.commit(); db.close()
        value = run(self.output, client)
        self.assertEqual(value['by_status'], {'complete': 1}); self.assertEqual(client.calls, 1)
    def test_unknown_network_outcome_is_not_retried(self):
        client = Client(crash=True); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        with self.assertRaises(TimeoutError): run(self.output, client)
        with self.assertRaisesRegex(ValueError, 'unknown outcome'): run(self.output, client)
        self.assertEqual(client.calls, 1)
    def test_batch_file_is_local_and_never_submitted(self):
        path = self.root / 'requests.jsonl'; value = batch_file(self.output, path)
        self.assertFalse(value['submitted']); self.assertEqual(value['requests'], 1)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['params']['model'], 'claude-haiku-5-5')
        context = json.loads(json.loads(path.read_text(encoding='utf-8'))['params']['messages'][0]['content'])
        self.assertEqual(set(context['entity']), {'name', 'type', 'universe', 'aliases'})
        self.assertTrue(all('id' not in t and 'scope_id' not in t and 'record_id' not in t for t in context['texts']))
    def test_comparison_displays_persisted_input_and_unmodified_raw_response(self):
        client = Client(fenced=True); count_tokens(self.output, client); (self.output / 'WAIT_FOR_USER_GO').unlink()
        run(self.output, client)
        db = pilot_db(self.output); row = db.execute('SELECT * FROM jobs').fetchone()
        payload = json.loads(row['request_json']); payload['system'] = 'Exact stored prompt <script>never execute</script>'
        db.execute('UPDATE jobs SET request_json=? WHERE id=?', (json.dumps(payload), row['id'])); db.commit(); db.close()
        page = render_compare(self.output, row['id'])
        def pre(id):
            return html.unescape(re.search(r'<pre id="'+id+r'"[^>]*>(.*?)</pre>', page, re.S)[1])
        self.assertEqual(pre('system-text'), payload['system'])
        self.assertEqual(pre('user-text'), payload['messages'][0]['content'])
        raw = ''.join(b['text'] for b in json.loads(row['response_json'])['content'])
        self.assertEqual(pre('response-raw'), raw)
        self.assertEqual(json.loads(pre('response-readable')), json.loads(raw[8:-4]))
        self.assertNotIn('<script>never execute</script>', page)
        self.assertIn('Pilot flies a ship', page)
        self.assertEqual(client.calls, 1)
    def test_review_exposes_all_original_sources_and_confines_private_runtime(self):
        server = make_server(self.output, self.database, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def get(path, expected=200, host=None, method='GET'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request(method, path, headers={'Host': host} if host else {})
                response = connection.getresponse(); text = response.read().decode('utf-8')
                self.assertEqual(response.status, expected, text)
                return text
            finally: connection.close()
        try:
            db = pilot_db(self.output); jid = db.execute('SELECT id FROM jobs').fetchone()[0]; db.close()
            page = get('/articles/' + jid + '.html')
            self.assertIn('Ce qui a été envoyé', page); self.assertIn('Ce qui a été reçu', page)
            self.assertIn('Pilot flies a ship', page)
            get('/?article=invalid', 400)
            get('/?article=' + jid + '&article=' + jid, 400)
            get('/?article=article-' + '0'*32, 404)
            self.assertIn('toggle-raw', get('/compare.js'))
            self.assertIn('Pilot flies a ship', get('/sources/' + jid + '/' + self.rid))
            for path in ['/config.local.json', '/articles.sqlite', '/../credential', '/articles/' + jid + '.metadata.json']:
                get(path, 404)
            get('/', 403, host='foreign.test'); get('/', 405, method='POST')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
