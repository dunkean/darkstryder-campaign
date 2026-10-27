"""Read-only adapters for already acquired corpora. No network, OCR or model calls."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .store import confined, dumps


KINDS = {
    'NOTABLE_NPCS': 'person', 'CREATURES_DATA': 'creature_type', 'RACES_DATA': 'species',
    'PLANETS_DATA': 'location', 'SYSTEMS_DATA': 'location', 'STARSHIP_DATA': 'craft_model',
    'VEHICLES_DATA': 'craft_model', 'WEAPONS_DATA': 'equipment_model', 'EQUIPMENT_DATA': 'equipment_model',
    'ARMOR_DATA': 'equipment_model', 'DROID_COMPONENTS': 'equipment_model',
    'FORCE_POWERS_DATA': 'force_power', 'POWERS_DATA': 'force_power',
    'PDF_SHIPS': 'craft_model', 'PDF_VEHICLES': 'craft_model', 'PDF_DROIDS': 'droid_model',
    'PDF_SPECIES': 'species', 'PDF_WEAPONS_MELEE': 'equipment_model', 'PDF_WEAPONS_RANGED': 'equipment_model',
    'PDF_SHIP_WEAPONS': 'equipment_model', 'PDF_EQUIPMENT': 'equipment_model', 'PDF_TEMPLATES': 'archetype',
    'ADV_SKILLS': 'skill',
}
CATEGORY_KINDS = {'character': 'person', 'craft': 'craft_model', 'location': 'location', 'organization': 'organization',
                  'species': 'species', 'technology': 'equipment_model', 'rules': 'rule', 'adventure': 'scenario',
                  'history': 'event', 'intrigue': 'scenario', 'legend': 'concept', 'setting': 'mixed_document'}
PROFILE_KINDS = {'person', 'archetype', 'species', 'creature_type', 'creature_individual', 'droid_model', 'droid_individual',
                 'craft_model', 'craft_individual', 'equipment_model', 'item_individual', 'force_power', 'skill', 'profile_component'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_jsonl(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():yield json.loads(line)


def text_body(value):
    # Retain complete raw payload separately; body is useful searchable text, not a regenerated stat sheet.
    return json.dumps(value, ensure_ascii=False, indent=2)


def row_key(row):
    """Native identity when supplied; otherwise content identity, never array position."""
    if isinstance(row, dict):
        for field in ('_id', 'id', 'uuid', 'uid', 'url'):
            if row.get(field) not in (None, ''):
                return field + ':' + str(row[field])
    return 'sha256:' + hashlib.sha256(dumps(row).encode('utf-8')).hexdigest()


def item(dataset, key, payload, name=None, kind='unknown', locator=None, origin='fan_compilation', universe='star_wars', body=None, aliases=None, scope=None):
    scalar_name = payload if isinstance(payload, str) else None
    if not isinstance(payload, dict):
        payload = {'value': payload}
    name = name or scalar_name or payload.get('name') or payload.get('Name') or payload.get('title') or str(key)
    if not isinstance(name, (str, int)):
        name = str(key)
    aliases = aliases or payload.get('aliases', [])
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        aliases = []
    scope = scope or {k: payload[k] for k in ['era', 'Era', 'edition', 'book', 'source', 'affiliation', 'canon'] if payload.get(k) not in (None, '')}
    return dict(dataset=dataset, key=str(key), name=str(name), payload=payload, kind=kind,
                body=body if body is not None else text_body(payload), aliases=aliases, origin=origin, universe=universe,
                scope=scope, locator=locator or {},
                profile=payload if kind in PROFILE_KINDS and origin in {'fan_compilation', 'reup_adaptation', 'statbase_compilation'} else None)


def derived(external):
    for repo in ['SW-WEG-D6-Toolkit', 'swd6-character-generator']:
        for path in sorted((external / 'derived-json' / repo).glob('*.json')):
            value = read(path); variable = path.stem; dataset = repo + '/' + variable
            relative = path.relative_to(external).as_posix()
            def emit(key, row, name=None, kind=None, pointer=None):
                return item(dataset, key, row, name, kind or KINDS.get(variable, 'rule'),
                            {'kind': 'json_pointer', 'file': relative, 'pointer': '/' + str(pointer if pointer is not None else key)})
            if isinstance(value, list):
                for i, row in enumerate(value):
                    yield emit(row_key(row), row, pointer=i)
            elif variable in ['STARSHIP_DATA', 'ARMOR_DATA']:
                for category, rows in value.items():
                    for i, row in enumerate(rows):
                        record = emit(category + '/' + row_key(row), row, pointer=category + '/' + str(i))
                        record['scope']['category'] = category
                        if 'Named' in category:
                            record['kind'] = 'craft_individual'
                        yield record
            elif variable == 'RACES_DATA':
                for name, row in value.items():
                    yield emit(name, row, name)
            elif variable == 'DATA':
                categories = {'species': 'species', 'nearHumans': 'species', 'equipment': 'equipment_model',
                              'armor': 'equipment_model', 'melee': 'equipment_model', 'ranged': 'equipment_model',
                              'explosives': 'equipment_model', 'powers': 'force_power', 'planets': 'location'}
                for category, rows in value.items():
                    if category in categories and isinstance(rows, list):
                        for i, row in enumerate(rows):
                            yield emit(category + '/' + row_key(row), row, kind=categories[category], pointer=category + '/' + str(i))
                    elif category == 'skills' and isinstance(rows, dict):
                        for attribute, skills in rows.items():
                            for i, row in enumerate(skills if isinstance(skills, list) else [skills]):
                                yield emit(category + '/' + attribute + '/' + row_key(row), row, kind='skill', pointer=category + '/' + attribute + '/' + str(i))
                    else:
                        yield emit(category, rows, variable + ': ' + category, 'rule')
            else:
                # Generator tables, lists of names and library indices are documents/tables, not individual people.
                for key, row in value.items():
                    yield emit(key, row, variable + ': ' + key, 'rule' if variable != 'LIBRARY_DATA' else 'mixed_document')
    # The downloaded rooms table has invalid JS syntax. Preserve it as searchable data, never execute it.
    path = external / 'SW-WEG-D6-Toolkit' / 'data_rooms.js'
    if path.is_file():
        raw = path.read_text(encoding='utf-8-sig')
        for start in range(0, len(raw), 8000):
            yield item('SW-WEG-D6-Toolkit/unparsed-rooms', str(start), {'format': 'javascript', 'parse_status': 'unparsed_not_executed'},
                       name='Room tables — raw fragment ' + str(start // 8000 + 1), kind='mixed_document', body=raw[start:start + 8000],
                       locator={'kind': 'text_offset', 'file': path.relative_to(external).as_posix(), 'offset_start': start, 'offset_end': min(start + 8000, len(raw))},
                       scope={'parse_status': 'unparsed_not_executed'})


def foundry(external):
    kinds = {'d6htemplates': 'archetype', 'reupgmpc': 'archetype', 'reuppowers': 'force_power',
             'reupskills': 'skill', 'advskills': 'skill', 'reupstarfighter': 'craft_model',
             'reupgear': 'equipment_model', 'reupweaps': 'equipment_model', 'reupcommodity': 'equipment_model'}
    folder = external / 'starwarsd6-essentialcompanion-od6s' / 'packs'
    for path in sorted(folder.glob('*.db')):
        for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line); key = row.get('_id') or row_key(row)
            record = item('essential-companion/' + path.stem, key, row, kind=kinds.get(path.stem, 'mixed_document'),
                          locator={'kind': 'ndjson_record', 'file': path.relative_to(external).as_posix(), 'line': line_number, 'upstream_id': key},
                          origin='reup_adaptation')
            record['scope']['edition'] = 'REUP'
            yield record
    for path in sorted((external / 'Holocron-Designer' / 'Star Wars D6' / 'Resources').glob('*.json')):
        document = read(path)
        if path.stem == 'Export_Template':
            continue  # Filled example/export skeleton, not an independent reference character.
        for i, row in enumerate(document.get('items', [])):
            yield item('holocron-designer/' + path.stem, row.get('_id') or row_key(row), row,
                       kind='force_power' if path.stem == 'force_powers' else 'equipment_model',
                       locator={'kind': 'json_pointer', 'file': path.relative_to(external).as_posix(), 'pointer': '/items/' + str(i)}, origin='reup_adaptation')


def sql_copy(external):
    path = external / 'swd6' / 'pgdata' / 'initial.sql'
    if not path.is_file():
        return
    text = path.read_text(encoding='utf-8')
    kinds = {'race': 'species', 'planet': 'location', 'skill': 'skill', 'skill_specialization': 'skill',
             'force_ability': 'force_power', 'force_power': 'force_power', 'attribute': 'skill', 'armor': 'equipment_model'}
    for match in re.finditer(r'COPY\s+(\w+)\s+\(([^\n]+)\)\s+FROM stdin;\r?\n(.*?)\r?\n\\\.', text, re.S):
        table, columns, data = match.groups(); columns = [x.strip() for x in columns.split(',')]
        for i, line in enumerate(data.splitlines()):
            cells = line.split('\t')
            if len(cells) != len(columns):
                raise ValueError('Malformed PostgreSQL COPY row in ' + table)
            def decode(value):
                if value == r'\N':
                    return None
                return re.sub(r'\\([\\tnr])', lambda m: {'t': '\t', 'n': '\n', 'r': '\r', '\\': '\\'}[m[1]], value)
            row = dict(zip(columns, map(decode, cells)))
            yield item('swd6-sql/' + table, row.get('id') or i, row, name=row.get('name') or table + ':' + str(row.get('id') or i),
                       kind=kinds.get(table, 'mixed_document'), locator={'kind': 'sql_copy_row', 'file': path.relative_to(external).as_posix(), 'table': table, 'id': row.get('id'), 'row': i + 1})


def csv_starters(external):
    for path in sorted((external / 'starwarsd6' / 'data').glob('*/*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for i, row in enumerate(csv.DictReader(stream), 2):
                if not any(row.values()):
                    continue
                yield item('vehrka/' + path.stem, row_key(row), row, kind={'forcepowers': 'force_power', 'skills': 'skill'}.get(path.stem, 'equipment_model'),
                           locator={'kind': 'csv_row', 'file': path.relative_to(external).as_posix(), 'row': i},
                           scope={'edition': '1E + house rules'})


def lore(external):
    for path in sorted((external / 'swapi-info').glob('*.json')):
        if path.stem == 'manifest':
            continue
        for i, row in enumerate(read(path)):
            yield item('swapi/' + path.stem, row.get('url') or row_key(row), row,
                       kind={'people': 'person', 'planets': 'location', 'species': 'species', 'starships': 'craft_model', 'vehicles': 'craft_model'}.get(path.stem, 'mixed_document'),
                       origin='film_reference', locator={'kind': 'json_pointer', 'file': path.relative_to(external).as_posix(), 'pointer': '/' + str(i), 'url': row.get('url')})
    path = external / 'starwars-api' / 'api' / 'all.json'
    if path.is_file():
        for i, row in enumerate(read(path)):
            yield item('akabab/characters', row.get('id') or row_key(row), row, kind='person', origin='lore_summary',
                       locator={'kind': 'json_pointer', 'file': path.relative_to(external).as_posix(), 'pointer': '/' + str(i)})
    for path in sorted((external / 'starwars-databank-api' / 'complete').glob('*.json')):
        if path.stem == 'manifest':
            continue
        document = read(path)
        for i, row in enumerate(document.get('data', [])):
            yield item('databank/' + path.stem, row.get('_id') or row_key(row), row,
                       kind={'characters': 'person', 'creatures': 'creature_type', 'droids': 'droid_model', 'locations': 'location', 'organizations': 'organization', 'species': 'species', 'vehicles': 'craft_model'}.get(path.stem, 'unknown'),
                       origin='modern_canon_summary', locator={'kind': 'json_pointer', 'file': path.relative_to(external).as_posix(), 'pointer': '/data/' + str(i)},
                       scope={'continuity': 'modern_canon', 'edition': None})


def statbase(project):
    sid = 'src-6469212ce4f6d450'; path = project / 'content' / 'transcriptions' / sid / 'book.md'
    if not path.is_file():
        return
    kinds = {'Alien Species': ('Species', 'species'), 'Creatures': ('Creature', 'creature_type'),
             'Droids': ('Droid', 'droid_model'), 'Equipment': ('Item', 'equipment_model'),
             'Starships': ('Name', 'craft_model'), 'Vehicles': ('Name', 'craft_model'),
             'Weapons': ('Name', 'equipment_model'), 'Starship Weapons': ('Weapon', 'profile_component')}
    sheet = ''; headers = []; sheet_no = 0
    # U+2028 is embedded INSIDE spreadsheet cells. splitlines() would truncate 119 rows.
    for line_no, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
        if line.startswith('## Feuille '):
            sheet_no = int(line.split()[-1])
        if line.startswith('# '):
            sheet = line[2:].strip(); headers = []
        if line.startswith('| Sheet Index') or line.startswith('| Starship Weapon Sheet Index'):
            headers = [x.strip() for x in re.split(r'(?<!\\)\|', line.strip('|'))]
        if headers and re.match(r'^\| [A-Z]+\d+\s*\|', line):
            cells = [x.strip().replace(r'\|', '|').replace('<br>', '\n') for x in re.split(r'(?<!\\)\|', line.strip('|'))]
            row = {header: cells[i] if i < len(cells) else '' for i, header in enumerate(headers)}
            if len(cells) != len(headers):
                row['_parse_warning'] = {'columns': len(headers), 'cells': len(cells), 'raw_line': line}
            name_field, kind = kinds.get(sheet, ('Name', 'mixed_document'))
            row_id = row.get('Sheet Index') or row.get('Starship Weapon Sheet Index')
            name = row.get(name_field) or row.get('Weapon') or row.get('Vehicle') or row.get('Model') or row.get('Item') or row_id
            if sheet == 'Starships' and 'individual' in (row.get('Subcat') or '').casefold():
                kind = 'craft_individual'
            record = item('statbase/' + sheet, row_id, row, name, kind,
                       {'kind': 'worksheet_row', 'source_id': sid, 'sheet': sheet, 'unit': sheet_no, 'line': line_no, 'row_id': row_id, 'file': 'content/transcriptions/' + sid + '/book.md'},
                       scope={'source': row.get('Source'), 'data_source': row.get('Data Source'), 'edition': None, 'unfinished': row.get('Unfinished')},
                       origin='statbase_compilation')
            if '_parse_warning' in row:
                record['profile'] = None  # Never publish shifted columns as usable statistics.
            yield record


def local_documents(project, include_pages=True):
    root = project / 'content' / 'transcriptions'
    catalog = {x['id']: x for x in read(project / 'catalog' / 'sources.json')}
    for path in sorted((root / '_extraction' / 'documents').glob('src-*/*/*.md')):
        raw = path.read_text(encoding='utf-8-sig'); meta = {}; body = raw
        if raw.startswith('---\n'):
            _, front, body = raw.split('---', 2)
            for line in front.splitlines():
                key, sep, value = line.partition(':')
                if sep:
                    try:meta[key.strip()] = json.loads(value.strip())
                    except json.JSONDecodeError:meta[key.strip()] = value.strip().strip('"\'')
        sid = str(meta.get('source_id') or path.parents[1].name)
        if sid not in catalog or sid != path.parents[1].name:
            raise ValueError('Unregistered or mismatched phase-3 source: ' + str(path))
        source = catalog[sid]
        document_id = str(meta.get('id') or path.stem)
        category = str(meta.get('category') or path.parent.name)
        yield item('phase3/' + sid, document_id, meta, name=meta.get('name') or path.stem.rsplit('--', 1)[0],
                   kind=CATEGORY_KINDS.get(category, 'unknown'), body=body.strip(), aliases=meta.get('aliases'),
                   locator={'kind': 'extracted_document', 'source_id': sid, 'file': path.relative_to(project).as_posix(),
                            'profile_pages': meta.get('profile_pages', []), 'role_pages': meta.get('role_pages', [])},
                   origin='phase3_extraction', scope={'collection': source.get('collection'), 'source_title': source['name'], 'review_status': 'unreviewed'})
    if not include_pages:
        return
    # All admitted units remain searchable even before a phase-3 document exists. They are not identities.
    for sid, source in catalog.items():
        if sid == 'src-6469212ce4f6d450':
            continue  # Already indexed by worksheet/record rather than one enormous table-document.
        folder = root / sid
        if not (folder / 'book.md').is_file():
            continue
        paths = sorted((folder / 'pages').glob('*.md'))
        if paths:
            for path in paths:
                yield item('transcript/' + sid, path.stem, {'source_id': sid, 'unit': path.stem},
                           name=source['name'] + ' — unité ' + path.stem, kind='mixed_document',
                           body=path.read_text(encoding='utf-8-sig'), origin='transcribed_reference',
                           locator={'kind': 'document_unit', 'source_id': sid, 'unit': int(path.stem), 'unit_label': source.get('unitLabel', 'unit'), 'file': path.relative_to(project).as_posix()},
                           scope={'collection': source.get('collection'), 'format': source.get('format')})
        else:
            # Some copies include only assembled books. Preserve real existing unit markers when available.
            raw = (folder / 'book.md').read_text(encoding='utf-8-sig')
            marker = re.compile(r'(?m)^<!-- source: [^\n]+(?:pdf_page|document_unit):\s*(\d+)[^\n]*-->')
            matches = list(marker.finditer(raw))
            if matches:
                for i, match in enumerate(matches):
                    unit = int(match[1]); end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
                    yield item('transcript/' + sid, str(unit), {'source_id': sid, 'unit': unit},
                               name=source['name'] + ' — unité ' + str(unit), kind='mixed_document', body=raw[match.start():end],
                               origin='transcribed_reference', locator={'kind': 'document_unit', 'source_id': sid, 'unit': unit, 'file': (folder / 'book.md').relative_to(project).as_posix(), 'offset_start': match.start(), 'offset_end': end},
                               scope={'collection': source.get('collection'), 'format': source.get('format')})
            else:
                for i, start in enumerate(range(0, len(raw), 8000)):
                    yield item('transcript/' + sid, 'fragment-' + str(i), {'source_id': sid},
                               name=source['name'] + ' — fragment ' + str(i), kind='mixed_document', body=raw[start:start + 8000],
                               origin='transcribed_reference', locator={'kind': 'text_offset', 'source_id': sid, 'file': (folder / 'book.md').relative_to(project).as_posix(), 'offset_start': start, 'offset_end': min(start + 8000, len(raw))},
                               scope={'collection': source.get('collection'), 'format': source.get('format')})


def wiki(external):
    path = external / 'd6holocron-wikiteam' / 'history.xml'
    if not path.is_file():
        return
    latest = {}
    for _, page in ET.iterparse(path, events=('end',)):
        if page.tag.split('}')[-1] != 'page':
            continue
        ns = {'m': page.tag.split('}')[0][1:]}
        def find(element, key):return element.findtext('m:' + key, default='', namespaces=ns)
        if find(page, 'ns') not in {'0', '14', '10'}:
            page.clear(); continue
        page_id = find(page, 'id'); title = find(page, 'title')
        for revision in page.findall('m:revision', ns):
            revision_id = int(find(revision, 'id') or 0)
            if page_id not in latest or revision_id > latest[page_id][0]:
                latest[page_id] = (revision_id, title, find(revision, 'text'), find(revision, 'timestamp'), find(page, 'ns'))
        page.clear()
    for page_id, (revision_id, title, body, timestamp, namespace) in latest.items():
        categories = re.findall(r'\[\[Category:([^\]|]+)', body, re.I)
        foreign = re.search(r'\b(stargate|star trek|warhammer|mass effect|babylon 5|battlestar|halo|farscape|firefly|andromeda)\b', ' '.join(categories), re.I)
        universe = 'crossover' if foreign else 'unknown'
        yield item('d6holocron-2023', page_id, {'categories': categories, 'namespace': namespace}, title,
                   kind='concept' if namespace in {'14','10'} else 'unknown', body=body, origin='wiki_compilation', universe=universe,
                   locator={'kind': 'wiki_revision', **({'namespace':10} if namespace=='10' else {}), 'file': path.relative_to(external).as_posix(), 'page_id': page_id, 'revision_id': revision_id, 'timestamp': timestamp, 'url': 'http://d6holocron.com/wiki/index.php?oldid=' + str(revision_id)})


def acquired_web(external):
    from .sections import category_kind
    for path in web_snapshots(external):
        source=path.parent.name
        for row in read_jsonl(path):
            namespace=row.get('namespace',0)
            categories=row.get('categories',[])
            kind=category_kind(categories)
            section=row.get('section')
            if namespace in {10,14}:section='metadata';kind='mixed_document'
            if source=='swrpggm':
                if 'Source Guide' in categories and kind=='unknown':section='scenario';kind='scenario'
                if set(categories)&{'HoloNet News','Blog'} and kind=='unknown':section='metadata';kind='mixed_document'
            if row.get('format')=='html' and kind=='unknown':
                kind=category_kind([row['body'][:200]])
            body=row['body']
            # The category metadata is retained explicitly, not inferred from an entire book.
            scope={'categories':categories,'section':section or ('scenario' if kind=='scenario' else 'reference'),
                   'review_status':'unreviewed','language':'fr' if source.startswith(('anakinworld','holonet')) else 'en'}
            universe='star_wars'
            if source=='episode-d6':
                universe='crossover' if re.search(r'\b(stargate|star trek|warhammer|mass effect|babylon 5|battlestar|halo|farscape|firefly|andromeda)\b',' '.join(categories),re.I) else 'unknown'
                scope['lineage']='d6holocron-2023';scope['not_independent_source']=True
            if source=='wookieepedia-darkstryder':scope['continuity']='Legends' if any('Legends' in x for x in categories) else 'unspecified'
            origin='fan_conversion' if source=='swrpggm' else 'wiki_compilation' if source=='episode-d6' else 'lore_summary'
            if source=='opend6-srd':
                universe='generic_d6'
                origin='open_d6_reference'
                kind=row['kind']
                scope.update(row.get('scope',{}))
            elif source in {'rpggamer-d6','d6db-compendium','seawolf-star-wars','seawolf-atlas','seawolf-files','d6db-library'}:
                universe=row['universe']
                origin=row['origin']
                kind=row['kind']
                scope.update(row.get('scope',{}))
            receipt=row['receipt']
            locator={'kind':row['format'],'file':path.relative_to(external).as_posix(),'native_id':row['key'],
                     'url':row['url'],'revision_id':row.get('revision_id'),'namespace':namespace,
                     'retrieved_at':receipt['retrieved_at'],'response_sha256':receipt['sha256'],'response_file':receipt['path']}
            if row.get('unit'):locator['unit']=row['unit']
            if source in {'opend6-srd','rpggamer-d6','d6db-compendium','seawolf-star-wars','seawolf-atlas','seawolf-files','d6db-library'}:
                locator['response_root']=path.parent.parent.relative_to(external).as_posix()
            record=item('web/'+source,row['key'],row.get('payload',{}),name=row['name'],kind=kind,body=body,aliases=row.get('aliases'),
                        origin=origin,universe=universe,scope=scope,locator=locator)
            if source=='d6db-compendium':
                # Native dice/stat fields are a profile; HTML and page widgets
                # remain evidence, never inferred equipment or character stats.
                record['profile']=row.get('payload',{}).get('source_data') if kind in PROFILE_KINDS else None
            elif source in {'seawolf-atlas','seawolf-star-wars'}:
                record['profile']=None
            yield record


def web_snapshots(external):
    latest={}
    for path in sorted(external.glob('web-*/*/records.jsonl')):latest[path.parent.name]=path
    return sorted(latest.values())


ADAPTERS = {'derived': derived, 'foundry': foundry, 'sql': sql_copy, 'csv': csv_starters, 'lore': lore, 'wiki': wiki, 'web':acquired_web}


def present_datasets(adapter, project, external, include_pages):
    """An existing empty collection is authoritative; an absent file is not a deletion."""
    if adapter == 'derived':
        return ([p.parent.name + '/' + p.stem for p in (external / 'derived-json').glob('*/*.json')] +
                (['SW-WEG-D6-Toolkit/unparsed-rooms'] if (external / 'SW-WEG-D6-Toolkit' / 'data_rooms.js').is_file() else []))
    if adapter == 'foundry':
        return (['essential-companion/' + p.stem for p in (external / 'starwarsd6-essentialcompanion-od6s' / 'packs').glob('*.db')] +
                ['holocron-designer/' + p.stem for p in (external / 'Holocron-Designer' / 'Star Wars D6' / 'Resources').glob('*.json') if p.stem != 'Export_Template'])
    if adapter == 'sql':
        path = external / 'swd6' / 'pgdata' / 'initial.sql'
        return ['swd6-sql/' + name for name in re.findall(r'COPY\s+(\w+)\s+\(', path.read_text(encoding='utf-8'))] if path.is_file() else []
    if adapter == 'csv':
        return ['vehrka/' + p.stem for p in (external / 'starwarsd6' / 'data').glob('*/*.csv')]
    if adapter == 'lore':
        return (['swapi/' + p.stem for p in (external / 'swapi-info').glob('*.json') if p.stem != 'manifest'] +
                (['akabab/characters'] if (external / 'starwars-api' / 'api' / 'all.json').is_file() else []) +
                ['databank/' + p.stem for p in (external / 'starwars-databank-api' / 'complete').glob('*.json') if p.stem != 'manifest'])
    if adapter == 'wiki':
        return ['d6holocron-2023'] if (external / 'd6holocron-wikiteam' / 'history.xml').is_file() else []
    if adapter == 'web':
        # Partial/inaccessible snapshots never retire previously admitted documents.
        return ['web/'+p.parent.name for p in web_snapshots(external)
                if read(p.parent/'status.json').get('status') in {'complete','complete_scope'}]
    root = project / 'content' / 'transcriptions'
    if adapter == 'statbase':
        path = root / 'src-6469212ce4f6d450' / 'book.md'
        return ['statbase/' + name for name in re.findall(r'^# (.+)$', path.read_text(encoding='utf-8'), re.M)] if path.is_file() else []
    return (['phase3/' + p.name for p in (root / '_extraction' / 'documents').glob('src-*') if p.is_dir()] +
            ['transcript/' + p.parent.name for p in root.glob('src-*/book.md') if p.parent.name != 'src-6469212ce4f6d450' and include_pages])


def ingest_all(store, project, external, selected=None, include_pages=True, limit=0):
    project, external = Path(project), Path(external)
    names = selected or ['derived', 'foundry', 'sql', 'csv', 'lore', 'wiki', 'statbase', 'local', 'web']
    sources = {}; counts = {}; total = changed = 0
    manifest_path = external / 'research-manifest.json'
    manifest = read(manifest_path) if manifest_path.is_file() else []
    roots = {'essential-companion': 'starwarsd6-essentialcompanion-od6s', 'holocron-designer': 'Holocron-Designer', 'swd6-sql': 'swd6', 'vehrka': 'starwarsd6', 'swapi': 'swapi-info', 'akabab': 'starwars-api', 'databank': 'starwars-databank-api', 'd6holocron-2023': 'd6holocron-wikiteam'}
    for adapter in names:
        if adapter == 'statbase':
            iterator = statbase(project)
        elif adapter == 'local':
            iterator = local_documents(project, include_pages)
        else:
            iterator = ADAPTERS[adapter](external)
        seen = updated = 0
        from .store import stable
        touched = {stable('src-web', dataset): set() for dataset in present_datasets(adapter, project, external, include_pages)}
        authoritative = set(touched)
        retired = 0
        run = store.db.execute('INSERT INTO imports(dataset,status) VALUES(?,?)', (adapter, 'running')).lastrowid
        store.db.commit()
        try:
            for record in iterator:
                dataset = record.pop('dataset')
                if dataset not in sources:
                    root = roots.get(dataset.split('/')[0], dataset.split('/')[0])
                    references = [entry for entry in manifest if entry.get('path', '').startswith(root + '/')]
                    sources[dataset] = store.source(dataset, dataset, record.get('origin', 'fan_compilation'), {'adapter': adapter, 'verification': 'not_required_for_admission', 'acquisition': references})
                rid, did_change = store.ingest(sources[dataset], **record)
                touched.setdefault(sources[dataset], set()).add(rid)
                seen += 1; updated += did_change; total += 1; changed += did_change
                if seen % 250 == 0:
                    store.db.execute('UPDATE imports SET records_seen=?,changed=? WHERE id=?', (seen, updated, run));store.db.commit()
                if limit and total >= limit:
                    break
            if not limit or total < limit:
                for source_id, admitted in touched.items():
                    if adapter=='web' and source_id not in authoritative:continue
                    old = [row[0] for row in store.db.execute('SELECT id FROM records WHERE source_id=? AND active=1', (source_id,)) if row[0] not in admitted]
                    for rid in old:
                        store.retire(rid);retired += 1
            store.db.execute('UPDATE imports SET records_seen=?,changed=?,status=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                             (seen, updated, 'partial' if limit and total >= limit else 'complete', run));store.db.commit()
            counts[adapter] = {'seen': seen, 'changed': updated, 'retired': retired}
        except BaseException:
            store.db.rollback()
            store.db.execute("UPDATE imports SET status='failed',records_seen=?,changed=? WHERE id=?", (seen, updated, run));store.db.commit()
            raise
        if limit and total >= limit:
            break
    return {'records_seen': total, 'changed': changed, 'adapters': counts}
