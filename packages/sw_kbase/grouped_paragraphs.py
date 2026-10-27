"""One joint result per candidate document (or complete, bounded document lot).

The client must explicitly attest joint multi-score inference. A batch HTTP
wrapper around independent Jebadiah forwards does not meet this contract.
"""
from collections import Counter
import json
import math
import re
import time

from . import enrichment as base
from .curation_store import entity
from .disambiguation import fingerprint
from .document_sections import VERSION as UNITS_VERSION, split_sections
from .document_units import fragments
from .native_jev import NativeJevContextError
from .store import dumps

VERSION = 'sw-enrichment/3-grouped-paragraphs-no-global'


def exact_key(passage, source):
    body = ' '.join(passage['body'].casefold().split())
    path = passage.get('path', '') if passage.get('format') == 'json' else ''
    # An isolated dice value cannot identify its attribute without a JSON path.
    if not path and len(re.findall(r'[^\W_]+', body)) < 2:
        return None
    return fingerprint([body, path, source['scope'], source['section']])


def remember_exact(index, passage, source):
    key = exact_key(passage, source)
    if key:
        index.setdefault(key, {'record_id': source['id'], 'paragraph_id': passage['id'],
            'offset_start': passage['offset_start'], 'offset_end': passage['offset_end'],
            'normalized_text_sha256': fingerprint(' '.join(passage['body'].casefold().split()))})


def plan(main, directory, **kwargs):
    return base.plan(main, directory, paragraph_splitter=split_sections,
                     policy=VERSION, units_policy=UNITS_VERSION, **kwargs)


def request_for(target, source, reference, coverage, passages, *, full=True):
    return {'model': source['_model'], 'state': {
        'ENTITY': target,
        'REFERENCE': {k: reference[k] for k in ('id', 'name', 'body', 'scope', 'section')} if full else
                     {'id': reference['id'], 'name': reference['name']},
        'RETAINED_EVIDENCE': [p for p in coverage if not full or p['record_id'] != reference['id']],
        'SOURCE': {k: source[k] for k in ('id', 'version_id', 'name', 'scope', 'section')},
        'PARAGRAPHS': [{'id': p['vote_id'], 'paragraph': p['label'], 'body': p['body'],
                        'offset_start': p['offset_start'], 'offset_end': p['offset_end'],
                        'path': p.get('path', '')} for p in passages],
        'instructions': 'Score EVERY supplied paragraph for information absent from REFERENCE and RETAINED_EVIDENCE. '
            'Contradictions, new profiles and new scope-specific facts count as useful information. '
            'Candidate paragraphs are not reference evidence. Among equivalent candidate paragraphs, '
            'prefer the earliest one; later repetitions add nothing. Do not give a global document vote.'},
        'output': {'paragraphs': {p['vote_id']: {'yes': 'probability percent useful',
                    'no': 'probability percent redundant', 'unknown': 'probability percent uncertain'} for p in passages}}}


def validate_response(response, labels):
    if not isinstance(response, dict) or response.get('inference_passes') != 1:
        raise ValueError('Expected exactly one joint inference, not serial question forwards')
    rows = response.get('paragraphs')
    if not isinstance(rows, dict) or set(rows) != set(labels):
        raise ValueError('Missing or extra paragraph scores')
    for values in rows.values():
        if not isinstance(values, dict) or set(values) != {'yes', 'no', 'unknown'}:
            raise ValueError('Expected useful/redundant/uncertain paragraph probabilities')
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in values.values()):
            raise ValueError('Invalid paragraph probability')
        if abs(sum(values.values()) - 100) > .03:
            raise ValueError('Paragraph probabilities must sum to 100')
    return rows


def fits(client, request):
    try:
        client.preflight(request)
        return True
    except NativeJevContextError as error:
        if error.reason != 'context_budget':
            raise
        return False


def bounded_request(client, target, source, reference, coverage, passages, *, full_possible=True):
    if full_possible:
        request = request_for(target, source, reference, coverage, passages)
        if fits(client, request):
            return request
    # Only reference coverage is narrowed, to complete and recorded evidence
    # spans. Never truncate candidate passages or silently drop an output score.
    selected = coverage.retrieve(' '.join(p['body'] + ' ' + p.get('path', '') for p in passages), count=12)
    for count in (12, 8, 4, 2, 1):
        if not selected:
            break
        request = request_for(target, source, reference, selected[:count], passages, full=False)
        if fits(client, request):
            return request
    return None


def score(main, directory, client, *, limit=0):
    if getattr(client, 'capabilities', {}).get('joint_paragraph_scores') is not True:
        raise ValueError('A genuine joint paragraph-score backend is required; Jebadiah serial fallback is disabled')
    db = base.connect(directory); pause = main.path.parent / 'openjev.pause'; counts = Counter()
    try:
        if pause.exists():
            raise ValueError('Enrichment paused; no inference started')
        base.verify(main, db); settings = base.meta(db)
        if settings['policy'] != VERSION:
            raise ValueError('Prepare a grouped paragraph plan; old selections cannot be reused')
        if settings['units_policy'] != UNITS_VERSION:
            raise ValueError('Rechunk the old plan before scoring; old fragment decisions cannot be reused')
        if settings.get('jev_identity') is not None and settings['jev_identity'] != client.identity:
            raise ValueError('Joint backend identity changed; use a fresh plan')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('jev_identity', dumps(client.identity)))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('inference_started', 'true')); db.commit()
        for entry in db.execute("SELECT * FROM entities WHERE status!='scored' ORDER BY id LIMIT ?", (limit or -1,)).fetchall():
            base.verify(main, db, entry['id']); target = entity(main, entry['id'])
            reference = base.load_document(main, entry['reference_id'])
            exact = {}
            for paragraph in base.paragraph_rows(db, reference['id']):
                remember_exact(exact, paragraph, reference)
            coverage = base.Coverage(base.evidence_unit(p, f, reference['id'])
                for p in base.paragraph_rows(db, reference['id']) for f in fragments(p, 1600))
            for rid in json.loads(entry['order_json']):
                if rid == reference['id']:
                    continue
                source = base.load_document(main, rid); source['_model'] = client.model
                paragraphs = base.paragraph_rows(db, rid)
                queue = [{**p, 'vote_id': p['label']} for p in paragraphs]
                # Tokenize an oversized static reference once per document,
                # rather than again for every attempted paragraph lot. This
                # one-character probe is only a CPU budget check, never sent
                # to inference or stored as evidence.
                full_possible = True
                if queue:
                    probe = {**queue[0], 'body': 'x', 'offset_end': queue[0]['offset_start'] + 1}
                    full_possible = fits(client, request_for(target, source, reference, coverage, [probe]))
                results = {p['id']: [] for p in paragraphs}; added = []; request_ids = []
                while queue:
                    if pause.exists():
                        raise ValueError('Enrichment paused')
                    # Start with the whole candidate; subdivide only if its
                    # exact complete input/output schema exceeds this backend.
                    size = min(len(queue), 256); request = None
                    while size:
                        request = bounded_request(client, target, source, reference, coverage, queue[:size], full_possible=full_possible)
                        if request:
                            break
                        size //= 2
                    if not request:
                        part = queue.pop(0)
                        if len(part['body']) > 160:
                            middle = len(part['body']) // 2
                            queue[:0] = [{**part, 'body': part['body'][:middle],
                                'offset_end': part['offset_start'] + middle, 'vote_id': part['vote_id'] + 'a'},
                                {**part, 'body': part['body'][middle:], 'offset_start': part['offset_start'] + middle,
                                 'vote_id': part['vote_id'] + 'b'}]
                            continue
                        results[part['id']].append({'scores_pct': {}, 'decision': 'uncertain',
                            'reason': 'complete_context_unavailable', 'fragment': part})
                        counts['unresolved_fragments'] += 1
                        coverage.extend(base.evidence_unit(part, f, rid) for f in fragments(part, 1600))
                        continue
                    batch = queue[:size]; del queue[:size]
                    key = fingerprint({'policy': VERSION, 'identity': client.identity, 'request': request})
                    cached = db.execute('SELECT response_json FROM requests WHERE id=?', (key,)).fetchone()
                    if cached:
                        client.validate_runtime(); response = json.loads(cached[0]); counts['cache_hits'] += 1
                    else:
                        started = time.perf_counter(); response = client(request)
                        validate_response(response, [p['vote_id'] for p in batch])
                        db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', (key, dumps(request), dumps(client.identity),
                            dumps(response), 1000 * (time.perf_counter() - started))); db.commit(); counts['model_calls'] += 1
                    scores = validate_response(response, [p['vote_id'] for p in batch]); request_ids.append(key)
                    for part in batch:
                        values = scores[part['vote_id']]; decision = base.decision(values)
                        proof = exact.get(exact_key(part, source))
                        vote = {'request_id': key, 'scores_pct': values,
                            'decision': 'redundant' if proof else decision, 'fragment': part, 'joint_vote_id': part['vote_id']}
                        if proof:
                            vote['selection_proof'] = {'kind': 'exact_normalized_passage', 'covered_by': proof}
                            decision = 'redundant'; counts['exact_passages'] += 1
                        results[part['id']].append(vote)
                        if decision != 'redundant':
                            # The next batch/document sees accepted evidence.
                            coverage.extend(base.evidence_unit(part, f, rid) for f in fragments(part, 1600))
                            remember_exact(exact, part, source)
                compact = {}
                for paragraph in paragraphs:
                    votes = results[paragraph['id']]; result = base.rollup(votes)
                    if votes and all(v['decision'] == 'redundant' for v in votes):
                        result['decision'] = 'redundant'
                    if any(v.get('selection_proof') for v in votes):
                        result['selection_basis'] = 'exact_coverage_and_native_scores'
                    select = 'skip_redundant' if result['decision'] == 'redundant' else (
                        'add' if result['decision'] == 'useful' else 'add_uncertain')
                    result.update(votes=votes, considered=True, document_id=rid, paragraph=paragraph['label'])
                    db.execute('UPDATE paragraphs SET status=?,result_json=? WHERE id=?', (select, dumps(result), paragraph['id']))
                    if select != 'skip_redundant':
                        added.append(paragraph['id'])
                        remember_exact(exact, paragraph, source)
                    compact[paragraph['label']] = result['scores_pct'] if result['scores_pct'] is not None else {
                        'decision': result['decision'], 'basis': 'fragment_decisions',
                        'fragment_scores': [{'id': v.get('joint_vote_id'), 'scores_pct': v.get('scores_pct', {}),
                            'reason': v.get('reason')} for v in votes]}
                    counts[select] += 1
                result = {'basis': 'joint_paragraph_scores', 'global_vote_requested': False,
                    'paragraphs': compact, 'request_ids': request_ids, 'paragraph_ids_added_to_coverage': added}
                db.execute("UPDATE documents SET status='scored',result_json=? WHERE id=?", (dumps(result), rid)); db.commit()
                counts['documents'] += 1
            base.verify(main, db, entry['id'])
            db.execute("UPDATE entities SET status='scored' WHERE id=?", (entry['id'],)); db.commit(); counts['entities'] += 1
        remaining = db.execute("SELECT count(*) FROM entities WHERE status!='scored'").fetchone()[0]
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('state', dumps('scored' if not remaining else 'partially_scored'))); db.commit()
        result = {**counts, 'remaining_entities': remaining, 'main_database_modified': False, 'writer_started': False}
        base.atomic_json(directory / 'scoring-status.json', result)
        return result
    finally:
        db.close()
