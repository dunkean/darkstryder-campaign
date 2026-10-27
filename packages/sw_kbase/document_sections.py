"""Coherent numbered passages, independent of model token limits.

Atomic parser spans remain available as provenance, not as inference jobs.
"""
import hashlib
import html
import re

from .document_units import detect_format, split_document

VERSION = 'document-sections/2.2-coherent-blocks'


def _structural_sections(body):
    atoms = split_document(body)
    if not atoms:
        return []
    format_ = detect_format(body)
    groups = []
    if format_ == 'json' and len(body) <= 8000:
        # A normal stat object is one coherent passage, not one vote per field.
        groups = [atoms]
    else:
        pending = []
        for atom in atoms:
            heading = atom['kind'] == 'heading' or (format_ == 'html' and bool(re.match(r'\s*<h[1-6]\b', atom['body'], re.I)))
            if heading:
                if pending and any(p['kind'] != 'heading' and not (format_ == 'html' and re.match(r'\s*<h[1-6]\b', p['body'], re.I)) for p in pending):
                    groups.append(pending); pending = []
                pending.append(atom)
                continue
            if pending:
                previous = pending[-1]
                gap = body[previous['offset_end']:atom['offset_start']]
                is_list = lambda p: bool(re.match(r'\s*(?:[-*+]\s|\d+[.)]\s|\|)', p['body']))
                list_continuation = (format_ == 'markdown' and is_list(previous) and is_list(atom)
                                     and not re.search(r'\n\s*\n', gap))
                html_kind = lambda p: re.match(r'\s*<(li|tr)\b', p['body'], re.I)
                html_continuation = (format_ == 'html' and html_kind(previous) and html_kind(atom)
                                     and html_kind(previous).group(1).casefold() == html_kind(atom).group(1).casefold())
                json_continuation = format_ == 'json' and atom['offset_end'] - pending[0]['offset_start'] <= 2400
                previous_heading = previous['kind'] == 'heading' or (format_ == 'html' and re.match(r'\s*<h[1-6]\b', previous['body'], re.I))
                if previous_heading or list_continuation or html_continuation or json_continuation:
                    pending.append(atom)
                    continue
                groups.append(pending)
            pending = [atom]
        if pending:
            groups.append(pending)
    output = []
    for group in groups:
        start, end = group[0]['offset_start'], group[-1]['offset_end']
        if format_ == 'json' and len(groups) == 1:
            start, end = 0, len(body)  # Preserve the object's braces too.
        text = body[start:end]; number = len(output) + 1
        output.append({'number': number, 'label': f'P{number:05d}',
                       'offset_start': start, 'offset_end': end, 'body': text,
                       'format': format_, 'path': group[-1]['path'], 'kind': 'passage',
                       'sha256': hashlib.sha256(text.encode()).hexdigest(),
                       'parser_spans': [{k: p[k] for k in ('offset_start', 'offset_end', 'path', 'kind')} for p in group]})
    return output


TITLES = {'attributes & skills', 'attributes and skills', 'attributes', 'skills', 'statistics', 'stats',
          'points', 'special abilities', 'equipment', 'weapons', 'sensors', 'description', 'capsule',
          'background', 'history', 'biography', 'personality', 'appearance', 'notes', 'sources',
          'general', 'game statistics', 'physical description', 'combat', 'abilities'}


def _info(section):
    raw = section['body']
    visible = html.unescape(re.sub(r'<[^>]*>', ' ', raw))
    visible = re.sub(r"'{2,3}|\*\*", '', visible).strip()
    lines = [re.sub(r'^\s*[#*|=-]+\s*|\s*[=]+$', '', s).strip() for s in visible.splitlines() if s.strip()]
    first = lines[0] if lines else ''
    heading = bool(re.match(r'\s*(?:#{1,6}\s|={2,6}[^=]|<h[1-6]\b)', raw, re.I)) or first.casefold() in TITLES
    atom_heading_only = all(p['kind'] == 'heading' for p in section['parser_spans'])
    only_heading = atom_heading_only or (len(lines) == 1 and heading)
    # Rendered stat cards often put labels, numbers and skill dice on separate lines.
    # Keep them together even when the scraper inserted blank lines between cells.
    dice = bool(re.search(r'(?i)\b\d+D(?:[+-]\d+)?\b|[a-z]\d+D\b', visible))
    field = bool(re.match(r'^[^.!?\n:]{1,65}:', visible))
    label_only = bool(re.fullmatch(r'[^.!?\n:]{1,100}:\s*',visible))
    image_only = bool(re.fullmatch(r'(?is)\[\[(?:File|Image):.*?\]\]',visible))
    scalar = bool(re.fullmatch(r'[+\-\d.,/ xX%]+(?:\s*(?:km|m|kg|credits|hours|days))?', visible))
    table = bool(re.match(r'\s*(?:[-*+]\s|\||<(?:tr|li)\b)', raw, re.I))
    sentence = bool(re.search(r'[.!?]["\')\]]*\s*$', visible))
    narrative_sentence = sentence and len(visible.split()) >= 8
    stats = (scalar or table or (dice or field and len(visible) < 700) and not narrative_sentence) and not (label_only or image_only or visible in {'-','+'})
    prose = not only_heading and not stats and (sentence or len(visible) >= 180)
    return {'heading': heading, 'context': not prose and not stats, 'stats': stats, 'prose': prose,
            'only_heading': only_heading, 'scalar': scalar, 'label_only': label_only}


def split_sections(body):
    """Coalesce source spans, never rewrite them or turn each HTML cell into a vote.

    Prose paragraphs remain separate. Short label/value/stat runs form bounded
    blocks; headings travel with their text. Long prose is split by the model
    budget later, not by an arbitrary minimum chunk size here.
    """
    sections = _structural_sections(body)
    if not sections:
        return []
    if sections[0]['format'] == 'json':
        groups = [[s] for s in sections]
    else:
        groups = []; pending = []; pending_context = True
        def flush():
            nonlocal pending_context
            if pending:
                groups.append(pending[:]); pending.clear()
            pending_context = True
        for i,section in enumerate(sections):
            info = _info(section)
            if info['label_only'] and i+1 < len(sections) and _info(sections[i+1])['prose']:
                info['heading'] = True
            if info['heading'] and pending and not pending_context:
                flush()
            if info['prose']:
                if pending and not pending_context:
                    flush()
                pending.append(section); flush()
                continue
            # Do not cut a label from its scalar value at the target block size.
            if pending and section['offset_end'] - pending[0]['offset_start'] > 1600 and not info['scalar'] and not pending_context:
                flush()
            pending.append(section); pending_context = pending_context and info['context']
        if pending and pending_context and groups:
            groups[-1].extend(pending)
        elif pending:
            groups.append(pending)
    output = []
    for group in groups:
        start, end = group[0]['offset_start'], group[-1]['offset_end']
        text = body[start:end]; number = len(output)+1
        output.append({'number': number, 'label': f'P{number:05d}', 'offset_start': start, 'offset_end': end,
            'body': text, 'format': group[0]['format'], 'path': group[-1]['path'], 'kind': 'passage',
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'grouping_policy': VERSION, 'short_complete_document': len(groups) == 1 and len(text) <= 80,
            'parser_spans': [p for s in group for p in s['parser_spans']]})
    return output
