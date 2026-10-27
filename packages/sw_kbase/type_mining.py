"""Evidence-based navigation types; source versions and curation snapshots stay intact."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import sqlite3
import uuid

from .cleaning import bucket
from .store import dumps

VERSION = 'sw-type-mining/2.1-source-definitions'
LABELS = {'vehicle': 'Véhicules', 'craft': 'Vaisseaux', 'location': 'Lieux',
          'system': 'Systèmes', 'stellar_route': 'Routes stellaires',
          'unknown': 'Non classés', 'person': 'Personnages', 'organization': 'Organisations',
          'species': 'Espèces', 'creature': 'Créatures', 'droid': 'Droïdes',
          'equipment': 'Équipement', 'document': 'Documents', 'scenario': 'Scénarios',
          'event': 'Événements', 'rule': 'Règles', 'skill': 'Compétences',
          'force_power': 'Pouvoirs de la Force', 'archetype': 'Archétypes'}
GROUND = r'\b(?:landspeeders?|airspeeders?|aircars?|cloud cars?|speeder bikes?|hover(?:craft|trains?|tanks?|cars?)|walkers?|repulsor[ -]?(?:craft|tanks?)|repulsorlift vehicles?|submarines?|helicopters?|copters?|ground vehicles?|wheeled vehicles?|tracked vehicles?|trucks?|motorcycles?|skiffs?|swoops?|sail barges?|atmospheric (?:fighters?|gunships?|transports?))\b'
SHIPS = r'\b(?:starships?|starfighters?|space ?ships?|space boats?|space transports?|capital ships?|freighters?|corvettes?|frigates?|cruisers?|battlecruisers?|battlestars?|dreadnoughts?|star destroyers?|shuttles?|assault ships?|space stations?)\b'
WEAPONS = r'\b(?:weapons?|pistols?|rifles?|blasters?|lightsabers?|light sabers?|cannons?|turrets?|grenades?|shotguns?|muzzleloaders?|crossbows?|swords?|carbines?|armou?r|equipment|gadgets?|comlinks?|bacta tanks?)\b'
ROUTES = r'\b(?:hyperlanes?|hyperroutes?|hyperspace (?:routes?|lanes?)|trade routes?|stellar routes?|routes? (?:hyperspatiales?|stellaires?))\b'
FIELDS = re.compile(r'(?im)^\s*(?:[-*+|]\s*)?["\']?\s*([a-z][a-z0-9_ /()\-]{0,36}?)["\']?\s*[:=]\s*([^\n]{1,350})')

DDL = '''
CREATE TABLE IF NOT EXISTS type_mining_runs(id TEXT PRIMARY KEY,policy TEXT NOT NULL,created_at TEXT NOT NULL,report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mined_record_types(record_id TEXT PRIMARY KEY REFERENCES records(id),version_id TEXT NOT NULL,
 kind TEXT NOT NULL,subtype TEXT,rule TEXT NOT NULL,evidence_json TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES type_mining_runs(id));
CREATE TABLE IF NOT EXISTS mined_aggregate_types(aggregate_id TEXT PRIMARY KEY REFERENCES aggregates(id),cleaning_run TEXT NOT NULL,
 anchor_version TEXT NOT NULL,kind TEXT NOT NULL,subtype TEXT,rule TEXT NOT NULL,evidence_json TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES type_mining_runs(id));
CREATE TABLE IF NOT EXISTS type_mining_history(run_id TEXT NOT NULL,subject_id TEXT NOT NULL,subject_kind TEXT NOT NULL,
 before_json TEXT,after_json TEXT NOT NULL,PRIMARY KEY(run_id,subject_id,subject_kind));
CREATE VIEW IF NOT EXISTS typed_aggregates AS
 SELECT a.id,a.key,a.name,coalesce(t.kind,a.kind) kind,a.universe,a.anchor_record_id,a.warnings_json,a.run_id,
        a.kind original_kind,t.subtype,t.rule type_rule,t.run_id type_run_id
 FROM aggregates a JOIN records r ON r.id=a.anchor_record_id
 LEFT JOIN mined_aggregate_types t ON t.aggregate_id=a.id AND t.cleaning_run=a.run_id AND t.anchor_version=r.current_version
 AND NOT EXISTS(SELECT 1 FROM json_each(t.evidence_json) e LEFT JOIN records proof ON proof.id=json_extract(e.value,'$.record_id')
                WHERE proof.current_version IS NOT json_extract(e.value,'$.version_id'));
CREATE VIEW IF NOT EXISTS typed_record_types AS
 SELECT r.id record_id,r.current_version version_id,coalesce(t.kind,v.kind) kind,v.kind original_kind,t.subtype,t.rule,t.run_id
 FROM records r JOIN record_versions v ON v.id=r.current_version
 LEFT JOIN mined_record_types t ON t.record_id=r.id AND t.version_id=r.current_version;
'''


def aggregate_table(store):
    return 'typed_aggregates' if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='typed_aggregates'").fetchone() else 'aggregates'


def record_annotation(store, rid, version):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='mined_record_types'").fetchone():
        return None
    row = store.db.execute('SELECT kind,subtype,rule,evidence_json,run_id FROM mined_record_types WHERE record_id=? AND version_id=?', (rid, version)).fetchone()
    if row:
        value = dict(row); value['evidence'] = json.loads(value.pop('evidence_json')); value['method'] = 'text_mining'
        return value


def aggregate_annotation(store, aid):
    if aggregate_table(store) == 'aggregates':
        return None
    row = store.db.execute('SELECT t.kind,t.subtype,t.rule,t.evidence_json,t.run_id FROM mined_aggregate_types t '
        'JOIN typed_aggregates a ON a.id=t.aggregate_id AND a.type_run_id=t.run_id WHERE t.aggregate_id=?', (aid,)).fetchone()
    if row:
        value = dict(row); value['evidence'] = json.loads(value.pop('evidence_json')); value['method'] = 'text_mining'
        return value


def _text(value):
    return re.sub(r'\s+', ' ', html.unescape(str(value))).strip().casefold()


def _field_spans(body):
    if body.lstrip().startswith('{'):
        try:
            obj = json.loads(body)
        except ValueError:
            return {}  # A partial large JSON is not evidence about its root type.
        result = {}
        for key, value in obj.items():
            if isinstance(value, (dict, list)) or value is None: continue
            pattern = re.escape(json.dumps(key, ensure_ascii=False)) + r'\s*:\s*("(?:\\.|[^"\\])*"|true|false|null|-?[0-9.eE+]+)'
            for match in re.finditer(pattern, body):
                try:
                    if json.loads(match[1]) != value: continue
                except ValueError:
                    continue
                field = _text(key).replace('_', ' ')
                result[field] = (_text(value), {'field': field, 'pointer': '/' + str(key).replace('~', '~0').replace('/', '~1'),
                    'offset_start': match.start(), 'offset_end': match.end(), 'excerpt': match[0]})
                break
        return result
    # Replacements preserve exact character positions in the original body.
    masked = re.sub(r'<br\s*/?>', lambda m: '\n' + ' ' * (len(m[0]) - 1), body, flags=re.I)
    masked = re.sub(r'<[^>]*>|\'{2,5}|\*{2}|\[\[|\]\]', lambda m: ' ' * len(m[0]), masked)
    result = {}
    for match in FIELDS.finditer(masked):
        key = _text(match[1]).replace('_', ' ')
        result.setdefault(key, (_text(match[2].strip(' ",{}')), {'field': key,
            'offset_start': match.start(), 'offset_end': match.end(), 'excerpt': body[match.start():match.end()]}))
    return result


def infer(record):
    """Return a supported type and literal source evidence, never a fake probability."""
    base = bucket(record['kind']); name = record['name']; body = record.get('body', '')
    allowed = {'craft', 'vehicle'} if base == 'craft' else {'location', 'system', 'stellar_route'} if base == 'location' else set(LABELS) if base == 'unknown' else set()
    if not allowed:
        return None
    fields = _field_spans(body); signals = []
    def offer(kind, rule, evidence, weight, subtype=None):
        if kind in allowed:
            signals.append({'kind': kind, 'subtype': subtype, 'rule': rule, 'evidence': evidence, 'weight': weight})
    def match_text(text, evidence, weight, rule):
        value = _text(re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', str(text))).replace('_', ' ').replace('-', ' ')
        if value in {'vehicle', 'vehicles', 'vehicule', 'vehicules', 'véhicule', 'véhicules'}: offer('vehicle', rule, evidence, weight)
        if value in {'ship', 'ships', 'vaisseau', 'vaisseaux'}: offer('craft', rule, evidence, weight)
        if re.search(ROUTES, value): offer('stellar_route', rule, evidence, weight)
        astronomical = re.search(r'\b(?:planets?|terrestrial|gas giants?|moons?)\b', value)
        if astronomical: offer('system', rule, evidence, weight, 'moon' if astronomical[0].startswith('moon') else 'planet')
        if re.search(r'\b(?:star systems?|stellar systems?|systemes? stellaires?)\b', value): offer('system', rule, evidence, weight, 'star_system')
        if re.search(r'\b(?:droids?|androids?|dro[iï]des?)\b', value): offer('droid', rule, evidence, weight)
        if re.search(GROUND, value) and not re.search(r'\banti[- ]?(?:vehicle|walker)\b', value): offer('vehicle', rule, evidence, weight)
        if re.search(SHIPS, value) and not re.search(r'\b(?:weapons?|pilots?|crew)\b', value): offer('craft', rule, evidence, weight)
        if re.search(WEAPONS, value) and not re.search(r'\b(?:armed|artillery) walkers?\b', value): offer('equipment', rule, evidence, weight)
        if re.search(r'\b(?:characters?|npcs?|people|personnages?|soldiers?|assassins?|smugglers?|technicians?|crew members)\b', value): offer('person', rule, evidence, weight)
        if re.search(r'\b(?:species|races?|esp[eè]ces?)\b', value): offer('species', rule, evidence, weight)
        if re.search(r'\b(?:creatures?|beasts?|predators?|animals?)\b', value): offer('creature', rule, evidence, weight)
        if re.search(r'\b(?:city|cities|buildings?|cantinas?|restaurants?|spaceports?|settlements?|locations?|sectors?)\b', value): offer('location', rule, evidence, weight)
        if re.search(r'\b(?:organizations?|factions?|corporations?)\b', value): offer('organization', rule, evidence, weight)
        if re.search(r'\b(?:force powers?)\b', value): offer('force_power', rule, evidence, weight)
    if re.match(r'^(?:Category|Template|Help|Talk|User|Forum)\s*:', name, re.I):
        offer('document', 'wiki_metadata_title', {'field': 'name', 'excerpt': name}, 180)
    template = re.search(r'\{\{\s*(?:Infobox[ _]+)?(Planet|Moon|Star[ _]?system|System|Hyperspace[ _]?route|Hyperlane|Vehicle|Starship|Ship|Weapon|Droid|Person|Character|Organization|Species|Creature|Location|Year)\s*[|\n]', body, re.I)
    if template:
        mapping = {'planet': ('system', 'planet'), 'moon': ('system', 'moon'), 'star system': ('system', 'star_system'),
            'hyperspace route': ('stellar_route', None), 'hyperlane': ('stellar_route', None), 'vehicle': ('vehicle', None),
            'starship': ('craft', None), 'ship': ('craft', None), 'weapon': ('equipment', None), 'droid': ('droid', None),
            'person': ('person', None), 'character': ('person', None), 'organization': ('organization', None),
            'species': ('species', None), 'creature': ('creature', None), 'location': ('location', None), 'year': ('document', 'chronology')}
        template_name = _text(template[1].replace('_', ' '))
        template_name = {'starsystem': 'star system', 'system': 'star system', 'hyperspaceroute': 'hyperspace route'}.get(template_name, template_name)
        kind, subtype = mapping[template_name]
        offer(kind, 'wiki_infobox', {'field': 'body', 'offset_start': template.start(), 'offset_end': template.end(), 'excerpt': template[0]}, 150, subtype)
    scope = record.get('scope') or {}
    categories = scope.get('categories') or []
    if isinstance(categories, str): categories = [categories]
    for i, category in enumerate(categories):
        match_text(category, {'field': 'scope', 'pointer': f'/categories/{i}', 'excerpt': str(category)}, 100, 'source_category')
    for category in re.finditer(r'\[\[Category:([^\]|]+)', body, re.I):
        match_text(category[1], {'field': 'body', 'offset_start': category.start(), 'offset_end': category.end(), 'excerpt': category[0]}, 100, 'wiki_category')
    for key in ('category', 'cat', 'section of site', 'book'):
        if key in fields: match_text(fields[key][0], fields[key][1], 100, 'category_field')
    for key in ('type', 'class', 'craft type', 'vehicle type', 'vehicle class', 'ship class'):
        if key in fields: match_text(fields[key][0], fields[key][1], 130, 'type_field')
    subject = re.sub(r'/(?:Legends|Canon)$', '', name, flags=re.I)
    masked = re.sub(r'<[^>]*>|\[\[|\]\]|\'{2,5}', lambda m: ' ' * len(m[0]), body[:3500])
    pattern = re.escape(subject).replace(r'\ ', r'\s+') + r'\s+(?:is|was|are|were)\s+([^\n.!?]{5,400})'
    definition = re.search(pattern, masked, re.I)
    if definition:
        end = definition.start(1) + len(re.split(r'\b(?:that|which|where|in|linking|connecting)\b', definition[1], maxsplit=1, flags=re.I)[0])
        match_text(masked[definition.start(1):end], {'field': 'body', 'offset_start': definition.start(),
            'offset_end': end, 'excerpt': body[definition.start():end]}, 125, 'article_subject_definition')
    scale = fields.get('scale', ('', {}))[0]
    skill = fields.get('skill', ('', {}))[0]
    if re.search(r'\b(?:repulsorlift operations?|walker operations?|ground vehicle operations?|hover vehicle operations?)\b', skill):
        if not re.search(r'\b(?:starfighter|capital ship)\b', scale): offer('vehicle', 'vehicle_operation_skill', fields['skill'][1], 140)
    if re.search(r'\b(?:starfighter piloting|space transports?|capital ship piloting)\b', skill):
        offer('craft', 'space_piloting_skill', fields['skill'][1], 140)
    if {'crew', 'hull'} <= fields.keys() and 'starfighter' in scale:
        offer('craft', 'starfighter_vehicle_profile', fields['scale'][1], 120)
    if {'crew', 'hull'} <= fields.keys() and scale in {'speeder', 'walker'}:
        offer('vehicle', 'surface_vehicle_profile', fields['scale'][1], 120)
    if len({'atmosphere', 'gravity', 'hydrosphere', 'terrain', 'temperature'} & fields.keys()) >= 3:
        key = next(k for k in ('atmosphere', 'gravity', 'hydrosphere') if k in fields)
        offer('system', 'planetary_profile_fields', fields[key][1], 120, 'planet')
    if {'grid', 'region', 'col', 'row'} <= fields.keys() and base == 'location':
        offer('system', 'galactic_map_record', fields['grid'][1], 120, 'atlas_entry')
    if 'species' in fields and ({'gender', 'age', 'role'} & fields.keys()) and not re.search(r'\b(?:droid|android)\b', fields['species'][0]):
        offer('person', 'individual_profile_fields', fields['species'][1], 120)
    # Name hints are lower priority than explicit source taxonomy/stat blocks.
    match_text(name, {'field': 'name', 'excerpt': name}, 80, 'explicit_name_noun')
    if re.search(r'attribute\s*dice', body, re.I) and len(re.findall(r'\b(?:DEXTERITY|KNOWLEDGE|MECHANICAL|PERCEPTION|STRENGTH|TECHNICAL)\b', body)) >= 3:
        match = re.search(r'attribute\s*dice[^\n<]{0,60}', body, re.I)
        offer('species', 'species_attribute_template', {'field': 'body', 'offset_start': match.start(), 'offset_end': match.end(), 'excerpt': match[0]}, 120)
    if re.match(r'^(?:Dr\.?|Doctor|Moff|Admiral|Captain|Commander|Senator)\s+\S', name, re.I):
        offer('person', 'person_title', {'field': 'name', 'excerpt': name}, 75)
    if not signals:
        return None
    signals.sort(key=lambda s: (-s['weight'], s['kind']))
    best = signals[0]
    conflict = [s for s in signals if s['kind'] != best['kind'] and best['weight'] - s['weight'] < 15]
    if conflict:
        return {'kind': base, 'subtype': None, 'rule': 'ambiguous_evidence', 'assigned': False, 'signals': signals[:8]}
    return {**best, 'assigned': True, 'signals': signals[:8]}


def source_fingerprint(main):
    result = hashlib.sha256()
    for sql in ('SELECT id,current_version,entity_id,active FROM records ORDER BY id',
                'SELECT id,sha256,kind FROM record_versions ORDER BY id',
                'SELECT id,key,name,kind,universe,anchor_record_id,warnings_json,run_id FROM aggregates ORDER BY id',
                'SELECT record_id,aggregate_id,version_id,match_key,rule FROM aggregate_members ORDER BY record_id',
                'SELECT * FROM editorial ORDER BY entity_id'):
        for row in main.db.execute(sql): result.update(dumps(tuple(row)).encode())
    return result.hexdigest()


def classify(main):
    records = {}; per_aggregate = defaultdict(list); unresolved = []; rules = Counter()
    for row in main.db.execute('SELECT r.id,r.current_version,r.source_id,v.name,v.kind,substr(v.body,1,18000) body,'
            'v.scope_json,v.locator_json,m.aggregate_id,a.kind aggregate_kind FROM records r '
            'JOIN record_versions v ON v.id=r.current_version JOIN aggregate_members m ON m.record_id=r.id '
            'JOIN aggregates a ON a.id=m.aggregate_id WHERE r.active=1'):
        base = bucket(row['kind'])
        if base not in {'unknown', 'craft', 'location'}: continue
        record = dict(row); record['scope'] = json.loads(record.pop('scope_json'))
        decision = infer(record)
        if decision and decision['assigned']:
            proof = {'record_id': row['id'], 'version_id': row['current_version'], 'source_id': row['source_id'],
                     'locator': json.loads(row['locator_json']), 'evidence': decision['evidence']}
            value = {'record_id': row['id'], 'version_id': row['current_version'], 'kind': decision['kind'],
                'subtype': decision['subtype'], 'rule': decision['rule'], 'proof': proof}
            records[row['id']] = value; per_aggregate[row['aggregate_id']].append(value); rules[decision['rule']] += 1
        elif base == 'unknown':
            unresolved.append({'record_id': row['id'], 'name': row['name'], 'reason': decision['rule'] if decision else 'no_explicit_evidence'})
    aggregates = {}
    for row in main.db.execute('SELECT a.id,a.kind,a.run_id,r.current_version FROM aggregates a JOIN records r ON r.id=a.anchor_record_id'):
        choices = per_aggregate.get(row['id'], [])
        if row['kind'] not in {'unknown', 'craft', 'location'}: continue
        allowed = {'vehicle', 'craft'} if row['kind'] == 'craft' else {'system', 'stellar_route', 'location'} if row['kind'] == 'location' else set(LABELS)
        choices = [v for v in choices if v['kind'] in allowed]
        kinds = {v['kind'] for v in choices}
        # Different source classifications are a review case, not a majority vote.
        if len(kinds) > 1:
            if kinds <= {'location', 'system'}: choices = [v for v in choices if v['kind'] == 'system']
            elif kinds <= {'location', 'stellar_route'}: choices = [v for v in choices if v['kind'] == 'stellar_route']
            else: continue
        if not choices: continue
        chosen = next((v for v in choices if v['record_id'] == main.db.execute('SELECT anchor_record_id FROM aggregates WHERE id=?', (row['id'],)).fetchone()[0]), choices[0])
        subtypes = {v['subtype'] for v in choices if v['subtype']}
        aggregates[row['id']] = {'kind': chosen['kind'], 'subtype': next(iter(subtypes)) if len(subtypes) == 1 else None, 'rule': 'consistent_source_types',
            'cleaning_run': row['run_id'], 'anchor_version': row['current_version'], 'evidence': [v['proof'] for v in choices]}
    return records, aggregates, unresolved, rules


def run(main, apply=False):
    before = source_fingerprint(main)
    records, aggregates, unresolved, rules = classify(main)
    original = {r['id']: r['kind'] for r in main.db.execute('SELECT id,kind FROM aggregates')}
    counts = Counter(aggregates[aid]['kind'] if aid in aggregates else kind for aid, kind in original.items())
    report = {'policy': VERSION, 'model_calls': 0, 'applied': apply, 'source_data_hash_before': before,
        'aggregate_types_before': dict(Counter(original.values())), 'aggregate_types_after': dict(counts),
        'record_assignments': len(records), 'aggregate_assignments': len(aggregates), 'rules': dict(rules),
        'unknown_records_remaining': len(unresolved), 'unresolved_examples': unresolved[:20]}
    if apply:
        run_id = 'type-mining-' + str(uuid.uuid4()); writer = sqlite3.connect(main.path); writer.row_factory = sqlite3.Row
        try:
            writer.execute('PRAGMA foreign_keys=ON'); writer.executescript(DDL)
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('INSERT INTO type_mining_runs VALUES(?,?,?,?)', (run_id, VERSION, datetime.now(timezone.utc).isoformat(), dumps(report)))
            for table, key, values in (('mined_record_types', 'record_id', records), ('mined_aggregate_types', 'aggregate_id', aggregates)):
                for old in writer.execute('SELECT * FROM ' + table).fetchall():
                    if old[key] not in values:
                        writer.execute('INSERT INTO type_mining_history VALUES(?,?,?,?,?)',
                            (run_id, old[key], key, dumps(dict(old)), dumps({'status': 'no_current_assignment'})))
                        writer.execute('DELETE FROM ' + table + ' WHERE ' + key + '=?', (old[key],))
                for identity, value in values.items():
                    old = writer.execute('SELECT * FROM ' + table + ' WHERE ' + key + '=?', (identity,)).fetchone()
                    columns = ('record_id,version_id,kind,subtype,rule,evidence_json,run_id' if key == 'record_id' else
                               'aggregate_id,cleaning_run,anchor_version,kind,subtype,rule,evidence_json,run_id')
                    args = ((identity, value['version_id'], value['kind'], value['subtype'], value['rule'], dumps(value['proof']), run_id) if key == 'record_id' else
                            (identity, value['cleaning_run'], value['anchor_version'], value['kind'], value['subtype'], value['rule'], dumps(value['evidence']), run_id))
                    writer.execute('INSERT OR REPLACE INTO ' + table + '(' + columns + ') VALUES(' + ','.join('?' for _ in args) + ')', args)
                    writer.execute('INSERT INTO type_mining_history VALUES(?,?,?,?,?)', (run_id, identity, key, dumps(dict(old)) if old else None, dumps(value)))
            writer.commit(); report['run_id'] = run_id
        except Exception:
            writer.rollback(); raise
        finally:
            writer.close()
    report['source_data_hash_after'] = source_fingerprint(main)
    if report['source_data_hash_after'] != before:
        raise ValueError('Source data changed during type mining')
    return report
