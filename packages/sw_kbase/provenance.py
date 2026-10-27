"""Two-layer, evidence-backed provenance. Never changes imported source versions."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import uuid
from urllib.parse import urlsplit

from .store import dumps
from .type_mining import source_fingerprint

POLICY = 'sw-document-provenance/1.2'
PROVIDERS = {'d6db-compendium': 'D6DB', 'd6db-library': 'D6DB · bibliothèque',
    'rpggamer-d6': 'RPGGamer', 'wookieepedia-darkstryder': 'Wookieepedia',
    'seawolf-atlas': 'Seawolf · atlas', 'swrpggm': 'SWRPGGM', 'opend6': 'OpenD6'}
DDL = '''
CREATE TABLE IF NOT EXISTS provenance_runs(id TEXT PRIMARY KEY,policy TEXT NOT NULL,created_at TEXT NOT NULL,report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_provenance(record_id TEXT PRIMARY KEY REFERENCES records(id),version_id TEXT NOT NULL,
 status TEXT NOT NULL,acquisition_json TEXT NOT NULL,works_json TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES provenance_runs(id));
CREATE TABLE IF NOT EXISTS provenance_history(run_id TEXT NOT NULL,record_id TEXT NOT NULL,value_json TEXT NOT NULL,
 PRIMARY KEY(run_id,record_id));
CREATE VIEW IF NOT EXISTS current_document_provenance AS
 SELECT p.* FROM document_provenance p JOIN records r ON r.id=p.record_id AND r.current_version=p.version_id;
CREATE VIEW IF NOT EXISTS document_source_citations AS
 SELECT p.record_id,p.version_id,p.status,
 json_extract(p.acquisition_json,'$.label') acquisition_origin,
 json_extract(p.acquisition_json,'$.dataset') acquisition_dataset,
 json_extract(p.acquisition_json,'$.url') acquisition_url,
 json_extract(w.value,'$.id') work_id,json_extract(w.value,'$.title') work_title,
 json_extract(w.value,'$.code') work_code,json_extract(w.value,'$.kind') work_kind,
 json_extract(w.value,'$.relationship') work_relationship,
 json_extract(w.value,'$.pages') pages,json_extract(w.value,'$.page_basis') page_basis,
 json_extract(w.value,'$.raw_citation') raw_citation
 FROM current_document_provenance p LEFT JOIN json_each(p.works_json) w ON 1=1;
'''


def _json(value):
    try: return json.loads(value) if isinstance(value, str) else value or {}
    except ValueError: return {}


def safe_url(value):
    try:
        p = urlsplit(value or '')
        return value if p.scheme in {'http', 'https'} and p.hostname and not p.username and not p.password else None
    except (ValueError, TypeError): return None


def norm(value):
    value = unicodedata.normalize('NFKD', html.unescape(value)).casefold()
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def title_text(value):
    value = re.sub(r'\[\[([^]|]+)(?:\|[^]]*)?\]\]', r'\1', value)
    value = re.sub(r'<[^>]*>', '', value)
    return re.sub(r'\s+', ' ', re.sub(r"'{2,3}", '', html.unescape(value))).strip(' \t*|;')


def split_citation(raw):
    # Printed page references are kept separate from PDF/page-unit locators.
    page = re.search(r'(?i)(?:\(?\b(?:pages?|pp?\.?|pgs?\.)\s*)([0-9]+(?:\s*[-–,/]\s*[0-9]+)*)(?:\))?', raw)
    pages = page[1] if page else None
    title = raw[:page.start()].rstrip(' ,;(') if page else raw
    return title_text(title), pages


class Registry:
    def __init__(self, catalog=(), books=()):
        self.titles = defaultdict(list)
        self.codes = defaultdict(list)
        for item in catalog:
            self.add(item['name'], item.get('collection'), item.get('id'))
        for item in books:
            self.add(item.get('Title', ''), code=item.get('WEG Number'))

    def add(self, title, collection=None, source_id=None, code=None):
        match = re.search(r'(?i)\bWEG[ -]?(\d{5})\b', str(code or title))
        code = 'WEG' + match[1] if match else None
        clean = re.sub(r'(?i)\bWEG[ -]?\d{5}\b', '', title)
        clean = re.sub(r'(?i)^\s*[- ]*(?:Star Wars\s*[-:]\s*)?', '', clean).strip(' -')
        if not clean: return
        kind = 'homemade' if collection == 'addons' else 'book' if code or collection in {'sourcebooks', 'darkstryder', 'guides-rules'} else 'content'
        item = {'title': clean, 'code': code, 'kind': kind, 'catalog_source_id': source_id}
        for alias in {norm(title), norm(clean)}:
            if item not in self.titles[alias]: self.titles[alias].append(item)
        if code and item not in self.codes[code]: self.codes[code].append(item)

    def resolve(self, title):
        candidates = self.titles.get(norm(title), [])
        aliases = {'darkstryder campaign boxed set':'WEG40209', 'darkstryder boxed set':'WEG40209'}
        if not candidates and norm(title) in aliases: candidates = self.codes.get(aliases[norm(title)], [])
        match = re.search(r'(?i)\bWEG[ -]?(\d{5})\b', title)
        if not candidates and match: candidates = self.codes.get('WEG' + match[1], [])
        # Multiple catalog spellings of the same code are not conflicting books.
        codes = {c['code'] for c in candidates}
        if candidates and (len(candidates) == 1 or len(codes) == 1 and None not in codes):
            return candidates[0]
        return None


def extract(row, registry=None):
    registry = registry or Registry()
    scope, locator, payload = (_json(row.get(k)) for k in ('scope_json', 'locator_json', 'payload_json'))
    dataset, body = row['dataset'], row.get('body', '')
    url = safe_url(locator.get('url'))
    source_type = ('website' if dataset.startswith('web/') else 'wiki_archive' if locator.get('kind') == 'wiki_revision' else 'local_extraction' if dataset.startswith('phase3/')
        else 'local_transcription' if locator.get('kind') in {'markdown_page', 'transcription'} else 'database')
    provider = (PROVIDERS.get(dataset[4:], urlsplit(url).hostname if url else dataset[4:]) if dataset.startswith('web/')
        else 'Transcription locale · extraction' if dataset.startswith('phase3/') else dataset.split('/')[0])
    if dataset == 'd6holocron-2023': provider = 'D6 Holocron · archive wiki'
    metadata = _json(row.get('source_metadata_json'))
    repositories = {a['repository'] for a in metadata.get('acquisition', []) if a.get('repository')}
    repository_url = safe_url('https://github.com/' + next(iter(repositories))) if len(repositories) == 1 else None
    acquisition = {'type': source_type, 'label': provider, 'dataset': dataset, 'source_id': row['source_id'],
        'url': url, 'native_id': locator.get('native_id') or locator.get('page_id') or locator.get('row_id'),
        'repository_url': repository_url,
        'retrieved_at': locator.get('retrieved_at'), 'local_source_id': locator.get('source_id'),
        'unit': locator.get('kind'), 'response_sha256': locator.get('response_sha256')}
    works = []

    def add(raw, field, relationship='cites', offset=None, kind=None, pages=None, page_basis=None, proof_override=None):
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 1200: return
        raw = raw.strip()
        if norm(raw) in {'unknown', 'none', 'n a', 'various', 'source', 'sources', 'yes', 'no'}: return
        title, printed_pages = split_citation(raw)
        if not title or len(title) < 2: return
        if norm(title) in {'unknown', 'none', 'n a', 'various'}: return
        known = registry.resolve(title)
        code_match = re.search(r'(?i)\bWEG[ -]?(\d{5})\b', title)
        code = known['code'] if known else 'WEG' + code_match[1] if code_match else None
        is_site = bool(safe_url(title) or re.search(r'(?i)\b[\w-]+\.(?:com|org|net)\b', title)
                       or re.match(r'(?i)^(?:Wookieepedia|RPGGamer|SWRPGGM|D6DB|D6 Holocron)\b', title))
        actual_kind = kind or ('web_content' if is_site else known['kind'] if known else 'book' if code else
                               'compilation' if relationship == 'compilation' else 'unclassified_content')
        # A short citation abbreviation is retained without guessing its expansion.
        unresolved = bool(re.fullmatch(r'[A-Z0-9&/ ,;-]{1,50}', title)) and not code and not known
        if unresolved: actual_kind = 'unresolved_citation'
        canonical = known['title'] if known else title
        identity = code or norm(canonical)
        proof = {'field': field, 'excerpt': raw, 'excerpt_sha256': hashlib.sha256(raw.encode()).hexdigest()}
        if offset is not None: proof.update(offset_start=offset, offset_end=offset+len(raw))
        if proof_override: proof = proof_override
        work = {'id': 'work-' + hashlib.sha256(identity.encode()).hexdigest()[:24], 'title': canonical,
            'raw_citation': proof['excerpt'], 'kind': actual_kind, 'code': code, 'relationship': relationship,
            'pages': printed_pages or pages, 'page_basis': 'cited_page' if printed_pages else page_basis,
            'identification': 'unresolved' if unresolved else 'catalog_match' if known else 'explicit_citation',
            'evidence': [proof]}
        if known and known.get('catalog_source_id'): work['catalog_source_id'] = known['catalog_source_id']
        duplicate = next((w for w in works if (w['id'],w['pages'],w['relationship']) == (work['id'],work['pages'],work['relationship'])), None)
        if duplicate:
            if proof not in duplicate['evidence']: duplicate['evidence'].append(proof)
        else: works.append(work)

    def fields(obj, prefix):
        if not isinstance(obj, dict): return
        for key, value in obj.items():
            normalized = norm(key)
            if normalized not in {'source', 'sources', 'source book', 'sourcebook', 'book', 'book title', 'reference', 'references', 'data source'}: continue
            values = value if isinstance(value, list) else [value]
            for part in values:
                add(part, prefix + '/' + key, 'compilation' if normalized in {'book', 'data source'} else 'cites')

    if dataset.startswith('phase3/') and scope.get('source_title'):
        pdf_pages = sorted(set(locator.get('profile_pages', []) + locator.get('role_pages', [])))
        add(scope['source_title'], '/scope/source_title', 'derived_from',
            kind='homemade' if scope.get('collection') == 'addons' else None,
            pages=', '.join(map(str,pdf_pages)) or None, page_basis='pdf_page')
    if dataset == 'web/d6db-library':
        add(payload.get('book_title') or scope.get('collection'), '/payload/book_title' if payload.get('book_title') else '/scope/collection', 'derived_from')
    if dataset == 'statbase/Books List': add(payload.get('Title'), '/payload/Title', 'describes')
    fields(scope, '/scope'); fields(payload, '/payload')
    # Root article properties only: a nested weapon's source is not the owner's source.
    fields(payload.get('source_object', {}), '/payload/source_object')
    for match in re.finditer(r'(?im)^\s*(?:[|*#-]\s*)?(?:\'{2,3})?(?:Source(?:s|book)?|Source Book|Reference(?:s)?|Data Source)(?:\'{2,3})?\s*[:=]\s*([^\n]+)', body):
        raw = match[1].strip()
        if raw.startswith(('"', '{', '[')) and body.lstrip().startswith('{'): continue
        add(raw, '/body/source_field', offset=match.start(1)+len(match[1])-len(match[1].lstrip()))
    # Wiki citation sections only; ordinary linked people/places are never works.
    for section in re.finditer(r'(?im)^==+\s*(?:Sources|Appearances|Bibliography|References)\s*==+\s*\n([\s\S]*?)(?=^==|\Z)', body):
        for line in re.finditer(r'(?m)^\s*\*\s*(?:\'{2})?\[\[([^]|\n]+)(?:\|[^]\n]+)?\]\]', section[1]):
            raw = line[1]; add(raw, '/body/wiki_citations', offset=section.start(1)+line.start(1))
    for ref in re.finditer(r'(?is)<ref\b[^>]*>(.*?)</ref>', body):
        # Recognize catalog works in inline references; don't invent a book from a person link.
        for link in re.finditer(r'\[\[([^]|]+)(?:\|[^]]+)?\]\]', ref[1]):
            if registry.resolve(link[1]): add(link[1], '/body/wiki_ref', offset=ref.start(1)+link.start(1))
    darkstryder = {'endgame': 'WEG40112', 'the kathol outback': 'WEG40118', 'kathol outback': 'WEG40118',
                  'the kathol rift': 'WEG40121', 'kathol rift': 'WEG40121',
                  'darkstryder campaign': 'WEG40209', 'the darkstryder campaign': 'WEG40209'}
    for template in re.finditer(r'(?i)\{\{DarkStryder\b([^{}]*)\}\}', body):
        book = re.search(r'(?i)(?:^|\|)\s*book\s*=\s*([^|]+)', template[1])
        code = darkstryder.get(norm(book[1])) if book else 'WEG40209'
        if code:
            # Exact template is the evidence, not the expanded title/code.
            add(code, '/body/DarkStryder_template', proof_override={'field': '/body/DarkStryder_template', 'excerpt': template[0],
                    'offset_start': template.start(), 'offset_end': template.end(),
                    'excerpt_sha256': hashlib.sha256(template[0].encode()).hexdigest(), 'mapping': 'DarkStryder book parameter'})
    substantive = [w for w in works if w['kind'] not in {'web_content','unresolved_citation'}]
    status = 'identified' if substantive else 'citation_unresolved' if works else 'not_stated'
    return {'status': status, 'acquisition': acquisition, 'works': works}


def annotation(store, rid, version):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='document_provenance'").fetchone(): return None
    row = store.db.execute('SELECT status,acquisition_json,works_json,run_id FROM document_provenance WHERE record_id=? AND version_id=?', (rid,version)).fetchone()
    if not row: return None
    return {'status': row['status'], 'acquisition': _json(row['acquisition_json']), 'works': _json(row['works_json']), 'run_id': row['run_id']}


def run(store, catalog=(), apply=False, datasets=None):
    before = source_fingerprint(store)
    books = [_json(r[0]) for r in store.db.execute("SELECT v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE s.dataset='statbase/Books List'")]
    registry = Registry(catalog, books)
    report = {'policy': POLICY, 'applied': apply, 'model_calls': 0, 'documents': 0, 'datasets': datasets,
              'statuses': Counter(), 'work_kinds': Counter(), 'by_dataset': {}, 'source_data_hash_before': before}
    writer = sqlite3.connect(store.path) if apply else None
    run_id = 'provenance-' + str(uuid.uuid4())
    try:
        if writer:
            writer.execute('PRAGMA foreign_keys=ON')
            writer.executescript(DDL); writer.execute('BEGIN IMMEDIATE')
            writer.execute('INSERT INTO provenance_runs VALUES(?,?,?,?)', (run_id,POLICY,datetime.now(timezone.utc).isoformat(),'{}'))
        query = 'SELECT r.id,r.current_version,r.source_id,s.dataset,s.metadata_json source_metadata_json,v.body,v.scope_json,v.locator_json,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.active=1'
        if datasets: query += ' AND s.dataset IN (' + ','.join('?' for _ in datasets) + ')'
        for row in store.db.execute(query+' ORDER BY r.id', datasets or []):
            value = extract(dict(row), registry); report['documents'] += 1; report['statuses'][value['status']] += 1
            for kind in {w['kind'] for w in value['works']}: report['work_kinds'][kind] += 1
            counts = report['by_dataset'].setdefault(row['dataset'], Counter()); counts[value['status']] += 1
            if writer:
                writer.execute('INSERT OR REPLACE INTO document_provenance VALUES(?,?,?,?,?,?)',
                    (row['id'],row['current_version'],value['status'],dumps(value['acquisition']),dumps(value['works']),run_id))
                writer.execute('INSERT INTO provenance_history VALUES(?,?,?)', (run_id,row['id'],dumps(value)))
        report['source_data_hash_after'] = source_fingerprint(store)
        if before != report['source_data_hash_after']: raise ValueError('Imported source data changed during provenance extraction')
        if writer:
            report['run_id'] = run_id
            writer.execute('UPDATE provenance_runs SET report_json=? WHERE id=?', (dumps(report),run_id)); writer.commit()
    except Exception:
        if writer: writer.rollback()
        raise
    finally:
        if writer: writer.close()
    return report
