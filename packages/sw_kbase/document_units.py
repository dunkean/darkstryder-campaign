"""Format-aware, lossless evidence spans and deterministic reference ranking."""
from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
import math
import re

VERSION = 'document-units/2'
ORIGIN_PRIORITIES = {'transcribed_reference': 12, 'phase3_extraction': 10,
                     'statbase_compilation': 6, 'fan_compilation': 3, 'personal_adaptation': 0}


def detect_format(body, hint=''):
    if hint in {'json', 'markdown', 'html', 'text'}:
        return hint
    try:
        if isinstance(json.loads(body), (dict, list)):
            return 'json'
    except (ValueError, TypeError):
        pass
    if re.search(r'<(?:html|body|p|div|table|article|section)\b', body, re.I):
        return 'html'
    if re.search(r'(?m)^\s{0,3}(?:#{1,6}\s|```|~~~|[-*+]\s|\|)', body):
        return 'markdown'
    return 'text'


def _json_spans(body):
    """Keep exact member text, including its key; JSON pointers carry ancestors."""
    decoder = json.JSONDecoder()
    def white(pos):
        while pos < len(body) and body[pos].isspace():
            pos += 1
        return pos
    def walk(start, end, pointer):
        pos = white(start)
        if body[pos:pos+1] not in ('{', '['):
            yield start, end, pointer, 'json_value'
            return
        object_ = body[pos] == '{'; pos = white(pos + 1); index = 0
        while pos < end and body[pos] not in '}]':
            member_start = pos
            if object_:
                key, pos = decoder.raw_decode(body, pos)
                pos = white(pos)
                if body[pos] != ':':
                    raise ValueError('Invalid JSON member')
                pos = white(pos + 1)
                path = pointer + '/' + str(key).replace('~', '~0').replace('/', '~1')
            else:
                path = pointer + '/' + str(index)
            value_start = pos
            _, value_end = decoder.raw_decode(body, pos)
            if value_end - member_start > 1800 and body[value_start:value_start+1] in ('{', '['):
                yield from walk(value_start, value_end, path)
            else:
                yield member_start, value_end, path, 'json_member' if object_ else 'json_item'
            pos = white(value_end)
            if pos < end and body[pos] == ',':
                pos = white(pos + 1)
            index += 1
    yield from walk(0, len(body), '')


def _line_spans(body, markdown):
    start = 0; heading = []; fenced = None
    for match in re.finditer(r'.*(?:\n|$)', body):
        if match.start() == match.end():
            continue
        line = match.group(); stripped = line.strip()
        fence = re.match(r'^\s{0,3}(`{3,}|~{3,})', line) if markdown else None
        if fence:
            mark = fence.group(1)
            if fenced is None:
                if match.start() > start:
                    yield start, match.start(), '/'.join(heading), 'prose'
                start = match.start(); fenced = mark
            elif mark[0] == fenced[0] and len(mark) >= len(fenced):
                yield start, match.end(), '/'.join(heading), 'code'
                start = match.end(); fenced = None
            continue
        if fenced:
            continue
        title = re.match(r'^\s{0,3}(#{1,6})\s+(.*)', line) if markdown else None
        boundary = not stripped or title or (markdown and re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s|\|)', line))
        if boundary and match.start() > start:
            yield start, match.start(), '/'.join(heading), 'prose'
            start = match.start()
        if title:
            level = len(title.group(1)); heading = heading[:level-1] + [title.group(2).strip()]
            yield start, match.end(), '/'.join(heading), 'heading'; start = match.end()
        elif not stripped:
            start = match.end()
    if start < len(body):
        yield start, len(body), '/'.join(heading), 'code' if fenced else 'prose'


def split_document(body, hint=''):
    """Paragraphs retain exact offsets. Long paragraphs are split later for Jev."""
    format_ = detect_format(body, hint)
    if not body.strip():
        return []
    if format_ == 'json':
        try:
            spans = list(_json_spans(body))
        except (ValueError, IndexError):
            spans = [(0, len(body), '', 'unparsed_json')]
        if not spans:
            spans = [(0, len(body), '', 'json_empty_container')]
    elif format_ == 'html':
        boundaries = [0] + [m.start() for m in re.finditer(r'<(?:p|li|h[1-6]|tr|div|article|section)\b[^>]*>|<br\s*/?>', body, re.I)] + [len(body)]
        boundaries = sorted(set(boundaries))
        spans = [(a, b, '', 'html_block') for a, b in zip(boundaries, boundaries[1:])]
    else:
        spans = list(_line_spans(body, format_ == 'markdown'))
    output = []
    for start, end, path, kind in spans:
        raw = body[start:end]; a = start + len(raw) - len(raw.lstrip()); b = end - (len(raw) - len(raw.rstrip()))
        if a >= b:
            continue
        if format_ == 'html' and not html.unescape(re.sub(r'<!--.*?-->|<[^>]*>', ' ', body[a:b], flags=re.S)).strip():
            continue
        number = len(output) + 1
        output.append({'number': number, 'label': f'P{number:05d}', 'offset_start': a, 'offset_end': b,
                       'body': body[a:b], 'format': format_, 'path': path, 'kind': kind,
                       'sha256': hashlib.sha256(body[a:b].encode()).hexdigest()})
    return output


def fragments(paragraph, maximum=1000):
    """Exact complete partition, with sentence boundaries preferred to hard cuts."""
    text = paragraph['body']; start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if end < len(text):
            candidates = [m.end() + start for m in re.finditer(r'[.!?;]\s+|\n', text[start:end])]
            if candidates and candidates[-1] > start + maximum // 2:
                end = candidates[-1]
        yield {'body': text[start:end], 'offset_start': paragraph['offset_start'] + start,
               'offset_end': paragraph['offset_start'] + end, 'paragraph': paragraph['label'],
               'path': paragraph.get('path', ''), 'format': paragraph.get('format', 'text')}
        start = end


def reference_rank(document, config=None):
    """Explainable utility heuristic, not a source-truth/canon assertion."""
    config = config or {}; body = document['body']; units = split_document(body)
    normalized = [' '.join(p['body'].casefold().split()) for p in units]
    unique = sum(len(t) for t in set(normalized)); total = sum(len(t) for t in normalized) or 1
    words = re.findall(r'[^\W_]+', re.sub(r'<[^>]*>', ' ', body).casefold())
    title_terms = set(re.findall(r'[^\W_]+', document['name'].casefold()))
    profile = document.get('profile', {})
    components = {
        'dataset_priority': float(config.get('source_priorities', {}).get(document.get('dataset', ''), 0)),
        'origin_priority': float(config.get('origin_priorities', {}).get(document.get('origin', ''),
                                 ORIGIN_PRIORITIES.get(document.get('origin', ''), 0))),
        'useful_size': 8 * math.log1p(min(unique, 30000)),
        'lexical_diversity': 12 * len(set(words)) / max(1, len(words)),
        'duplicate_penalty': -25 * (1 - unique / total),
        'structured_profile': min(12, math.log1p(len(json.dumps(profile))) * 2) if profile else 0,
        'provenance': 4 if document.get('locator') else 0,
        'name_alignment': 5 * len(title_terms & set(words)) / max(1, len(title_terms)),
        'markup_penalty': -20 * len(re.findall(r'<[^>]*>', body)) / max(1, len(words)),
        'ocr_penalty': -min(30, body.count('\ufffd') * 2),
    }
    return {'score': round(sum(components.values()), 6), 'components': components, 'units': len(units),
            'useful_characters': unique, 'policy': VERSION}


def choose_reference(documents, config=None):
    ranked = [(document, reference_rank(document, config)) for document in documents]
    if not ranked:
        raise ValueError('No reference candidate')
    term_sets = {d['id']: set(re.findall(r'[^\W_]+', re.sub('<[^>]*>', ' ', d['body']).casefold())) for d in documents}
    frequency = Counter(t for terms in term_sets.values() for t in terms)
    for document, rank in ranked:
        terms = term_sets[document['id']]
        centrality = 10 * sum((frequency[t]-1) / max(1, len(documents)-1) for t in sorted(terms)) / max(1, len(terms))
        rank['components']['corpus_term_coverage'] = centrality
        rank['score'] = round(rank['score'] + centrality, 6)
    ranked.sort(key=lambda pair: (-pair[1]['score'], pair[0]['id']))
    return ranked[0][0]['id'], {document['id']: rank for document, rank in ranked}
