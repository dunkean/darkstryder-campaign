"""Display exactly the stored model input and output, with no new inference."""
from contextlib import closing
import html
import json
from pathlib import Path
import re
import sqlite3


def render_compare(root, jid=None):
    root = Path(root)
    with closing(sqlite3.connect((root / 'articles.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        entries = db.execute('SELECT id,name FROM jobs ORDER BY name COLLATE NOCASE,id').fetchall()
        if not entries: raise KeyError('No pilot documents')
        if jid is None:
            jid = next((e['id'] for e in entries if e['name'] == 'Aural Blaster'), entries[0]['id'])
        row = db.execute('SELECT name,request_json,response_json,bundle_json FROM jobs WHERE id=?', (jid,)).fetchone()
    if row is None: raise KeyError(jid)
    esc = lambda x: html.escape(str(x), quote=True)
    payload = json.loads(row['request_json'])
    # Read the persisted request, never rebuild it from today's prompt or sources.
    user_text = payload['messages'][0]['content']
    context = json.loads(user_text)
    system = payload.get('system', '')
    if not isinstance(system, str): system = json.dumps(system, ensure_ascii=False, indent=2)
    response = json.loads(row['response_json']) if row['response_json'] else None
    raw = ''.join(b.get('text', '') for b in response.get('content', []) if b.get('type') == 'text') if response else ''
    fence = re.fullmatch(r'\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*', raw, flags=re.IGNORECASE)
    decoded = None
    try:
        decoded = json.loads(fence[1] if fence else raw)
        readable = json.dumps(decoded, ensure_ascii=False, indent=2)
    except (ValueError, TypeError): readable = raw
    anchors = {}
    for i, part in enumerate(context.get('texts', []), 1):
        if part.get('document'): anchors.setdefault(part['document'], 'input-document-' + str(i))
    index = next(i for i, e in enumerate(entries) if e['id'] == jid)
    options = ''.join('<option value="' + e['id'] + '"' + (' selected' if e['id'] == jid else '') + '>' + esc(e['name']) + '</option>' for e in entries)
    nav = ''
    if index: nav += '<a href="/?article=' + entries[index-1]['id'] + '">← Précédente</a>'
    if index+1 < len(entries): nav += '<a href="/?article=' + entries[index+1]['id'] + '">Suivante →</a>'
    out = ['<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>' + esc(row['name']) + ' · Entrée / réponse Haiku</title><link rel="stylesheet" href="/compare.css">',
           '<body><header><strong>Entrée / réponse Haiku</strong><form action="/" method="get">',
           '<label for="article-select">Fiche</label><select id="article-select" name="article">' + options + '</select>',
           '<button type="submit">Afficher</button></form><nav>' + nav + '</nav><span>' + str(index+1) + ' / ' + str(len(entries)) + '</span></header>',
           '<main><section aria-label="Ce qui a été envoyé"><div class="pane-head"><h1>Ce qui a été envoyé</h1></div><div class="pane-body">',
           '<details><summary>Prompt système exact</summary><pre id="system-text">' + esc(system) + '</pre></details>']
    for i, part in enumerate(context.get('texts', []), 1):
        role = {'reference': 'Référence', 'selected addition': 'Paragraphe ajouté', 'uncertain addition': 'Paragraphe ajouté · incertain'}.get(part.get('role'), part.get('role', 'Texte'))
        source = part.get('source', {})
        label = source.get('acquisition', '')
        works = source.get('works') or []
        if works: label += ' · ' + ' / '.join(str(w.get('title') or '') for w in works)
        document = part.get('document')
        anchor = anchors.get(document)
        out.append('<article' + (' id="'+anchor+'"' if anchor and anchor == 'input-document-'+str(i) else '') + '><h2>' + esc(role) + ' <small>' + str(i) + '</small></h2>')
        if document: out.append('<p class="source"><strong>' + esc(document) + '</strong></p>')
        if label: out.append('<p class="source">' + esc(label) + '</p>')
        out.append('<pre class="input-text">' + esc(part['text']) + '</pre></article>')
    if context.get('existing_profiles'):
        out.append('<h2>Profils également envoyés</h2><pre id="profiles-text">' + esc(json.dumps(context['existing_profiles'], ensure_ascii=False, indent=2)) + '</pre>')
    out.append('<details><summary>Message utilisateur exact · tous les champs</summary><pre id="user-text">' + esc(user_text) + '</pre></details>')
    out.append('<details><summary>Requête complète enregistrée</summary><pre id="request-text">' + esc(json.dumps(payload, ensure_ascii=False, indent=2)) + '</pre></details>')
    out.append('</div></section><section aria-label="Ce qui a été reçu"><div class="pane-head"><h1>Ce qui a été reçu</h1>')
    if response: out.append('<button type="button" id="toggle-raw">Voir le brut exact</button>')
    out.append('</div><div class="pane-body">')
    if response:
        warnings = decoded.get('warnings', []) if isinstance(decoded, dict) else []
        if isinstance(warnings, list) and warnings:
            labels = json.loads(row['bundle_json']).get('document_labels', {})
            out.append('<aside class="document-warning" role="alert"><h2>DOCUMENTS À VÉRIFIER AVANT INTÉGRATION</h2>')
            for warning in warnings:
                if not isinstance(warning, dict): continue
                document = str(warning.get('document', ''))
                out.append('<p><strong>' + esc(document) + '</strong><br>' + esc(warning.get('reason', '')) + '</p>')
                if document in anchors:
                    out.append('<p><a href="#' + anchors[document] + '">Voir le texte envoyé</a></p>')
                if document in labels:
                    out.append('<p><a href="/sources/' + esc(jid) + '/' + esc(labels[document]) + '">Ouvrir le document original complet</a></p>')
            out.append('</aside>')
        elif isinstance(decoded, dict) and str(decoded.get('type', '')).strip().casefold() == 'unknown':
            out.append('<aside class="document-warning" role="alert"><h2>TYPE UNKNOWN À RENSEIGNER</h2><p>Avertissement du contrôleur : le sujet n’a pas été classé.</p></aside>')
        out.append('<pre id="response-readable">' + esc(readable) + '</pre><pre id="response-raw" hidden>' + esc(raw) + '</pre>')
        out.append('<details><summary>Réponse API complète</summary><pre id="response-envelope">' + esc(json.dumps(response, ensure_ascii=False, indent=2)) + '</pre></details>')
    else: out.append('<p>Pas encore de réponse enregistrée.</p>')
    out.append('</div></section></main><script type="module" src="/compare.js"></script></body></html>')
    return ''.join(out)
