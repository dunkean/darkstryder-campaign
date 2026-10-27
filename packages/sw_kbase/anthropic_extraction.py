"""Explicit, resumable pilot over reference + selected spans. Never loads a GPU."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from pathlib import Path
import sqlite3
import time

from .anthropic_client import AnthropicClient, PRICING, usage_cost
from .disambiguation import fingerprint
from .enrichment import atomic_json, verify, input_snapshot
from .review import _small
from .review_workspace import selection_database
from .rich_merge import evidence_for
from .store import Store, dumps
from .structured_article import VERSION, PROMPT, KINDS, SECTION_TYPES, article_schema, validate_article, article_html
from .type_mining import aggregate_table

POLICY = 'sw-anthropic-structured-pilot/2-simple-sections'
QUOTAS = {'unknown': 10, 'person': 5, 'equipment': 5, 'craft': 4, 'vehicle': 4,
          'location': 3, 'system': 3, 'stellar_route': 2, 'rule': 3, 'skill': 2,
          'force_power': 2, 'species': 2, 'organization': 2, 'scenario': 2, 'archetype': 1}
STYLE = '''body{font:16px/1.6 system-ui;background:#111b2a;color:#e7edf7;max-width:1100px;margin:2rem auto;padding:0 1rem}
a{color:#99ceff}h1,h2,h3{line-height:1.3}small{color:#a3b5cd;font-size:.72rem;overflow-wrap:anywhere}
aside{border:1px solid #7598b9;border-left:5px solid #75c59c;padding:1rem;margin:1rem 0;background:#1b2e43}
aside.rules{border-left-color:#e3b962}aside.conflict{border-left-color:#e79579}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border-bottom:1px solid #435571;padding:.45rem;text-align:left;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1b2e43;padding:1rem;max-height:75vh;overflow:auto}summary{cursor:pointer}'''


class EmptyEvidenceError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def read_config(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value['model'] != 'claude-haiku-5-5': raise ValueError('This pilot requires the explicitly selected Haiku 5.5')
    if not 1 <= value.get('sample_size', 50) <= 50: raise ValueError('Pilot limit is 50 articles')
    if not 512 <= value.get('max_output_tokens', 12000) <= 128000: raise ValueError('Invalid output budget')
    if value.get('language', 'English') != 'English': raise ValueError('This contract currently requests English output')
    if not 0 < value.get('spend_limit_usd', 1) <= 10: raise ValueError('Invalid pilot spend limit')
    return value


@contextmanager
def inputs(config):
    main = Store(config['database'], readonly=True)
    try:
        main.db.execute('BEGIN')
        with selection_database(main, config['selection_database']) as selection:
            if selection is None: raise ValueError('Compatible selection database is unavailable')
            yield main, selection
    finally:
        main.close()


def pilot_db(root):
    path = Path(root) / 'articles.sqlite'
    db = sqlite3.connect(path); db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def taxonomy_for(main):
    known = {}
    if main.db.execute("SELECT 1 FROM sqlite_master WHERE name='mined_aggregate_types'").fetchone():
        for kind, subtype in main.db.execute("SELECT DISTINCT kind,subtype FROM mined_aggregate_types WHERE subtype!='' ORDER BY kind,subtype LIMIT 200"):
            if subtype: known.setdefault(kind, []).append(subtype)
    return {'types': KINDS, 'subtypes': known, 'section_types': SECTION_TYPES}


def context_for(main, selection, entity, config, taxonomy=None):
    verify(main, selection, entity['id'])
    selected = selection.execute('SELECT status,reference_id FROM entities WHERE id=?', (entity['id'],)).fetchone()
    if not selected or selected['status'] != 'scored': raise ValueError('Entity selection is incomplete')
    bundle = evidence_for(main, selection, entity['id'], include_links=False)
    if not bundle['evidence']: raise EmptyEvidenceError('Entity has no selected evidence')
    ref = selected['reference_id']; compact = []
    sources = {s['id']: s for s in bundle['source_documents']}
    ordered = sorted(bundle['evidence'], key=lambda e: (e['record_id'] != ref, e['record_id'], e['offset_start']))
    for evidence in ordered:
        doc = sources[evidence['record_id']]
        prov = doc.get('provenance') or {}
        compact.append({'text': evidence['excerpt'],
            'role': {'reference': 'reference', 'add': 'selected addition', 'add_uncertain': 'uncertain addition'}[evidence['selection']],
            'context': {'section': evidence['section'], 'scope': _small(evidence['scope'], 2000)}, 'origin': evidence['origin'],
            'source': {'acquisition': (prov.get('acquisition') or {}).get('label', doc.get('source_title', '')),
                       'works': [{k: w.get(k) for k in ('title', 'code', 'kind', 'pages', 'page_basis')}
                                 for w in prov.get('works', [])]}})
    aliases = [row[0] for row in main.db.execute('SELECT label FROM aggregate_aliases WHERE aggregate_id=? ORDER BY label LIMIT 100', (entity['id'],))]
    retained_records = {e['record_id'] for e in bundle['evidence']}
    context = {'entity': {'name': entity['name'], 'type': entity['kind'], 'universe': entity['universe'], 'aliases': aliases},
        'known_names': taxonomy or taxonomy_for(main), 'texts': compact,
        'existing_profiles': [{'data': p['data'], 'context': _small(p['scope'], 2000)}
                              for p in bundle['profiles'] if retained_records.intersection(p['source_documents'])],
        'output_example': {'type': '', 'subtype': '', 'sections': [
            {'title': '', 'subtitle': '', 'content': '', 'type': 'description', 'stats': {}}]},
        'language': config.get('language', 'English'), 'live_campaign_state_inferred': False}
    # Original profiles remain available to deterministic follow-up checks and export.
    return context, bundle, fingerprint(input_snapshot(main, entity['id']))


def payload_for(context, config):
    return {'model': config['model'], 'max_tokens': config.get('max_output_tokens', 12000),
        'thinking': {'type': 'disabled'},
        'system': PROMPT, 'messages': [{'role': 'user', 'content': dumps(context)}],
        # A free stats dictionary is intentional. Validate JSON locally rather than
        # forcing Anthropic's closed-object grammar onto arbitrary game stat names.
        'output_config': {'effort': 'low'}}


def prepare(config, root):
    root = Path(root); root.mkdir(parents=True, exist_ok=False)
    (root / 'WAIT_FOR_USER_GO').write_text('Prepared only. Wait for the user before inference or batch submission.\n', encoding='utf-8')
    db = pilot_db(root)
    try:
        db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        CREATE TABLE jobs(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,initial_kind TEXT NOT NULL,
          input_hash TEXT NOT NULL,snapshot_hash TEXT NOT NULL,request_json TEXT NOT NULL,bundle_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'prepared',input_tokens INTEGER,response_json TEXT,article_json TEXT,
          usage_json TEXT,error_json TEXT,latency_seconds REAL,cost_usd REAL NOT NULL DEFAULT 0);''')
        count = config.get('sample_size', 50); quotas = config.get('quotas', QUOTAS)
        summary = {'policy': POLICY, 'schema_version': VERSION, 'prepared_at': now(), 'state': 'preparing',
            'requested': count, 'unit': 'consolidated_entity_article', 'model': config['model'],
            'generation_calls': 0, 'batch_submissions': 0, 'source_databases_modified': False,
            'sample_policy': 'deterministic type quotas; bounded selected evidence; includes unknowns',
            'pricing': PRICING, 'by_kind': {}, 'skipped': {}, 'articles': []}
        skipped = Counter(); by_kind = Counter(); chosen = set()
        seed = config.get('sample_seed', '20261027-haiku-pilot')
        with inputs(config) as (main, selection):
            taxonomy = taxonomy_for(main)
            rows = [dict(r) for r in main.db.execute('SELECT id,name,kind,universe FROM ' + aggregate_table(main))]
            rows.sort(key=lambda r: hashlib.sha256((seed + ':' + r['id']).encode()).digest())
            def add(entity):
                if entity['id'] in chosen: return False
                state = selection.execute('SELECT status FROM entities WHERE id=?', (entity['id'],)).fetchone()
                if not state or state[0] != 'scored': skipped['not_scored'] += 1; return False
                try:
                    context, bundle, snapshot = context_for(main, selection, entity, config, taxonomy)
                except EmptyEvidenceError:
                    skipped['empty_evidence'] += 1; return False
                size = sum(len(p['text']) for p in context['texts'])
                if size < config.get('min_evidence_chars', 500): skipped['too_short'] += 1; return False
                if size > config.get('max_evidence_chars', 45000): skipped['too_long_for_pilot'] += 1; return False
                payload = payload_for(context, config)
                if len(dumps(payload)) > config.get('max_request_chars', 150000): skipped['request_budget'] += 1; return False
                signature = fingerprint(payload); jid = 'article-' + signature[:32]
                db.execute('INSERT INTO jobs(id,entity_id,name,initial_kind,input_hash,snapshot_hash,request_json,bundle_json) VALUES(?,?,?,?,?,?,?,?)',
                    (jid, entity['id'], entity['name'], entity['kind'], signature, snapshot, dumps(payload), dumps(bundle)))
                chosen.add(entity['id']); by_kind[entity['kind']] += 1
                reference_id = next(s['id'] for s in bundle['source_documents'] if s['role'] == 'reference')
                summary['articles'].append({'id': jid, **entity, 'reference_id': reference_id,
                    'evidence_chars': size, 'selected_spans': len(context['texts']),
                    'source_documents': len(bundle['source_documents']), 'request_chars': len(dumps(payload))})
                return True
            for kind, quota in quotas.items():
                for entity in rows:
                    if len(chosen) >= count or by_kind[kind] >= quota: break
                    if entity['kind'] == kind: add(entity)
            for entity in rows:
                if len(chosen) >= count: break
                if entity['id'] not in chosen: add(entity)
        if len(chosen) != count: raise ValueError('Not enough compatible articles for the requested sample')
        summary.update(state='prepared_waiting_for_go', by_kind=dict(by_kind), skipped=dict(skipped))
        for key, value in [('preparation', summary), ('config', config), ('pricing', PRICING)]:
            db.execute('INSERT INTO meta VALUES(?,?)', (key, dumps(value)))
        db.commit(); atomic_json(root / 'preparation.json', summary)
        atomic_json(root / 'config.local.json', config)
        atomic_json(root / 'response.schema.json', article_schema())
    finally:
        db.close()
    report(root)
    return summary


def config_from(db):
    return json.loads(db.execute("SELECT value_json FROM meta WHERE key='config'").fetchone()[0])


def check_contract(db):
    for row in db.execute('SELECT request_json FROM jobs'):
        payload = json.loads(row[0])
        context = json.loads(payload['messages'][0]['content'])
        if (payload.get('system') != PROMPT or 'format' in payload.get('output_config', {})
                or set(context.get('output_example', {})) != {'type', 'subtype', 'sections'}):
            raise ValueError('Obsolete pilot contract; prepare the simplified schema again')


def count_tokens(root, client=None):
    db = pilot_db(root)
    try:
        check_contract(db)
        config = config_from(db); client = client or AnthropicClient(config['api_key_file'])
        model = client.model(config['model'])
        if model['id'] != config['model']:
            raise ValueError('Requested model is not available')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('verified_model', dumps(model))); db.commit()
        for row in db.execute('SELECT id,request_json FROM jobs WHERE input_tokens IS NULL ORDER BY id').fetchall():
            tokens = client.count(json.loads(row['request_json']))['input_tokens']
            if type(tokens) is not int or not 0 < tokens <= config.get('max_input_tokens', 32000):
                raise ValueError('Exact token count exceeds pilot budget; reprepare without truncation')
            db.execute('UPDATE jobs SET input_tokens=? WHERE id=?', (tokens, row['id'])); db.commit()
    finally:
        db.close()
    return report(root)


def finish_received(db, row, config):
    """Validate already saved responses; a crash here never causes another API call."""
    response = json.loads(row['response_json']); payload = json.loads(row['request_json'])
    try:
        if response.get('model') != config['model']: raise ValueError('Unexpected response model')
        if response.get('stop_reason') != 'end_turn': raise ValueError('Refused/truncated response')
        text = ''.join(b['text'] for b in response.get('content', []) if b.get('type') == 'text')
        # A Markdown fence is transport formatting, not a reason to repay the model.
        fenced = re.fullmatch(r'\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*', text, flags=re.IGNORECASE)
        article = json.loads(fenced[1] if fenced else text); context = json.loads(payload['messages'][0]['content'])
        validate_article(article, context)
        db.execute("UPDATE jobs SET article_json=?,status='complete',error_json=NULL WHERE id=?", (dumps(article), row['id']))
    except (ValueError, KeyError, TypeError) as error:
        db.execute("UPDATE jobs SET status='invalid',error_json=? WHERE id=?",
                   (dumps({'type': type(error).__name__, 'reason': str(error)[:240]}), row['id']))
    db.commit()


def run(root, client=None):
    root = Path(root)
    if (root / 'WAIT_FOR_USER_GO').exists(): raise ValueError('Waiting for user GO; no inference started')
    db = pilot_db(root)
    try:
        check_contract(db)
        config = config_from(db); client = client or AnthropicClient(config['api_key_file'])
        pricing = json.loads(db.execute("SELECT value_json FROM meta WHERE key='pricing'").fetchone()[0])
        if db.execute('SELECT count(*) FROM jobs WHERE input_tokens IS NULL').fetchone()[0]:
            raise ValueError('Count tokens before inference')
        if db.execute("SELECT count(*) FROM jobs WHERE status='in_flight'").fetchone()[0]:
            raise ValueError('An earlier request has an unknown outcome; reconcile it before retrying')
        for received in db.execute("SELECT * FROM jobs WHERE status='received' ORDER BY id").fetchall():
            finish_received(db, received, config)
        with inputs(config) as (main, selection):
            for row in db.execute("SELECT * FROM jobs WHERE status='prepared' ORDER BY id").fetchall():
                if (root / 'WAIT_FOR_USER_GO').exists() or (root / 'PAUSE').exists(): break
                verify(main, selection, row['entity_id'])
                if fingerprint(input_snapshot(main, row['entity_id'])) != row['snapshot_hash']:
                    raise ValueError('Source snapshot changed; regenerate this pilot')
                payload = json.loads(row['request_json'])
                if fingerprint(payload) != row['input_hash']: raise ValueError('Prepared request was edited')
                spent = db.execute('SELECT coalesce(sum(cost_usd),0) FROM jobs').fetchone()[0]
                # Leave room for schema overhead above the count endpoint's estimate.
                ceiling = usage_cost({'input_tokens': row['input_tokens'] + 4096,
                                      'output_tokens': payload['max_tokens']}, pricing=pricing)['estimated_usd']
                if spent + ceiling > config.get('spend_limit_usd', 1):
                    raise ValueError('Pilot spend limit reached before the next request')
                db.execute("UPDATE jobs SET status='in_flight' WHERE id=?", (row['id'],)); db.commit()
                start = time.perf_counter()
                try:
                    response = client.message(payload)
                except Exception as error:
                    # No automatic retries: a connection loss can occur after a billable generation.
                    db.execute('UPDATE jobs SET error_json=? WHERE id=?',
                        (dumps({'type': type(error).__name__, 'outcome': 'unknown_no_automatic_retry'}), row['id']))
                    db.commit(); raise
                latency = time.perf_counter() - start
                usage = response.get('usage', {}); cost = usage_cost(usage, pricing=pricing)['estimated_usd']
                db.execute('UPDATE jobs SET response_json=?,usage_json=?,cost_usd=?,latency_seconds=?,status=? WHERE id=?',
                    (dumps(response), dumps(usage), cost, latency, 'received', row['id'])); db.commit()
                finish_received(db, db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone(), config)
                report(root, db=db)
    finally:
        db.close()
    return report(root)


def batch_file(root, destination):
    """Prepare only. The caller must separately authorize any submission."""
    db = pilot_db(root)
    try:
        check_contract(db)
        rows = db.execute("SELECT id,request_json FROM jobs WHERE status='prepared' ORDER BY id").fetchall()
        destination = Path(destination)
        with destination.open('x', encoding='utf-8') as stream:
            for row in rows: stream.write(dumps({'custom_id': row['id'], 'params': json.loads(row['request_json'])}) + '\n')
        return {'requests': len(rows), 'submitted': False}
    finally:
        db.close()


def _html_page(title, body):
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(title) + '</title><style>' + STYLE + '</style><body>' + body + '</body></html>'


def sources_html(jid, bundle):
    esc = lambda value: html.escape(str(value), quote=True)
    evidence = {}
    for span in bundle['evidence']: evidence.setdefault(span['record_id'], []).append(span)
    docs = sorted(bundle['source_documents'], key=lambda d: (d['role'] != 'reference', d['source_title'], d['id']))
    out = ['<section id="sources"><h2>Toutes les sources · ' + str(len(docs)) + ' documents</h2>',
           '<p>La référence et les extraits retenus ont été transmis à Haiku. Les autres sources restent consultables ci-dessous.</p>']
    for doc in docs:
        spans = evidence.get(doc['id'], [])
        provenance = doc.get('provenance') or {}; acquisition = provenance.get('acquisition') or {}
        role = 'Référence choisie' if doc['role'] == 'reference' else 'Complément utilisé' if spans else 'Source écartée du merge'
        out.append('<details><summary>' + esc(role + ' · ' + doc.get('source_title', '') + ' · ' + doc.get('name', '')) + '</summary>')
        out.append('<p><strong>Origine :</strong> ' + esc(acquisition.get('label') or doc.get('dataset', '')) + '</p>')
        works = provenance.get('works') or []
        if works:
            out.append('<p><strong>Ouvrage / contenu source :</strong></p><ul>')
            for work in works:
                out.append('<li>' + esc(' · '.join(str(work[k]) for k in ('title', 'code', 'pages', 'page_basis') if work.get(k))) + '</li>')
            out.append('</ul>')
        else: out.append('<p>Ouvrage / contenu source : non indiqué.</p>')
        counts = Counter(v['selection'] for v in doc.get('paragraph_votes', []))
        out.append('<p>' + esc(' · '.join(f'{k}: {v}' for k, v in counts.items())) + '</p>')
        out.append('<p><a href="/sources/' + esc(jid) + '/' + esc(doc['id']) + '">Lire le document original intégral</a></p>')
        if not spans: out.append('<p>Source conservée en base, sans extrait transmis à cette reconstruction.</p>')
        else:
            out.append('<details><summary>Texte exact transmis · ' + str(len(spans)) + ' extraits</summary>')
            for span in spans:
                out.append('<p>' + esc(span['selection'] + ' · ' + span.get('paragraph', '')) + '</p><pre>' + esc(span['excerpt']) + '</pre>')
            out.append('</details>')
        out.append('</details>')
    return ''.join(out) + '</section>'


def report(root, db=None):
    root = Path(root); own = db is None; db = db or pilot_db(root)
    try:
        config = config_from(db); rows = db.execute('SELECT * FROM jobs ORDER BY initial_kind,name').fetchall()
        statuses = Counter(r['status'] for r in rows); prompt = sum(r['input_tokens'] or 0 for r in rows)
        maximum_output = sum(json.loads(r['request_json'])['max_tokens'] for r in rows)
        measured = sum(r['cost_usd'] for r in rows)
        pricing = json.loads(db.execute("SELECT value_json FROM meta WHERE key='pricing'").fetchone()[0])
        result = {'policy': POLICY, 'model': config['model'], 'articles': len(rows), 'by_status': dict(statuses),
            'input_tokens_counted': prompt, 'counted_articles': sum(r['input_tokens'] is not None for r in rows),
            'generation_responses': sum(r['response_json'] is not None for r in rows),
            'observed_output_tokens': sum(json.loads(r['usage_json'] or '{}').get('output_tokens', 0) for r in rows),
            'estimated_actual_cost_usd': measured, 'billing_status': 'usage_estimate_not_invoice',
            'estimated_batch_equivalent_usd': measured * pricing['batch_multiplier'],
            'configured_output_ceiling_tokens': maximum_output,
            'estimated_pilot_ceiling_usd': sum(usage_cost({'input_tokens': (r['input_tokens'] or config.get('max_input_tokens', 32000)) + 4096,
                'output_tokens': json.loads(r['request_json'])['max_tokens']}, pricing=pricing)['estimated_usd'] for r in rows),
            'waiting_for_go': (root / 'WAIT_FOR_USER_GO').exists(), 'spend_limit_usd': config.get('spend_limit_usd', 1),
            'source_databases_modified': False, 'pricing': pricing}
        (root / 'articles').mkdir(exist_ok=True)
        cards = []; featured = None
        for row in rows:
            payload = json.loads(row['request_json']); context = json.loads(payload['messages'][0]['content'])
            bundle = json.loads(row['bundle_json'])
            navigation = '<p><a href="../index.html">← Les 50 fiches</a> · <a href="#sources">Toutes les sources (' + str(len(bundle['source_documents'])) + ')</a></p>'
            if row['article_json']:
                article = json.loads(row['article_json'])
                body = navigation + article_html(article, row['name'])
                atomic_json(root / 'articles' / (row['id'] + '.json'), article)
                atomic_json(root / 'articles' / (row['id'] + '.metadata.json'), {'id': row['entity_id'],
                    'schema_version': VERSION, 'section_source_mapping': 'not_yet_resolved',
                    'evidence': bundle['evidence'], 'source_documents': bundle['source_documents'],
                    'source_profiles': bundle['profiles'], 'editorial_overlays': bundle['editorial_overlays'],
                    'generation': {'model': config['model'], 'usage': json.loads(row['usage_json']),
                                   'request_hash': row['input_hash'], 'review_status': 'unreviewed'}})
                classified = article['type'] + ' / ' + article['subtype']
                if featured is None or len(bundle['source_documents']) > featured['sources']:
                    featured = {'id': row['id'], 'name': row['name'], 'sources': len(bundle['source_documents'])}
            else:
                body = navigation + '<h1>' + html.escape(row['name']) + '</h1><p>' + html.escape(row['status']) + '</p>'
                for evidence in context['texts']:
                    body += '<h3>' + html.escape(evidence['role']) + '</h3><pre>' + html.escape(evidence['text']) + '</pre>'
                classified = '—'
                if row['response_json']:
                    response = json.loads(row['response_json'])
                    out = ''.join(b.get('text', '') for b in response.get('content', []) if b.get('type') == 'text')
                    body += '<details open><summary>Réponse brute · validation à examiner</summary><pre>' + html.escape(out) + '</pre></details>'
            body += '<p><a href="#sources">Voir toutes les sources</a> · <a href="http://127.0.0.1:8765/review#aggregate=' + row['entity_id'] + '">Ouvrir dans la base</a></p>'
            body += '<p>Coût estimé : $' + format(row['cost_usd'], '.6f') + ' · Temps : ' + format(row['latency_seconds'] or 0, '.2f') + ' s</p>'
            body += sources_html(row['id'], bundle)
            (root / 'articles' / (row['id'] + '.html')).write_text(_html_page(row['name'], body), encoding='utf-8')
            cards.append('<tr><td><a href="articles/' + row['id'] + '.html">' + html.escape(row['name']) + '</a></td><td>' +
                html.escape(row['initial_kind'] + ' → ' + classified) + '</td><td>' + html.escape(row['status']) +
                '</td><td>' + str(len(bundle['source_documents'])) + '</td><td>$' + format(row['cost_usd'], '.6f') + '</td></tr>')
        body = '<h1>Haiku 5.5 · fiches reconstruites</h1><p>' + str(len(rows)) + ' fiches · ' + str(statuses.get('complete', 0)) + ' reconstruites · ' + str(statuses.get('invalid', 0)) + ' à examiner</p>'
        body += '<p>Coût total estimé : <strong>$' + format(measured, '.6f') + '</strong> · ' + str(prompt) + ' tokens d’entrée · ' + str(result['observed_output_tokens']) + ' tokens de sortie.</p>'
        if featured:
            body += '<p><a href="articles/' + featured['id'] + '.html">Ouvrir ' + html.escape(featured['name']) + ' et ses ' + str(featured['sources']) + ' sources</a></p>'
        body += '<p>Chaque fiche montre la reconstruction puis toutes les sources, y compris celles écartées du merge. Les originaux restent intacts.</p>'
        body += '<table><tr><th>Article</th><th>Type initial → extrait</th><th>Statut</th><th>Sources</th><th>Coût estimé</th></tr>' + ''.join(cards) + '</table>'
        body += '<details><summary>Détails du coût et du traitement</summary><pre>' + html.escape(json.dumps(result, indent=2)) + '</pre></details>'
        (root / 'index.html').write_text(_html_page('SW D6 · Haiku pilot', body), encoding='utf-8')
        atomic_json(root / 'report.json', result)
        return result
    finally:
        if own: db.close()
