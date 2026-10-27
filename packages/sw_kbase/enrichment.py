"""All-source reference selection -> global and paragraph Jev votes -> merge input.

Nothing starts a server. Main knowledge is read-only; checkpoints live in a
separate runtime SQLite. The original document worker and its cache are untouched.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from .curation_store import document, entity, snapshot, active_run
from .disambiguation import CRITERIA, PROMPTS, fingerprint, probabilities
from .document_units import VERSION as UNITS_VERSION, choose_reference, split_document, fragments
from .native_jev import NativeJevContextError
from .store import dumps

VERSION = 'sw-enrichment/2-global-and-progressive-paragraph-votes'
DDL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE entities(id TEXT PRIMARY KEY,reference_id TEXT NOT NULL,order_json TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL,status TEXT NOT NULL,ranking_json TEXT NOT NULL);
CREATE TABLE documents(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL REFERENCES entities(id),
 version_id TEXT NOT NULL,metadata_json TEXT NOT NULL,role TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT);
CREATE TABLE paragraphs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),
 number INTEGER NOT NULL,label TEXT NOT NULL,offset_start INTEGER NOT NULL,offset_end INTEGER NOT NULL,
 body TEXT NOT NULL,unit_json TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,UNIQUE(document_id,number));
CREATE TABLE requests(id TEXT PRIMARY KEY,request_json TEXT NOT NULL,identity_json TEXT NOT NULL,
 response_json TEXT NOT NULL,latency_ms REAL NOT NULL);
CREATE TABLE merge_requests(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,identity_json TEXT NOT NULL,
 result_json TEXT NOT NULL);
CREATE TABLE rich_entities(id TEXT PRIMARY KEY REFERENCES entities(id),signature TEXT NOT NULL,
 result_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE VIEW selected_paragraphs AS
 SELECT e.id entity_id,e.reference_id,e.status entity_status,d.id document_id,d.version_id,d.role,d.status document_status,
        p.id paragraph_id,p.label,p.offset_start,p.offset_end,p.body,p.unit_json,p.status,p.result_json,
        json_extract(p.result_json,'$.scores_pct.yes') novelty_pct,
        json_extract(p.result_json,'$.scores_pct.no') redundancy_pct,
        d.metadata_json
 FROM paragraphs p JOIN documents d ON d.id=p.document_id JOIN entities e ON e.id=d.entity_id
 WHERE p.status IN ('reference','add','add_uncertain');
'''


def connect(directory):
    path = Path(directory) / 'enrichment.sqlite'
    if not path.exists():
        raise ValueError('Prepare an enrichment plan first')
    db = sqlite3.connect(path); db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def meta(db):
    return {r['key']: json.loads(r['value_json']) for r in db.execute('SELECT * FROM meta')}


def atomic_json(path, value):
    path = Path(path); temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_document(main, rid):
    value = document(main, rid)
    row = main.db.execute('SELECT r.source_id,s.dataset,s.title,s.metadata_json,v.sha256,v.locator_json,'
        'v.aliases_json,v.universe,p.stats_json,e.revision,e.content_json,t.section FROM records r '
        'JOIN sources s ON s.id=r.source_id JOIN record_versions v ON v.id=r.current_version '
        'LEFT JOIN profiles p ON p.version_id=v.id LEFT JOIN editorial e ON e.entity_id=r.entity_id '
        'LEFT JOIN record_sections t ON t.record_id=r.id WHERE r.id=?', (rid,)).fetchone()
    value.update(source_id=row['source_id'], dataset=row['dataset'], source_title=row['title'],
        source_metadata=json.loads(row['metadata_json']), version_sha256=row['sha256'],
        locator=json.loads(row['locator_json']), aliases=json.loads(row['aliases_json']), universe=row['universe'],
        raw_profile=json.loads(row['stats_json']) if row['stats_json'] else {},
        editorial_revision=row['revision'], editorial=json.loads(row['content_json']) if row['content_json'] else None,
        section=row['section'] or 'unclassified', body_sha256=hashlib.sha256(value['body'].encode()).hexdigest())
    return value


def input_snapshot(main, aid):
    value = snapshot(main, 'aggregate', aid)
    value['entity'] = dict(main.db.execute('SELECT id,name,kind,universe,warnings_json FROM aggregates WHERE id=?', (aid,)).fetchone())
    value['aliases'] = [tuple(r) for r in main.db.execute('SELECT form,label FROM aggregate_aliases WHERE aggregate_id=? ORDER BY form,label', (aid,))]
    value['sources'] = [dict(r) for r in main.db.execute('SELECT DISTINCT s.id,s.metadata_json,s.origin,s.dataset,s.title '
        'FROM aggregate_members m JOIN records r ON r.id=m.record_id JOIN sources s ON s.id=r.source_id '
        'WHERE m.aggregate_id=? ORDER BY s.id', (aid,))]
    value['sections'] = [tuple(r) for r in main.db.execute('SELECT m.record_id,t.section FROM aggregate_members m '
        'LEFT JOIN record_sections t ON t.record_id=m.record_id WHERE m.aggregate_id=? ORDER BY m.record_id', (aid,))]
    return value


def fresh_memberships(main):
    if main.db.execute('SELECT 1 FROM records r LEFT JOIN aggregate_members m ON m.record_id=r.id '
            'WHERE r.active=1 AND (m.record_id IS NULL OR m.version_id!=r.current_version) LIMIT 1').fetchone():
        raise ValueError('Refresh algorithmic aggregation before planning/scoring all entries')


def plan(main, directory, *, config=None, aggregate_ids=None, paragraph_splitter=split_document,
         policy=VERSION, units_policy=UNITS_VERSION):
    """All current entries, including singletons and earlier semantic exclusions."""
    config = config or {}; directory = Path(directory)
    if aggregate_ids is not None:
        aggregate_ids = sorted(set(aggregate_ids))
    cleaning = active_run(main)
    fresh_memberships(main)
    directory.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(directory / 'enrichment.sqlite'); db.row_factory = sqlite3.Row
    counts = Counter(); started = time.monotonic()
    try:
        db.executescript(DDL)
        for key, value in {'policy': policy, 'units_policy': units_policy, 'config': config,
                           'cleaning_run': cleaning, 'main_database': str(main.path.resolve()),
                           'state': 'planning', 'complete_scope': aggregate_ids is None, 'inference_started': False}.items():
            db.execute('INSERT INTO meta VALUES(?,?)', (key, dumps(value)))
        db.commit()
        ids = aggregate_ids if aggregate_ids is not None else [r[0] for r in main.db.execute('SELECT id FROM aggregates ORDER BY id')]
        for aid in ids:
            baseline = fingerprint(input_snapshot(main, aid))
            documents = [load_document(main, r[0]) for r in main.db.execute(
                'SELECT record_id FROM aggregate_members WHERE aggregate_id=? ORDER BY record_id', (aid,))]
            reference, ranking = choose_reference(documents, config)
            order = sorted((d['id'] for d in documents), key=lambda rid: (-ranking[rid]['score'], rid))
            db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?)',
                (aid, reference, dumps(order), baseline, 'planned', dumps(ranking)))
            for source in documents:
                role = 'reference' if source['id'] == reference else 'candidate'
                metadata = {k: v for k, v in source.items() if k != 'body'}
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',
                    (source['id'], aid, source['version_id'], dumps(metadata), role, 'reference' if role == 'reference' else 'pending', None))
                for paragraph in paragraph_splitter(source['body']):
                    pid = 'para-' + fingerprint([source['id'], source['version_id'], paragraph['offset_start'], paragraph['offset_end'], units_policy])
                    db.execute('INSERT INTO paragraphs VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (pid, source['id'], paragraph['number'], paragraph['label'], paragraph['offset_start'], paragraph['offset_end'],
                         paragraph['body'], dumps({k: v for k, v in paragraph.items() if k != 'body'}),
                         'reference' if role == 'reference' else 'pending', None))
                    counts['paragraphs'] += 1
                    counts[role + '_paragraphs'] += 1
                counts['documents'] += 1
                counts[role + '_documents'] += 1
            if fingerprint(input_snapshot(main, aid)) != baseline:
                raise ValueError('Knowledge input changed during planning; replan')
            counts['entities'] += 1
            if counts['entities'] % 1000 == 0:
                atomic_json(directory / 'planning-progress.json', {'state': 'planning', **counts,
                    'elapsed_seconds': time.monotonic() - started})
        if active_run(main) != cleaning:
            raise ValueError('Knowledge input changed during planning; replan')
        fresh_memberships(main)
        db.execute('INSERT INTO meta VALUES(?,?)', ('counts', dumps(dict(counts))))
        db.execute("UPDATE meta SET value_json=? WHERE key='state'", (dumps('planned'),)); db.commit()
        summary = {'policy': policy, 'state': 'planned', **counts, 'model_calls': 0,
                   'all_sources_included': aggregate_ids is None, 'prior_semantic_selections_reused': False}
        atomic_json(directory / 'plan.json', summary)
        atomic_json(directory / 'planning-progress.json', {**summary, 'elapsed_seconds': time.monotonic() - started})
        return summary
    finally:
        db.close()


def verify(main, db, aid=None):
    settings = meta(db)
    supported = {VERSION: {UNITS_VERSION},
                 'sw-enrichment/3-grouped-paragraphs-no-global': {'document-sections/1', 'document-sections/2-coherent-blocks', 'document-sections/2.1-coherent-blocks', 'document-sections/2.2-coherent-blocks'}}
    if settings['units_policy'] not in supported.get(settings['policy'], set()):
        raise ValueError('Enrichment policy changed; replan')
    if settings['state'] == 'planning':
        raise ValueError('Incomplete enrichment plan; replan')
    if settings['cleaning_run'] != active_run(main) or settings['main_database'] != str(main.path.resolve()):
        raise ValueError('Knowledge input changed; replan')
    if aid is None:
        fresh_memberships(main)
        if settings['complete_scope'] and main.db.execute('SELECT count(*) FROM aggregates').fetchone()[0] != db.execute('SELECT count(*) FROM entities').fetchone()[0]:
            raise ValueError('Aggregate inventory changed; replan')
    for row in db.execute('SELECT id,snapshot_hash FROM entities' + (' WHERE id=?' if aid else ''), (aid,) if aid else ()):
        if fingerprint(input_snapshot(main, row['id'])) != row['snapshot_hash']:
            raise ValueError('Source, membership or editorial revision changed; replan')


def paragraph_rows(db, rid):
    return [{**dict(r), **json.loads(r['unit_json'])} for r in db.execute(
        'SELECT * FROM paragraphs WHERE document_id=? ORDER BY number', (rid,))]


def decision(scores):
    if scores.get('no', 0) > 80:
        return 'redundant'
    if scores.get('yes', 0) > 80:
        return 'useful'
    return 'uncertain'


def rollup(votes):
    """Explicit conservative rule, never presented as a calibrated whole-doc probability."""
    scores = [v.get('scores_pct', {}) for v in votes]
    state = 'redundant' if scores and all(decision(s) == 'redundant' for s in scores) else (
        'useful' if any(decision(s) == 'useful' for s in scores) else 'uncertain')
    return {'decision': state, 'scores_pct': scores[0] if len(scores) == 1 else None,
            'basis': 'direct_fragment' if len(scores) == 1 else 'all_paragraph_fragments',
            'max_usefulness_pct': max((s.get('yes', 0) for s in scores), default=0),
            'min_redundancy_pct': min((s.get('no', 0) for s in scores), default=0)}


def terms(text):
    return set(re.findall(r'[^\W_]+', text.casefold())) - {'the', 'a', 'an', 'of', 'and', 'le', 'la', 'de', 'et'}


def evidence_unit(paragraph, fragment, rid):
    return {**fragment, 'id': paragraph['id'], 'record_id': rid, 'path': paragraph.get('path', '')}


class Coverage(list):
    """Incremental lexical index; avoid rescanning every retained passage."""
    def __init__(self, values=()):
        super().__init__(); self.index = defaultdict(set); self.extend(values)

    def extend(self, values):
        for value in values:
            position = len(self)
            for term in terms(value['body'] + ' ' + value.get('path', '')):
                self.index[term].add(position)
            super().append(value)

    def retrieve(self, text, count=3):
        scores = Counter()
        for term in sorted(terms(text)):
            positions = self.index.get(term, ())
            weight = math.log(2 + len(self) / (1 + len(positions)))
            for position in positions:
                scores[position] += weight
        indices = sorted(scores, key=lambda i: (-scores[i], self[i]['record_id'], self[i]['offset_start']))[:count]
        return [self[i] for i in indices] if indices else list(self[:count])


class Votes:
    def __init__(self, db, client, pause):
        self.db, self.client, self.pause = db, client, pause
        self.calls = self.hits = 0

    def fits(self, request):
        preflight = getattr(self.client, 'preflight', None)
        if not preflight:
            return True
        try:
            preflight(request)
            return True
        except NativeJevContextError as error:
            if error.reason != 'context_budget':
                raise
            return False

    def ask(self, request):
        if self.pause.exists():
            raise ValueError('Enrichment paused')
        key = fingerprint({'policy': VERSION, 'identity': self.client.identity, 'request': request})
        row = self.db.execute('SELECT response_json FROM requests WHERE id=?', (key,)).fetchone()
        if row:
            self.client.validate_runtime(); response = json.loads(row[0]); self.hits += 1
        else:
            started = time.perf_counter(); response = self.client(request)
            if not response.get('deferred_reason'):
                if set(response.get('answers', {})) != {'source_contribution'}:
                    raise ValueError('Missing or extra Jev paragraph response')
                probabilities(response['answers']['source_contribution']); self.calls += 1
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', (key, dumps(request), dumps(self.client.identity),
                dumps(response), 1000 * (time.perf_counter() - started))); self.db.commit()
        if response.get('deferred_reason'):
            return {'request_id': key, 'scores_pct': {}, 'decision': 'uncertain', 'reason': response['deferred_reason']}
        scores = probabilities(response['answers']['source_contribution'])
        return {'request_id': key, 'scores_pct': scores, 'decision': decision(scores)}


def request_for(target, source, reference, coverage, stage, *, focus=None, full=True):
    metadata = {k: source[k] for k in ('id', 'version_id', 'name', 'scope', 'section')}
    if full:
        ref = {k: reference[k] for k in ('id', 'name', 'body', 'scope', 'section')}
        extra = [u for u in coverage if u['record_id'] != reference['id']]
    else:
        ref = {'id': reference['id'], 'name': reference['name'], 'role': 'identity anchor; exact reference evidence is below'}
        extra = coverage
    state = {'ENTITY': target, 'REFERENCE': ref, 'RETAINED_SOURCES': extra,
             'SOURCE': {**metadata, 'body': focus['body'] if focus else source['body']},
             'evaluation': {'stage': stage, 'evidence_mode': 'full_documents' if full else 'selected_complete_spans',
                'rule': 'Only REFERENCE and RETAINED_SOURCES establish coverage. Candidate document context is not retained evidence. '
                        'Contradictions and new scope/profile facts are additions. Assess the entire SOURCE supplied here.'}}
    if focus:
        state['SOURCE'].update(paragraph=focus.get('paragraph'), path=focus.get('path', ''),
            offset_start=focus['offset_start'], offset_end=focus['offset_end'])
        if full:
            state['CANDIDATE_DOCUMENT_CONTEXT'] = {'id': source['id'], 'body': source['body']}
    return {'model': source.get('_model'), 'state': state, 'questions': {'source_contribution': {
        'type': 'choice', 'instructions': PROMPTS['source_contribution'], 'criteria': CRITERIA.copy()}}}


def score_fragment(votes, target, source, reference, coverage, fragment, stage='paragraph', full_context=True):
    if full_context:
        request = request_for(target, source, reference, coverage, stage, focus=fragment)
        if votes.fits(request):
            return [{**votes.ask(request), 'evidence_mode': 'full_documents', 'fragment': fragment}]
    # Keep every source character. Only retrieval context is narrowed.
    ranked = coverage.retrieve(fragment['body'] + ' ' + fragment.get('path', ''))
    for number in (3, 2, 1, 0):
        selected = ranked[:number]
        request = request_for(target, source, reference, selected, stage, focus=fragment, full=False)
        if votes.fits(request):
            answer = votes.ask(request)
            if not selected:
                answer.update(raw_scores_pct=answer.get('scores_pct', {}), scores_pct={},
                              decision='uncertain', reason='reference_evidence_unavailable')
            return [{**answer, 'evidence_mode': 'selected_complete_spans', 'covering_evidence': selected, 'fragment': fragment}]
    if len(fragment['body']) > 160:
        middle = len(fragment['body']) // 2
        left = {**fragment, 'body': fragment['body'][:middle], 'offset_end': fragment['offset_start'] + middle}
        right = {**fragment, 'body': fragment['body'][middle:], 'offset_start': fragment['offset_start'] + middle}
        return score_fragment(votes, target, source, reference, coverage, left, stage, False) + score_fragment(votes, target, source, reference, coverage, right, stage, False)
    return [{'scores_pct': {}, 'decision': 'uncertain', 'reason': 'context_budget_unresolved', 'fragment': fragment}]


def score(main, directory, client, *, limit=0):
    db = connect(directory); counts = Counter(); pause = main.path.parent / 'openjev.pause'
    try:
        if pause.exists():
            raise ValueError('Enrichment paused; no inference started')
        verify(main, db); settings = meta(db)
        if settings['policy'] != VERSION:
            raise ValueError('Grouped paragraph plan requires the grouped scorer; legacy per-fragment scorer refused')
        if settings.get('jev_identity') is not None and settings['jev_identity'] != client.identity:
            raise ValueError('Jev identity changed; use a fresh plan')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('jev_identity', dumps(client.identity)))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('inference_started', 'true')); db.commit()
        votes = Votes(db, client, pause)
        for entry in db.execute("SELECT * FROM entities WHERE status!='scored' ORDER BY id LIMIT ?", (limit or -1,)).fetchall():
            verify(main, db, entry['id']); target = entity(main, entry['id'])
            reference = load_document(main, entry['reference_id'])
            coverage = Coverage(evidence_unit(p, f, reference['id']) for p in paragraph_rows(db, reference['id']) for f in fragments(p, 600))
            for rid in json.loads(entry['order_json']):
                if rid == reference['id']:
                    continue
                if pause.exists():
                    raise ValueError('Enrichment paused')
                source = load_document(main, rid); source['_model'] = client.model
                whole_request = request_for(target, source, reference, coverage, 'whole_document')
                paragraphs = paragraph_rows(db, rid)
                full_context = votes.fits(whole_request)
                if full_context:
                    whole = votes.ask(whole_request)
                    whole['basis'] = 'direct_whole_document'
                else:
                    # Global blocks see the SAME initial coverage: no part of
                    # this candidate can make its own document redundant.
                    document_votes = [v for p in paragraphs for f in fragments(p) for v in
                        score_fragment(votes, target, source, reference, coverage, f, 'whole_document_block', False)]
                    whole = {**rollup(document_votes), 'scores_pct': None, 'basis': 'all_document_blocks', 'votes': document_votes}
                considered = whole['decision'] != 'redundant'
                paragraph_results = []; additions = []
                for paragraph in paragraphs:
                    results = [v for f in fragments(paragraph) for v in score_fragment(votes, target, source, reference, coverage, f, full_context=full_context)]
                    if any(v.get('evidence_mode') != 'full_documents' for v in results):
                        full_context = False
                    aggregate = rollup(results)
                    select = 'skip_redundant' if aggregate['decision'] == 'redundant' else (
                        'add' if aggregate['decision'] == 'useful' else 'add_uncertain')
                    if not considered:
                        select = 'ignored_by_document_vote'
                    result = {**aggregate, 'votes': results, 'considered': considered,
                              'document_id': rid, 'paragraph': paragraph['label']}
                    db.execute('UPDATE paragraphs SET status=?,result_json=? WHERE id=?', (select, dumps(result), paragraph['id'])); db.commit()
                    paragraph_results.append((paragraph, result, select))
                    if select in ('add', 'add_uncertain'):
                        added = [evidence_unit(paragraph, f, rid) for f in fragments(paragraph, 600)]
                        # Progressive coverage is updated BEFORE the next paragraph.
                        coverage.extend(added); additions.append(paragraph['id'])
                    counts[select] += 1
                global_result = whole
                global_result['paragraph_votes_always_requested'] = True
                global_result['paragraph_ids_added_to_coverage'] = additions
                global_result['disagreement'] = bool(whole['decision'] == 'redundant' and any(
                    result['decision'] == 'useful' for _, result, _ in paragraph_results))
                db.execute("UPDATE documents SET status='scored',result_json=? WHERE id=?", (dumps(global_result), rid)); db.commit()
            verify(main, db, entry['id'])
            db.execute("UPDATE entities SET status='scored' WHERE id=?", (entry['id'],)); db.commit(); counts['entities'] += 1
        verify(main, db)
        remaining = db.execute("SELECT count(*) FROM entities WHERE status!='scored'").fetchone()[0]
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('state', dumps('scored' if not remaining else 'partially_scored'))); db.commit()
        result = {**counts, 'remaining_entities': remaining, 'model_calls': votes.calls, 'cache_hits': votes.hits,
                  'main_database_modified': False, 'writer_started': False}
        atomic_json(Path(directory) / 'scoring-status.json', result)
        return result
    finally:
        db.close()
