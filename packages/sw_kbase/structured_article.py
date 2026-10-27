"""Minimal model output. IDs, provenance and object relations belong to the engine."""
import html
import json
from pathlib import Path

VERSION = 'sw-rich-article/4-content-and-warnings'
KINDS = ['person', 'droid', 'creature', 'species', 'archetype', 'organization', 'craft', 'vehicle',
         'equipment', 'location', 'system', 'stellar_route', 'rule', 'skill', 'force_power',
         'scenario', 'event', 'concept', 'document']
SECTION_TYPES = ['description', 'rule', 'stats', 'scenario', 'gm_note', 'conflict', 'note']


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def article_schema():
    text = {'type': 'string'}
    content = {'anyOf': [text, {'type': 'object', 'additionalProperties': True}]}
    section = obj(title=text, type=text, content=content)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'SW D6 simple article',
            **obj(type=text, subtype=text, tags={'type': 'array', 'items': text},
                  warnings={'type': 'array', 'items': obj(document=text, reason=text)},
                  sections={'type': 'array', 'items': section})}


PROMPT = (Path(__file__).resolve().parents[2] / 'config/sw-article-extraction.prompt.txt').read_text(encoding='utf-8').strip()


def _validate_legacy(value, context=None):
    """Shape only: no fictitious confidence, citation coverage or stat normalization."""
    if not isinstance(value, dict) or set(value) != {'type', 'subtype', 'sections'}:
        raise ValueError('Expected only type, subtype and sections')
    if not isinstance(value['type'], str) or not value['type'].strip() or not isinstance(value['subtype'], str):
        raise ValueError('Article type must be nonempty and subtype must be a string')
    if not isinstance(value['sections'], list) or not value['sections']:
        raise ValueError('Article needs at least one section')
    for section in value['sections']:
        if not isinstance(section, dict) or set(section) != {'title', 'subtitle', 'content', 'type', 'stats'}:
            raise ValueError('Expected only title, subtitle, content, type and stats in a section')
        if any(not isinstance(section[key], str) for key in ('title', 'subtitle', 'content', 'type')):
            raise ValueError('Section text fields must be strings')
        if not section['title'].strip() or not section['type'].strip():
            raise ValueError('Section title/type must be nonempty')
        stats = section['stats']
        if not isinstance(stats, dict) or any(not isinstance(v, str) for v in stats.values()):
            raise ValueError('Stats must be a free key/string-value object')
        if any(not isinstance(k, str) or not k.strip() for k in stats):
            raise ValueError('Empty stat key')
        if not section['content'].strip() and not stats:
            raise ValueError('Empty section')
    json.dumps(value, allow_nan=False)
    return value


def validate_article(value, context=None):
    # Historical paid responses retain their original contract; never rewrite them.
    if context and set(context.get('output_example', {})) == {'type', 'subtype', 'sections'}:
        return _validate_legacy(value, context)
    if not isinstance(value, dict) or set(value) != {'type', 'subtype', 'tags', 'warnings', 'sections'}:
        raise ValueError('Expected type, subtype, tags, warnings and sections')
    if not all(isinstance(value[k], str) for k in ('type', 'subtype')):
        raise ValueError('Type/subtype must be strings')
    if not isinstance(value['tags'], list) or any(not isinstance(t, str) or not t.strip() for t in value['tags']):
        raise ValueError('Tags must be nonempty strings')
    warnings = value['warnings']
    if not isinstance(warnings, list): raise ValueError('Warnings must be an array')
    labels = {p.get('document') for p in (context or {}).get('texts', []) if p.get('document')}
    for warning in warnings:
        if (not isinstance(warning, dict) or set(warning) != {'document', 'reason'}
                or any(not isinstance(warning[k], str) or not warning[k].strip() for k in warning)):
            raise ValueError('Warning needs a document label and reason')
        if labels and warning['document'] not in labels:
            raise ValueError('Warning refers to an unknown document')
    if any(value[k].strip().casefold() in {'', 'unknown'} for k in ('type', 'subtype')) and not warnings:
        raise ValueError('Empty/unknown classification requires a warning')
    if not isinstance(value['sections'], list) or (not value['sections'] and not warnings):
        raise ValueError('Empty sections require a warning')
    for section in value['sections']:
        if not isinstance(section, dict) or set(section) != {'title', 'content', 'type'}:
            raise ValueError('Expected title, content and type; no subtitle or separate stats field')
        if any(not isinstance(section[k], str) for k in ('title', 'type')):
            raise ValueError('Section labels must be strings')
        if not section['title'].strip() or not section['type'].strip(): raise ValueError('Empty section label')
        content = section['content']
        if not isinstance(content, (str, dict)) or not content: raise ValueError('Content must be text or a key/value object')
        if isinstance(content, dict) and any(not isinstance(k, str) or not k.strip() for k in content):
            raise ValueError('Empty content key')
        if section['type'] == 'stats' and not isinstance(content, dict):
            raise ValueError('Stats belong directly in content as key/value pairs')
    json.dumps(value, allow_nan=False)
    return value


def article_html(article, title=''):
    """Rendering order follows sections; contradictions have the same shape."""
    esc = lambda value: html.escape(str(value), quote=True)
    out = ['<h1>' + esc(title or article['type']) + '</h1><p>' + esc(article['type']) +
           ' · ' + esc(article['subtype']) + ' · non relu</p>']
    if article.get('tags'): out.append('<p>' + esc(' · '.join(article['tags'])) + '</p>')
    for warning in article.get('warnings', []):
        out.append('<aside class="conflict" role="alert"><strong>DOCUMENT À VÉRIFIER</strong><p>' +
                   esc(warning['document']) + '</p><p>' + esc(warning['reason']) + '</p></aside>')
    for section in article['sections']:
        kind = section['type']
        framed = kind in {'stats', 'rule', 'conflict'}
        if framed:
            out.append('<aside class="' + {'stats': 'stats', 'rule': 'rules', 'conflict': 'conflict'}[kind] + '">')
        out.append('<h2>' + esc(section['title']) + '</h2>')
        if section.get('subtitle'): out.append('<h3>' + esc(section['subtitle']) + '</h3>')
        content = section['content']
        for paragraph in (content.split('\n\n') if isinstance(content, str) else []):
            if paragraph.strip(): out.append('<p>' + esc(paragraph).replace('\n', '<br>') + '</p>')
        stats = content if isinstance(content, dict) else section.get('stats', {})
        if stats:
            out.append('<table>')
            for key, val in stats.items():
                if not isinstance(val, str): val = json.dumps(val, ensure_ascii=False)
                out.append('<tr><th>' + esc(key) + '</th><td>' + esc(val) + '</td></tr>')
            out.append('</table>')
        if framed: out.append('</aside>')
    return ''.join(out)
