"""Minimal model output. IDs, provenance and object relations belong to the engine."""
import html
import json
from pathlib import Path

VERSION = 'sw-rich-article/3-simple-sections'
KINDS = ['person', 'droid', 'creature', 'species', 'archetype', 'organization', 'craft', 'vehicle',
         'equipment', 'location', 'system', 'stellar_route', 'rule', 'skill', 'force_power',
         'scenario', 'event', 'concept', 'document', 'unknown']
SECTION_TYPES = ['description', 'rule', 'stats', 'scenario', 'gm_note', 'conflict', 'note']


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def article_schema():
    text = {'type': 'string'}
    section = obj(title=text, subtitle=text, content=text, type=text,
                  stats={'type': 'object', 'additionalProperties': {'type': 'string'}})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'SW D6 simple article',
            **obj(type=text, subtype=text, sections={'type': 'array', 'minItems': 1, 'items': section})}


PROMPT = (Path(__file__).resolve().parents[2] / 'config/sw-article-extraction.prompt.txt').read_text(encoding='utf-8').strip()


def validate_article(value, context=None):
    """Shape only: no fictitious confidence, citation coverage or stat normalization."""
    if not isinstance(value, dict) or set(value) != {'type', 'subtype', 'sections'}:
        raise ValueError('Expected only type, subtype and sections')
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ('type', 'subtype')):
        raise ValueError('Article type/subtype must be nonempty strings')
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


def article_html(article, title=''):
    """Rendering order follows sections; contradictions have the same shape."""
    esc = lambda value: html.escape(str(value), quote=True)
    out = ['<h1>' + esc(title or article['type']) + '</h1><p>' + esc(article['type']) +
           ' · ' + esc(article['subtype']) + ' · non relu</p>']
    for section in article['sections']:
        kind = section['type']
        framed = kind in {'stats', 'rule', 'conflict'}
        if framed:
            out.append('<aside class="' + {'stats': 'stats', 'rule': 'rules', 'conflict': 'conflict'}[kind] + '">')
        out.append('<h2>' + esc(section['title']) + '</h2>')
        if section['subtitle']: out.append('<h3>' + esc(section['subtitle']) + '</h3>')
        for paragraph in section['content'].split('\n\n'):
            if paragraph.strip(): out.append('<p>' + esc(paragraph).replace('\n', '<br>') + '</p>')
        if section['stats']:
            out.append('<table>')
            for key, val in section['stats'].items():
                out.append('<tr><th>' + esc(key) + '</th><td>' + esc(val) + '</td></tr>')
            out.append('</table>')
        if framed: out.append('</aside>')
    return ''.join(out)
