"""Deterministic document wiring; metadata citations are not verified source claims."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .store import dumps, stable


def native_links(store, external=None):
    counts = {'wiki_links': 0, 'wiki_redirect_candidates': 0, 'swapi_foreign_keys': 0, 'sql_foreign_keys': 0}
    def connect(subject, target, kind, context):
        store.db.execute('INSERT OR REPLACE INTO document_links VALUES(?,?,?,?,?,?)',
                         (subject['id'], target['id'], kind, subject['current_version'], target['current_version'], dumps(context)))
    wiki = {r['name'].replace('_', ' '): dict(r) for r in store.db.execute("SELECT r.id,r.current_version,v.name FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.active=1 AND s.dataset='d6holocron-2023'")}
    for row in store.db.execute("SELECT r.id,r.current_version,v.body FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.active=1 AND s.dataset='d6holocron-2023'"):
        redirect = re.search(r'^\s*#redirect\s*\[\[([^\]|]+)', row['body'], re.I | re.M)
        for match in re.finditer(r'\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]', row['body']):
            title = match[1].split('#', 1)[0].strip().replace('_', ' ')
            target = wiki.get(title) or wiki.get(title[:1].upper() + title[1:])
            if target is None or target['id'] == row['id']:continue
            kind = 'wiki_redirect' if redirect and redirect[1] == match[1] else 'wiki_link'
            connect(row, target, kind, {'verification': 'declared_wiki_markup', 'target_title': match[1]})
            counts['wiki_links'] += 1
            if kind == 'wiki_redirect' and '#' not in match[1]:
                a, b = sorted([row['id'], target['id']])
                store.enqueue('identity', a, b, context={'reason': 'declared_wiki_redirect'}, score=120, reasons=['wiki_redirect'])
                counts['wiki_redirect_candidates'] += 1
    def url_key(value):
        if not isinstance(value, str):return None
        match = re.search(r'/(people|planets|films|species|starships|vehicles)/(\d+)/?$', urlsplit(value).path)
        return match.groups() if match else None
    swapi = {}
    for row in store.db.execute("SELECT r.id,r.current_version,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.active=1 AND s.dataset LIKE 'swapi/%'"):
        payload = json.loads(row['payload_json']);swapi[url_key(payload.get('url'))] = (dict(row), payload)
    for row, payload in swapi.values():
        for field, values in payload.items():
            if field in {'url', 'created', 'edited'}:continue
            for value in values if isinstance(values, list) else [values]:
                key = url_key(value)
                if key and key in swapi:
                    connect(row, swapi[key][0], 'foreign_key:' + field, {'verification': 'declared_swapi_url', 'field': field, 'value': value})
                    counts['swapi_foreign_keys'] += 1
    path = Path(external) / 'swd6' / 'pgdata' / 'initial.sql' if external else None
    if path and path.is_file():
        text = path.read_text(encoding='utf-8')
        pattern = r'ALTER TABLE(?: ONLY)?\s+(\w+)\s+ADD CONSTRAINT\s+\w+\s+FOREIGN KEY\s*\((\w+)\)\s+REFERENCES\s+(\w+)\s*\((\w+)\)'
        for table, field, target_table, target_field in re.findall(pattern, text, re.I):
            source = stable('src-web', 'swd6-sql/' + table);target_source = stable('src-web', 'swd6-sql/' + target_table)
            targets = {str(json.loads(r['payload_json']).get(target_field)): dict(r) for r in store.db.execute('SELECT r.id,r.current_version,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1 AND r.source_id=?', (target_source,))}
            for row in store.db.execute('SELECT r.id,r.current_version,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1 AND r.source_id=?', (source,)):
                value = json.loads(row['payload_json']).get(field)
                if value is not None and str(value) in targets:
                    connect(row, targets[str(value)], 'foreign_key:' + field, {'verification': 'declared_sql_foreign_key', 'table': table, 'field': field, 'target_table': target_table, 'target_field': target_field, 'value': value})
                    counts['sql_foreign_keys'] += 1
    # New snapshots retain native wiki titles and public HTML URLs, independently
    # of lexical mention mining. Mirrors are source-linked, not counted as new canon.
    from .acquire import HTMLText
    web=[dict(r) for r in store.db.execute("SELECT r.id,r.source_key,r.current_version,v.name,v.body,v.payload_json,v.locator_json,s.dataset FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.active=1 AND s.dataset LIKE 'web/%'")]
    titles={(r['dataset'],r['name'].replace('_',' ')):r for r in web}
    urls={json.loads(r['locator_json']).get('url','').rstrip('/'):r for r in web}
    counts['acquired_web_native_links']=0
    native_keys={(r['dataset'],r['source_key']):r for r in web}
    for row in web:
        if row['dataset'] in {'web/episode-d6','web/wookieepedia-darkstryder'}:
            redirect=re.search(r'^\s*#redirect\s*\[\[([^\]|]+)',row['body'],re.I|re.M)
            for match in re.finditer(r'\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]',row['body']):
                title=match[1].split('#',1)[0].strip().replace('_',' ')
                target=titles.get((row['dataset'],title))
                if target and target['id']!=row['id']:
                    kind='wiki_redirect' if redirect and title==redirect[1].strip() else 'wiki_reference'
                    connect(row,target,kind,{'verification':'declared_native_wiki_link','title':title,'offset':match.start()})
                    counts['acquired_web_native_links']+=1
        else:
            payload=json.loads(row['payload_json']);links=payload.get('outgoing_urls',[])
            if row['dataset']=='web/seawolf-atlas':
                for key in payload.get('contains_native_keys',[]):
                    target=native_keys.get((row['dataset'],key))
                    if target:
                        connect(row,target,'atlas_contains_reference',{'verification':'declared_native_atlas_card','native_key':key})
                        counts['acquired_web_native_links']+=1
            if row['dataset']=='web/d6db-library' and payload.get('book_slug'):
                parent=native_keys.get((row['dataset'],'/library/'+payload['book_slug']+'#index'))
                if parent:
                    connect(parent,row,'library_contains_unit',{'verification':'declared_native_book_route_and_pagination',
                            'book_slug':payload['book_slug'],'website_unit':payload.get('website_unit',{})})
                    counts['acquired_web_native_links']+=1
            if row['dataset']=='web/seawolf-files':
                for parent in payload.get('parent_articles',[]):
                    target=native_keys.get(('web/seawolf-star-wars',str(parent['key'])))
                    if target:
                        connect(row,target,'attachment_page_of',{'verification':'declared_public_article_attachment',
                                'file_name':payload.get('file_name'),'legacy_urls':payload.get('legacy_urls',[])})
                        counts['acquired_web_native_links']+=1
            if row['dataset']=='web/opend6-srd' and payload.get('category'):
                parent=native_keys.get((row['dataset'],'/srd'))
                if parent:
                    connect(parent,row,'srd_contains_card',{'verification':'declared_srd_category_and_card','category':payload['category']})
                    counts['acquired_web_native_links']+=1
            if row['dataset']=='web/swrpggm':
                parser=HTMLText();parser.feed(payload.get('content',{}).get('rendered',''));links=parser.links
            for url in set(links):
                target=urls.get(url.split('#')[0].rstrip('/'))
                if target and target['id']!=row['id']:
                    connect(row,target,'html_reference',{'verification':'declared_native_html_link','url':url})
                    counts['acquired_web_native_links']+=1
    return counts


def wire(store, external=None):
    # This table is entirely derived; rebuild atomically so removed upstream pointers disappear too.
    store.db.execute('DELETE FROM document_links')
    store.db.execute("DELETE FROM mining_gaps WHERE stage='wiring'")
    count = missing = component_missing = 0
    for row in store.db.execute("SELECT r.id,r.current_version,v.locator_json FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1 AND v.origin='phase3_extraction'"):
        locator = json.loads(row['locator_json'])
        source = stable('src-web', 'transcript/' + locator['source_id'])
        for field in ('profile_pages', 'role_pages'):
            for unit in locator.get(field, []):
                if not isinstance(unit, int):
                    continue
                target = store.db.execute('SELECT id,current_version FROM records WHERE source_id=? AND source_key IN (?,?) AND active=1', (source, str(unit), str(unit).zfill(4))).fetchone()
                if target is None:
                    missing += 1
                    store.gap(row['id'], 'wiring', 'cited_unit_not_imported', {'unit': unit, 'source_id': locator['source_id']})
                    continue
                context = {'source_id': locator['source_id'], 'unit': unit, 'field': field, 'verification': 'declared_in_existing_extraction_not_reverified'}
                store.db.execute('INSERT OR REPLACE INTO document_links VALUES(?,?,?,?,?,?)', (row['id'], target['id'], field, row['current_version'], target['current_version'], dumps(context)))
                count += 1
    source = stable('src-web', 'statbase/Starships')
    for row in store.db.execute("SELECT r.id,r.current_version,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE s.dataset='statbase/Starship Weapons' AND r.active=1"):
        data = json.loads(row['payload_json'])
        parent_key = data.get('Starship Sheet Index')
        target = store.db.execute('SELECT id,current_version FROM records WHERE source_id=? AND source_key=? AND active=1', (source, parent_key)).fetchone()
        if target:
            context = {'foreign_key': 'Starship Sheet Index', 'value': parent_key, 'verification': 'worksheet_foreign_key'}
            store.db.execute('INSERT OR REPLACE INTO document_links VALUES(?,?,?,?,?,?)', (row['id'], target['id'], 'profile_component_of', row['current_version'], target['current_version'], dumps(context)))
            count += 1
        else:
            component_missing += 1
            store.gap(row['id'], 'wiring', 'component_parent_not_imported', {'parent_key': parent_key})
    native = native_links(store, external)
    store.db.commit()
    return {'document_links_written': count, 'unresolved_unit_references': missing, 'unresolved_component_references': component_missing, **native}
