"""Versioned, provider-neutral structured article contract and local validation."""
import html
import json
import math
import re
from pathlib import Path

VERSION = 'sw-rich-article/2'
KINDS = ['person', 'droid', 'creature', 'species', 'archetype', 'organization', 'craft', 'vehicle',
         'equipment', 'location', 'system', 'stellar_route', 'rule', 'skill', 'force_power',
         'scenario', 'event', 'concept', 'document', 'unknown']


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def array(items):
    return {'type': 'array', 'items': items}


def choice(values):
    return {'type': 'string', 'enum': values}


def nullable(value):
    return {'anyOf': [value, {'type': 'null'}]}


def article_schema():
    text = {'type': 'string'}
    refs = {'type': 'array', 'items': text, 'minItems': 1}
    paragraph = obj(text=text, evidence_ids=refs)
    # Flat normalized entries cover arbitrary SW D6 profiles without opaque JSON strings.
    stat = obj(key=text, label=text, group=text, value_text=text, unit=nullable(text),
        number=nullable({'type': 'number'}), dice=nullable({'type': 'integer'}),
        pips=nullable({'type': 'integer'}), condition=nullable(text), evidence_ids=refs)
    common = dict(id=text, title=text, scope_ids=array(text), paragraphs=array(paragraph),
                  stat_block_ids=array(text), rule_block_ids=array(text))
    section = obj(**common, subsections=array(obj(**common)))
    classification = obj(kind=choice(KINDS), subtype=text,
        level=choice(['individual', 'model', 'archetype', 'concept', 'unknown']),
        confidence=choice(['high', 'medium', 'low']), rationale=text, evidence_ids=refs,
        proposed_category=nullable(obj(key=text, label=text, description=text, parent_kind=choice(KINDS))))
    profile = obj(id=text, title=text, profile_key=text, system=text, edition=nullable(text),
        era=nullable(text), variant=nullable(text), scale=nullable(text), scope_ids=array(text),
        entries=array(stat), notes=array(paragraph), evidence_ids=refs)
    rule = obj(id=text, title=text, system=text, edition=nullable(text), scope_ids=array(text),
        conditions=array(paragraph), procedure=array(paragraph), difficulties=array(paragraph),
        effects=array(paragraph), exceptions=array(paragraph), evidence_ids=refs)
    conflict = obj(id=text, subject=text, alternatives=array(paragraph), evidence_ids=refs)
    schema = obj(schema_version={'const': VERSION}, title=text, classification=classification,
        sections=array(section), stat_blocks=array(profile), rule_blocks=array(rule),
        conflicts=array(conflict), notes=array(paragraph))
    schema.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'SW D6 structured article'})
    return schema


PROMPT = (Path(__file__).resolve().parents[2] / 'config/sw-article-extraction.prompt.txt').read_text(encoding='utf-8').strip()


def _validate_shape(value, schema, path='$'):
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                _validate_shape(value, candidate, path); return
            except ValueError:
                pass
        raise ValueError('Invalid nullable value at ' + path)
    if 'const' in schema and value != schema['const']:
        raise ValueError('Invalid constant at ' + path)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Invalid category at ' + path)
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str),
             'integer': type(value) is int, 'number': type(value) in (int, float), 'null': value is None}.get(kind, True)
    if not valid or (type(value) is float and not math.isfinite(value)):
        raise ValueError('Invalid type at ' + path)
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError('Missing/extra fields at ' + path)
        for key, item in value.items():
            _validate_shape(item, schema['properties'][key], path + '.' + key)
    if kind == 'array':
        if len(value) < schema.get('minItems', 0):
            raise ValueError('Empty citations at ' + path)
        for i, item in enumerate(value):
            _validate_shape(item, schema['items'], path + '[' + str(i) + ']')


def validate_article(value, context):
    _validate_shape(value, article_schema())
    if not value['title'].strip(): raise ValueError('Empty article title')
    evidence = {e['id']: e for e in context['evidence']}
    used = set(); ids = set()
    def walk(node):
        if isinstance(node, list):
            for item in node: walk(item)
        elif isinstance(node, dict):
            if 'id' in node:
                if not node['id'].strip() or node['id'] in ids: raise ValueError('Empty/duplicate local ID')
                ids.add(node['id'])
            if 'evidence_ids' in node:
                refs = node['evidence_ids']
                if len(set(refs)) != len(refs) or not set(refs) <= evidence.keys():
                    raise ValueError('Unknown/duplicate evidence citations')
                # Classification citations do not satisfy content coverage.
                if node is not value['classification']: used.update(refs)
                if 'scope_ids' in node and set(node['scope_ids']) != {evidence[e]['scope_id'] for e in refs}:
                    raise ValueError('Technical block scope/citations mismatch')
            for item in node.values(): walk(item)
    walk(value)
    if used != evidence.keys(): raise ValueError('Selected evidence omitted from structured content')
    stats = {b['id'] for b in value['stat_blocks']}; rules = {b['id'] for b in value['rule_blocks']}
    placed_stats = set(); placed_rules = set()
    scopes = {e['scope_id'] for e in context['evidence']}
    for section in value['sections']:
        for part in [section, *section['subsections']]:
            if not set(part['scope_ids']) <= scopes: raise ValueError('Unknown section scope')
            if not set(part['stat_block_ids']) <= stats or not set(part['rule_block_ids']) <= rules:
                raise ValueError('Broken article block reference')
            placed_stats.update(part['stat_block_ids']); placed_rules.update(part['rule_block_ids'])
            for paragraph in part['paragraphs']:
                if not {evidence[e]['scope_id'] for e in paragraph['evidence_ids']} <= set(part['scope_ids']):
                    raise ValueError('Paragraph mixes unlisted scopes')
    if stats != placed_stats or rules != placed_rules: raise ValueError('Technical block missing from article')
    for block in [*value['stat_blocks'], *value['rule_blocks']]:
        if len(block['scope_ids']) != 1: raise ValueError('Mixed scopes in one technical profile/rule')
        def check_scope(node):
            if isinstance(node, list):
                for item in node: check_scope(item)
            elif isinstance(node, dict):
                if 'evidence_ids' in node and not {evidence[e]['scope_id'] for e in node['evidence_ids']} <= set(block['scope_ids']):
                    raise ValueError('Technical entry mixes scopes')
                for item in node.values(): check_scope(item)
        check_scope(block)
    for block in value['stat_blocks']:
        for entry in block['entries']:
            if not re.fullmatch('[a-z][a-z0-9_]*', entry['key']): raise ValueError('Invalid stat key')
            if entry['dice'] is not None:
                match = re.fullmatch(r'\s*(\d+)[dD](?:\s*\+\s*([012]))?\s*', entry['value_text'])
                if not match or entry['dice'] != int(match[1]) or entry['pips'] != int(match[2] or 0):
                    raise ValueError('Lossy dice normalization')
            elif entry['pips'] is not None: raise ValueError('Pips without dice')
    proposed = value['classification']['proposed_category']
    if proposed and not re.fullmatch('[a-z][a-z0-9_]*', proposed['key']): raise ValueError('Invalid proposed category key')
    json.dumps(value, allow_nan=False)
    return value


def article_html(article):
    """Escaped static review artifact. Object relations belong to SQLite."""
    esc = lambda value: html.escape(str(value), quote=True)
    def paragraph(p):
        return '<p>' + esc(p['text']) + '</p><small>' + esc(', '.join(p['evidence_ids'])) + '</small>'
    stats = {b['id']: b for b in article['stat_blocks']}; rules = {b['id']: b for b in article['rule_blocks']}
    out = ['<h1>' + esc(article['title']) + '</h1><p>' + esc(article['classification']['kind']) +
           ' · ' + esc(article['classification']['level']) + ' · non relu</p>']
    for section in article['sections']:
        for level, part in [(2, section), *[(3, s) for s in section['subsections']]]:
            out.append(f'<h{level}>' + esc(part['title']) + f'</h{level}>')
            out.extend(paragraph(p) for p in part['paragraphs'])
            for sid in part['stat_block_ids']:
                block = stats[sid]
                out.append('<aside class="stats"><h3>' + esc(block['title']) + '</h3><table>')
                for entry in block['entries']:
                    out.append('<tr><th>' + esc(entry['group'] + ' · ' + entry['label']) + '</th><td>' + esc(entry['value_text']) + '</td></tr>')
                out.append('</table>' + ''.join(paragraph(p) for p in block['notes']) + '</aside>')
            for rid in part['rule_block_ids']:
                block = rules[rid]; out.append('<aside class="rules"><h3>' + esc(block['title']) + '</h3>')
                for key in ['conditions', 'procedure', 'difficulties', 'effects', 'exceptions']:
                    if block[key]: out.append('<h4>' + esc(key) + '</h4>' + ''.join(paragraph(p) for p in block[key]))
                out.append('</aside>')
    if article['conflicts']:
        out.append('<h2>Contradictions</h2>')
        for conflict in article['conflicts']:
            out.append('<h3>' + esc(conflict['subject']) + '</h3>' + ''.join(paragraph(p) for p in conflict['alternatives']))
    if article['notes']: out.append('<h2>Notes</h2>' + ''.join(paragraph(p) for p in article['notes']))
    return ''.join(out)
