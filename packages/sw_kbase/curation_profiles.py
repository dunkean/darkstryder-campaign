"""Conservative comparison views, never replacements for imported profiles.

Unknown fields remain significant. Only known provenance/presentation fields are
removed; mechanics inside skills, equipment and abilities retain their hierarchy.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .normalize import FIELDS, dice


def _key(value):
    value = unicodedata.normalize('NFKD', str(value).casefold())
    return ''.join(c for c in value if c.isalnum() and not unicodedata.combining(c))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


PROVENANCE = {
    'url', 'uri', 'href', 'link', 'links', 'sourceurl', 'pageurl', 'imageurl',
    'source', 'sources', 'datasource', 'bibliography', 'citation', 'citations',
    'book', 'page', 'pages', 'pagenumber', 'sourceid', 'recordid', 'documentid',
    'entryindex', 'sheetindex', 'duplicates', 'importedat', 'createdat', 'updatedat',
    'timestamp', 'filename', 'filepath', 'sourcepath', 'locator', 'provenance',
    'image', 'images', 'thumbnail', 'illustration', 'author', 'copyright',
}
PRESENTATION = {'html', 'rawhtml', 'renderedhtml', 'pagehtml', 'documenthtml'}
NAMES = {'name', 'title', 'label', 'displayname'}
PROSE = {'description', 'desc', 'background', 'biography', 'appearance', 'summary', 'flavortext'}
MECHANICAL_CONTAINERS = {
    'skills', 'specializations', 'specialisations', 'weapons', 'weapon', 'equipment',
    'loadout', 'loadouts', 'abilities', 'specialabilities', 'specialability',
    'special', 'traits', 'advantages', 'disadvantages', 'features', 'attacks',
    'armor', 'armour', 'powers', 'forcepowers', 'systems', 'modifications',
}
MECHANICAL_TEXT = re.compile(
    r'\d\s*[dD](?:\s*[+−-]\s*\d)?|[+−-]\s*\d|\b\d+(?:[.,]\d+)?\s*(?:m|km|kg|tons?|credits?|%)\b|'
    r'\b(?:damage|dice|bonus|penalt(?:y|ies)|armor|armour|attack|defen[cs]e|resistan\w*|'
    r'difficulty|skill|attribute|roll|round|action|range|speed|cost|'
    r'degats|dégâts|blessure|malus|jet|competence|compétence|difficulte|difficulté)\b', re.I)


def _merge(mapping, key, value):
    if key not in mapping:
        mapping[key] = value
    elif mapping[key] != value:
        prior = mapping[key]
        values = prior['$conflicting_values'] if isinstance(prior, dict) and set(prior) == {'$conflicting_values'} else [prior]
        mapping[key] = {'$conflicting_values': sorted({ _json(v): v for v in [*values, value] }.values(), key=_json)}


def normalize_profile(value):
    """Return a stable, conservative mechanical view of a JSON profile/payload.

    A malformed JSON string remains explicit evidence. Array order and unknown
    values are preserved. Known dice notation loses formatting only, not pips.
    Root names describe the already identified subject; nested equipment names
    and otherwise unknown labels can distinguish loadouts and remain significant.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {'unparsed_profile': value}

    def walk(item, depth=0, mechanical=False):
        if isinstance(item, dict):
            result = {}
            for original, child in item.items():
                key = _key(original)
                if key in PROVENANCE:
                    continue
                if key == 'cover' and isinstance(child, str) and re.match(r'^(?:https?://|data:image/)', child, re.I):
                    continue
                if key in PRESENTATION:
                    # Nested ability text can be mechanical HTML, unlike the
                    # duplicated top-level rendered page supplied with imports.
                    if not mechanical:
                        continue
                    child = re.sub('<[^>]*>', ' ', str(child))
                    key = 'text'
                if depth == 0 and key in {'id', 'uuid', 'guid', *NAMES}:
                    continue
                if key in PROSE and not mechanical and isinstance(child, str) and not MECHANICAL_TEXT.search(child):
                    continue
                nested_mechanics = mechanical or key in MECHANICAL_CONTAINERS
                canonical = (original if original in {*FIELDS.values(), '$conflicting_values', 'unparsed_profile'}
                             else FIELDS.get(key, key or str(original)))
                _merge(result, canonical, walk(child, depth + 1, nested_mechanics))
            return dict(sorted(result.items()))
        if isinstance(item, list):
            return [walk(child, depth + 1, mechanical) for child in item]
        if isinstance(item, str):
            parsed = dice(item)
            if parsed:
                return {'dice': parsed['dice'], 'pips': parsed['pips']}
            return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', item)).strip()
        return item

    normalized = walk(value)
    return normalized if isinstance(normalized, (dict, list)) else {'value': normalized}


def profile_signature(value):
    """Hash the normalized view; no source path or raw spelling enters the hash."""
    return hashlib.sha256(_json(normalize_profile(value)).encode('utf-8')).hexdigest()


SCOPE_KEYS = {'edition': 'edition', 'rulesedition': 'edition', 'gameedition': 'edition',
              'epoch': 'epoch', 'era': 'epoch', 'period': 'epoch',
              'continuity': 'continuity', 'canon': 'continuity',
              'loadout': 'loadout', 'equipmentconfiguration': 'loadout', 'configuration': 'loadout'}


def _scope(scope):
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except json.JSONDecodeError:
            return {'unknown_scope': scope}
    result = {}
    for original, value in (scope or {}).items():
        key = SCOPE_KEYS.get(_key(original))
        if key is None or value is None or value == '' or value == [] or value == {}:
            continue
        def canonical(item):
            if isinstance(item, str):
                return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', item)).strip().casefold()
            if isinstance(item, list):
                return [canonical(v) for v in item]
            if isinstance(item, dict):
                normalized = {}
                for k, v in item.items():
                    _merge(normalized, _key(k), canonical(v))
                return normalized
            return item
        _merge(result, key, canonical(value))
    return result


def preserve_reasons(source_scope, retained_scopes):
    """Protect new scope values AND new combinations using existing policy labels.

    Unknown/missing retained values never prove equivalence with a known source.
    A source with less scope information can match a more specifically scoped
    retained document; no missing value is invented.
    """
    source = _scope(source_scope)
    retained = [_scope(value) for value in retained_scopes]
    if not source or any(all(other.get(key) == value for key, value in source.items()) for other in retained):
        return []
    reasons = []
    if 'edition' in source and all(other.get('edition') != source['edition'] for other in retained):
        reasons.append('new_edition')
    if 'epoch' in source and all(other.get('epoch') != source['epoch'] for other in retained):
        reasons.append('new_epoch')
    if not reasons or any(key in source and all(other.get(key) != source[key] for other in retained)
                          for key in ('continuity', 'loadout', 'unknown_scope')):
        reasons.append('distinct_profile')
    return reasons
