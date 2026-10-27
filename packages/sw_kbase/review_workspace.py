"""Document-first review of saved selection results. Read-only; no inference."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from .review import _json, _small, _page
from . import type_mining, provenance


def discover_selection(database):
    parent = Path(database).resolve().parent
    for filename, key in (('active-reference-workflow.json', 'enrichment'), ('pause-checkpoint.json', 'enrichment_output')):
        try:
            value = json.loads((parent / filename).read_text(encoding='utf-8-sig'))
            path = (Path(value[key]) / 'enrichment.sqlite').resolve()
            if path.is_file():
                return path
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


@contextmanager
def selection_database(main, filename):
    db = None
    try:
        if filename and Path(filename).is_file():
            db = sqlite3.connect(Path(filename).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            configured = db.execute("SELECT value_json FROM meta WHERE key='main_database'").fetchone()
            if configured and Path(json.loads(configured[0])).resolve() != main.path.resolve():
                db.close(); db = None
        yield db
    finally:
        if db is not None:
            db.close()


def _url(value):
    try:
        parsed = urlsplit(value or '')
        return value if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password else None
    except (TypeError, ValueError):
        return None


def _pages(locator):
    values = []
    for key in ('profile_pages', 'role_pages', 'pages'):
        items = locator.get(key, [])
        values.extend(items if isinstance(items, list) else [items])
    unit = locator.get('unit')
    for item in (locator, unit if isinstance(unit, dict) else {}):
        for key in ('page', 'page_number'):
            if item.get(key) is not None:
                values.append(item[key])
    return sorted({v for v in values if type(v) is int and 0 <= v <= 99999})[:100]


def _page_label(pages, locator):
    groups = []
    for page in pages:
        if groups and page == groups[-1][-1] + 1:
            groups[-1].append(page)
        else:
            groups.append([page])
    if groups:
        return 'p. ' + ', '.join(str(g[0]) if len(g) == 1 else f'{g[0]}–{g[-1]}' for g in groups)
    return 'Ligne ' + str(locator['line']) if type(locator.get('line')) is int else ''


def _document(main, rid):
    row = main.db.execute('SELECT r.id,r.source_id,r.current_version,v.name,v.kind,v.origin,'
        'v.scope_json,v.locator_json,length(v.body) body_characters,substr(v.body,1,220) excerpt,'
        's.dataset,s.title source_title FROM records r JOIN record_versions v ON v.id=r.current_version '
        'JOIN sources s ON s.id=r.source_id WHERE r.id=? AND r.active=1', (rid,)).fetchone()
    if row is None:
        raise KeyError(rid)
    value = dict(row); scope = _json(value.pop('scope_json'), {}); locator = _json(value.pop('locator_json'), {})
    dataset = value['dataset']; url = _url(locator.get('url')); pages = _pages(locator)
    if dataset.startswith('web/'):
        kind = 'web'
        providers = {'wookieepedia-darkstryder': 'Wookieepedia', 'd6db-compendium': 'D6DB',
            'd6db-library': 'D6DB · bibliothèque', 'rpggamer-d6': 'RPGGamer',
            'seawolf-atlas': 'Seawolf · atlas', 'seawolf-content': 'Seawolf’s Den', 'opend6': 'OpenD6'}
        provider = providers.get(dataset[4:], urlsplit(url).hostname if url else dataset[4:])
        label = provider + ' · ' + value['name']; group = label
        parsed = urlsplit(url) if url else None
        identity = urlunsplit(parsed._replace(fragment='')) if parsed else dataset + ':' + rid
    else:
        kind = 'extracted' if dataset.startswith('phase3/') else 'transcription' if locator.get('kind') in {'markdown_page', 'transcription'} else 'structured'
        group = str(scope.get('source_title') or scope.get('source') or value['source_title'])
        # A source label is a title, never an absolute local path.
        if re.match(r'^(?:[A-Za-z]:[\\/]|/)', group):
            group = re.split(r'[\\/]', group)[-1]
        label = group
        identity = str(locator.get('source_id') or value['source_id']) + ':' + group
    value.update(source_type=kind, source_label=label, source_group=group,
        source_group_id=hashlib.sha256(identity.encode()).hexdigest()[:24], source_url=url,
        pages=pages, page_label=_page_label(pages, locator), scope=_small(scope, 1200),
        is_reference=False, selection={'status': 'unavailable', 'counts': {}, 'total_paragraphs': 0},
        documentary_decision=None)
    typed = type_mining.record_annotation(main, rid, value['current_version'])
    if typed:
        value['original_kind'] = value['kind']; value['kind'] = typed['kind']; value['type_annotation'] = _small(typed, 1500)
    value['provenance'] = provenance.annotation(main, rid, value['current_version'])
    return value


def _triage(main, aid):
    if not main.db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_v1_jobs'").fetchone():
        return {}
    row = main.db.execute("SELECT j.run_id,j.result_json FROM jev_v1_jobs j JOIN jev_v1_runs r ON r.id=j.run_id "
        "WHERE j.subject_id=? AND j.kind='aggregate' AND j.status='complete' ORDER BY r.created_at DESC LIMIT 1", (aid,)).fetchone()
    if not row:
        return {}
    result = _json(row['result_json'], {}); values = {}
    for source in result.get('source_selection', []):
        value = {key: source.get(key) for key in ('merge_selection', 'reason', 'entity_score_pct', 'adds_information_score_pct', 'covered_by')}
        values[source['source_id']] = {**_small(value, 1000), 'run_id': row['run_id']}
    return values


def _decorate(db, documents, reference_id, triage):
    if not documents:
        return
    placeholders = ','.join('?' for _ in documents); ids = [d['id'] for d in documents]
    selected = {r['id']: r for r in db.execute('SELECT id,version_id,status,role FROM documents WHERE id IN (' + placeholders + ')', ids)} if db else {}
    counts = {}
    units = None
    if db:
        unit_row = db.execute("SELECT value_json FROM meta WHERE key='units_policy'").fetchone()
        units = _json(unit_row[0]) if unit_row else None
        for row in db.execute('SELECT document_id,status,count(*) n FROM paragraphs WHERE document_id IN (' + placeholders + ') GROUP BY document_id,status', ids):
            counts.setdefault(row['document_id'], {})[row['status']] = row['n']
    for doc in documents:
        row = selected.get(doc['id']); doc['is_reference'] = doc['id'] == reference_id
        if row:
            doc['selection'] = {'status': row['status'] if row['version_id'] == doc['current_version'] else 'stale',
                'counts': counts.get(doc['id'], {}), 'total_paragraphs': sum(counts.get(doc['id'], {}).values()), 'units_policy': units}
        doc['documentary_decision'] = triage.get(doc['id'])


def workspace(main, aid, params, filename=None):
    offset, limit = _page(params)
    row = main.db.execute('SELECT id,name,kind,key,warnings_json FROM '+type_mining.aggregate_table(main)+' WHERE id=?', (aid,)).fetchone()
    if not row:
        raise KeyError(aid)
    value = dict(row); value['warnings'] = _json(value.pop('warnings_json'), [])
    annotation = type_mining.aggregate_annotation(main, aid)
    if annotation: value['type_annotation'] = _small(annotation, 1500)
    count = main.db.execute("SELECT count(*) records,count(distinct r.source_id) source_collections,"
        "coalesce(sum(s.dataset LIKE 'web/%'),0) web_records FROM aggregate_members m JOIN records r ON r.id=m.record_id "
        'JOIN sources s ON s.id=r.source_id WHERE m.aggregate_id=?', (aid,)).fetchone()
    value['counts'] = dict(count)
    value['counts']['legacy_blocks'] = main.db.execute('SELECT count(*) FROM aggregate_text_blocks WHERE aggregate_id=?', (aid,)).fetchone()[0]
    value['aliases'] = [r[0] for r in main.db.execute('SELECT label FROM aggregate_aliases WHERE aggregate_id=? ORDER BY label LIMIT 100', (aid,))]
    with selection_database(main, filename) as db:
        entry = db.execute('SELECT reference_id,status FROM entities WHERE id=?', (aid,)).fetchone() if db else None
        ref = entry['reference_id'] if entry else None
        value['selection'] = {'available': bool(entry), 'status': entry['status'] if entry else 'unavailable',
            'paused': (main.path.parent / 'openjev.pause').exists(), 'reference_id': ref}
        ids = [r[0] for r in main.db.execute('SELECT record_id FROM aggregate_members WHERE aggregate_id=? '
            'ORDER BY CASE WHEN record_id=? THEN 0 ELSE 1 END,record_id LIMIT ? OFFSET ?', (aid, ref or '', limit + 1, offset))]
        documents = [_document(main, rid) for rid in ids[:limit]]
        reference = next((d for d in documents if d['id'] == ref), None)
        if ref and reference is None and main.db.execute('SELECT 1 FROM aggregate_members WHERE aggregate_id=? AND record_id=?', (aid, ref)).fetchone():
            reference = _document(main, ref)
        triage = _triage(main, aid)
        _decorate(db, documents + ([reference] if reference and reference not in documents else []), ref, triage)
        value['reference'] = reference
        value['documents'] = {'items': documents, 'offset': offset, 'limit': limit, 'has_more': len(ids) > limit}
    return value


def paragraphs(main, rid, params, filename=None):
    offset, limit = _page(params); status = params.get('status', 'all')
    if status not in {'all', 'reference', 'add', 'add_uncertain', 'skip_redundant', 'pending', 'retained'}:
        raise ValueError('Invalid paragraph status')
    document = _document(main, rid)
    aid = main.db.execute('SELECT aggregate_id FROM aggregate_members WHERE record_id=?', (rid,)).fetchone()
    with selection_database(main, filename) as db:
        entry = db.execute('SELECT reference_id FROM entities WHERE id=?', (aid[0],)).fetchone() if db and aid else None
        _decorate(db, [document], entry['reference_id'] if entry else None, _triage(main, aid[0]) if aid else {})
        available = bool(db and document['selection']['status'] not in {'unavailable', 'stale'})
        if available:
            where, args = 'document_id=?', [rid]
            if status == 'retained':
                where += " AND status IN ('reference','add','add_uncertain')"
            elif status != 'all':
                where += ' AND status=?'; args.append(status)
            rows = [dict(r) for r in db.execute('SELECT * FROM paragraphs WHERE ' + where + ' ORDER BY number LIMIT ? OFFSET ?', (*args, limit + 1, offset))]
        else:
            from .document_sections import split_sections
            body = main.db.execute('SELECT v.body FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.id=?', (rid,)).fetchone()[0]
            rows = [{**p, 'id': 'preview-' + rid + '-' + p['label'], 'status': 'pending', 'result_json': None,
                     'unit_json': json.dumps(p)} for p in split_sections(body)] if status in {'all', 'pending'} else []
            document['selection']['total_paragraphs'] = len(rows)
            rows = rows[offset:offset + limit + 1]
        items = []
        for row in rows[:limit]:
            result = _json(row['result_json'], {}); unit = _json(row.get('unit_json'), {})
            votes = result.get('votes', [])
            items.append({key: row[key] for key in ('id', 'label', 'number', 'offset_start', 'offset_end', 'status')} | {
                'body': row['body'][:6000], 'body_characters': len(row['body']), 'body_truncated': len(row['body']) > 6000,
                'format': unit.get('format', 'text'), 'path': unit.get('path', ''),
                'grouping_policy': unit.get('grouping_policy'), 'parser_span_count': len(unit.get('parser_spans', [])),
                'short_complete_document': unit.get('short_complete_document', False),
                'scores_pct': result.get('scores_pct'), 'decision': result.get('decision'),
                'exact_proof': any(v.get('selection_proof') for v in votes),
                'request_ids': list(dict.fromkeys(v['request_id'] for v in votes if v.get('request_id')))[:30]})
    return {'document': document, 'selection_available': available, 'items': items,
            'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def evaluated_request(main, rid, request_id, filename=None):
    _document(main, rid)
    with selection_database(main, filename) as db:
        row = db.execute('SELECT request_json,response_json FROM requests WHERE id=?', (request_id,)).fetchone() if db else None
        if not row:
            raise KeyError(request_id)
        request, response = json.loads(row['request_json']), json.loads(row['response_json'])
        if request.get('state', {}).get('SOURCE', {}).get('id') != rid:
            raise KeyError(request_id)
        return {'request': _small(request, 12000), 'response': _small(response, 1500), 'display_truncated': True}
