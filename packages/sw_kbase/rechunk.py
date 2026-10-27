"""CPU-only rebuild of an existing selection plan, preserving references and originals."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from . import enrichment
from .disambiguation import fingerprint
from .document_sections import VERSION, split_sections
from .store import dumps
from .type_mining import source_fingerprint


def activate(main, output):
    """Select a verified new plan for navigation/resume; never unpause or execute."""
    output = Path(output).resolve(); base = main.path.parent
    if not (base/'openjev.pause').is_file(): raise ValueError('Keep Jev paused before changing the active plan')
    report = json.loads((output/'plan.json').read_text(encoding='utf-8'))
    if report.get('state') != 'planned' or not report.get('all_old_passages_mapped') or report.get('source_data_hash_after') != source_fingerprint(main):
        raise ValueError('Unverified or stale rechunked plan')
    db = sqlite3.connect((output/'enrichment.sqlite').as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    try:
        meta = enrichment.meta(db)
        if meta['units_policy'] != VERSION or meta['state'] != 'planned' or db.execute('SELECT count(*) FROM requests').fetchone()[0]:
            raise ValueError('Expected a new, unscored coherent plan')
        states = dict(db.execute('SELECT status,count(*) FROM documents GROUP BY status'))
        if set(states)-{'reference','pending'}: raise ValueError('Unexpected document decisions')
        counts = dict(db.execute('SELECT status,count(*) FROM paragraphs GROUP BY status'))
        if set(counts)-{'reference','pending'}: raise ValueError('Unexpected paragraph decisions')
    finally: db.close()
    # Backups contain the exact previous local control files. No model settings change.
    archive = output/'control-before'; archive.mkdir(exist_ok=True)
    saved = {}
    for name in ('active-reference-workflow.json','pause-checkpoint.json'):
        path = base/name
        if path.is_file():
            data = path.read_bytes(); saved[name] = json.loads(data.decode('utf-8-sig'))
            backup = archive/name
            if not backup.exists(): backup.write_bytes(data)
    if 'active-reference-workflow.json' in saved:
        pointer = saved['active-reference-workflow.json']
        if Path(pointer.get('enrichment','')).resolve() != output:
            pointer.update(previous_enrichment=pointer.get('enrichment'),enrichment=str(output),state='paused_rechunked')
            enrichment.atomic_json(base/'active-reference-workflow.json',pointer)
    if 'pause-checkpoint.json' in saved:
        checkpoint = saved['pause-checkpoint.json']
        if Path(checkpoint.get('selection_database','')).resolve() != output/'enrichment.sqlite':
            checkpoint.update(archived_selection_database=checkpoint.get('selection_database'),
            archived_cached_requests=report['archived_requests'],selection_database=str(output/'enrichment.sqlite'),
            enrichment_output=str(output),state='paused_rechunked',resume_requires_explicit_user_request=True,
            cached_requests=0,documents={'pending':states.get('pending',0),'scored':0},paragraphs=counts,
            resumption='New coherent blocks; same references and source order. All candidate votes pending. '
                       'Old requests remain archived in the previous plan; do not reuse old fragment decisions.')
            enrichment.atomic_json(base/'pause-checkpoint.json',checkpoint)
    return {'active_plan':str(output),'started':False,'paused':True,'old_requests_archived':report['archived_requests']}


def rebuild(main, previous, output, preload=True, resume=False):
    previous = Path(previous).resolve(); output = Path(output).resolve()
    if output == previous or output.is_relative_to(previous):
        raise ValueError('Keep the previous plan separate and intact')
    old = sqlite3.connect((previous/'enrichment.sqlite').as_uri()+'?mode=ro',uri=True)
    old.row_factory = sqlite3.Row
    old.execute('PRAGMA cache_size=-65536'); old.execute('PRAGMA temp_store=MEMORY')
    old.execute('PRAGMA query_only=ON'); old.execute('BEGIN')
    settings = enrichment.meta(old)
    if settings.get('state') == 'planning' or settings.get('policy') != 'sw-enrichment/3-grouped-paragraphs-no-global':
        old.close(); raise ValueError('A complete grouped plan is required')
    if Path(settings['main_database']).resolve() != main.path.resolve():
        old.close(); raise ValueError('Different main database')
    if settings['cleaning_run'] != enrichment.active_run(main):
        old.close(); raise ValueError('Aggregates changed; prepare a fresh reference plan instead')
    before = source_fingerprint(main); started = time.monotonic()
    output.mkdir(parents=True,exist_ok=resume)
    db = sqlite3.connect(output/'enrichment.sqlite'); db.row_factory = sqlite3.Row
    counts = Counter({k:0 for k in ('entities','documents','paragraphs','reference_documents','candidate_documents',
                                  'reference_paragraphs','candidate_paragraphs')}); metrics = Counter()
    try:
        db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL'); db.execute('PRAGMA cache_size=-524288')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('PRAGMA temp_store=MEMORY')
        if resume:
            existing = enrichment.meta(db)
            if existing.get('state') != 'planning' or existing.get('units_policy') != VERSION or existing.get('rechunked_from') != str(previous) or db.execute('SELECT count(*) FROM requests').fetchone()[0]:
                raise ValueError('Only an incomplete, unscored rebuild can resume')
        if not resume:db.executescript(enrichment.DDL)
        db.execute('CREATE TABLE IF NOT EXISTS legacy_paragraph_map(old_paragraph_id TEXT PRIMARY KEY,new_paragraph_id TEXT NOT NULL REFERENCES paragraphs(id),old_status TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS legacy_paragraph_new ON legacy_paragraph_map(new_paragraph_id)')
        # Sequential reads avoid hundreds of thousands of random disk seeks.
        # This workstation has ample RAM; --stream remains available elsewhere.
        live = {}; bodies = {}; origins = {}; old_spans = {}
        if preload:
            enrichment.atomic_json(output/'planning-progress.json',{'state':'preloading','documents':0,'model_calls':0})
            live = {r['id']:r for r in main.db.execute('SELECT r.id,r.current_version,r.source_id,m.aggregate_id,e.revision FROM records r '
                'JOIN aggregate_members m ON m.record_id=r.id LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.active=1')}
            origins = {r['id']:json.loads(r['metadata_json']) for r in main.db.execute('SELECT id,metadata_json FROM sources')}
            bodies = {r['id']:r['body'] for r in main.db.execute('SELECT id,body FROM record_versions ORDER BY rowid')}
            for p in old.execute('SELECT document_id,id,offset_start,offset_end,body,status FROM paragraphs ORDER BY rowid'):
                old_spans.setdefault(p['document_id'],[]).append((p['id'],p['offset_start'],p['offset_end'],
                    hashlib.sha256(p['body'].encode()).digest(),p['status'],len(p['body'])))
        keep = {k:settings[k] for k in ('policy','config','cleaning_run','main_database','complete_scope')}
        keep.update(units_policy=VERSION,state='planning',inference_started=False,
            rechunked_from=str(previous),reference_selection_reused=True,prior_decisions_reused=False,
            archived_requests=old.execute('SELECT count(*) FROM requests').fetchone()[0])
        if not resume:db.executemany('INSERT INTO meta VALUES(?,?)',[(k,dumps(v)) for k,v in keep.items()])
        complete = {r['id'] for r in db.execute('SELECT id FROM documents')}
        existing_sizes = dict(db.execute('SELECT document_id,count(*) FROM paragraphs GROUP BY document_id')) if resume else {}
        if resume:
            for role,n in db.execute('SELECT role,count(*) FROM documents GROUP BY role'):
                counts['documents'] += n; counts[role+'_documents'] = n
            for role,n in db.execute('SELECT d.role,count(*) FROM paragraphs p JOIN documents d ON d.id=p.document_id GROUP BY d.role'):
                counts['paragraphs'] += n; counts[role+'_paragraphs'] = n
            metric = db.execute("SELECT coalesce(sum(length(body)<=40),0),coalesce(sum(length(body)<=80),0),coalesce(sum(json_extract(unit_json,'$.short_complete_document')),0),coalesce(sum(length(body)),0) FROM paragraphs").fetchone()
            metrics.update(dict(zip(('new_le40','new_le80','short_complete_documents','new_characters'),metric)))
        for e in old.execute('SELECT * FROM entities ORDER BY id'):
            db.execute('INSERT OR IGNORE INTO entities VALUES(?,?,?,?,?,?)',
                (e['id'],e['reference_id'],e['order_json'],e['snapshot_hash'],'planned',e['ranking_json']))
            counts['entities'] += 1
        db.commit()
        for doc in old.execute('SELECT * FROM documents ORDER BY entity_id,id'):
            row = live.get(doc['id']) if preload else main.db.execute('SELECT r.current_version,v.body,m.aggregate_id,s.metadata_json,e.revision '
                'FROM records r JOIN record_versions v ON v.id=r.current_version '
                'JOIN aggregate_members m ON m.record_id=r.id JOIN sources s ON s.id=r.source_id '
                'LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.id=? AND r.active=1',(doc['id'],)).fetchone()
            metadata = json.loads(doc['metadata_json'])
            if not row or row['current_version'] != doc['version_id'] or row['aggregate_id'] != doc['entity_id']:
                raise ValueError('Source version or membership changed: '+doc['id'])
            body = bodies[row['current_version']] if preload else row['body']
            origin = origins[row['source_id']] if preload else json.loads(row['metadata_json'])
            if hashlib.sha256(body.encode()).hexdigest() != metadata['body_sha256'] or row['revision'] != metadata.get('editorial_revision') or origin != metadata['source_metadata']:
                raise ValueError('Source body, origin or editorial snapshot changed: '+doc['id'])
            if doc['id'] in complete:
                saved = old_spans.get(doc['id'],[]) if preload else list(old.execute('SELECT id,offset_start,offset_end,body,status FROM paragraphs WHERE document_id=?',(doc['id'],)))
                metrics['old_paragraphs'] += len(saved)
                metrics['old_le40'] += sum(p[5]<=40 for p in saved) if preload else sum(len(p['body'])<=40 for p in saved)
                metrics['documents_changed'] += len(saved) != existing_sizes.get(doc['id'],0)
                continue
            reference = doc['role'] == 'reference'
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',
                (doc['id'],doc['entity_id'],doc['version_id'],doc['metadata_json'],doc['role'],'reference' if reference else 'pending',None))
            sections = split_sections(body); new = []
            for section in sections:
                if body[section['offset_start']:section['offset_end']] != section['body']:
                    raise ValueError('Non-exact chunk')
                pid = 'para-'+fingerprint([doc['id'],doc['version_id'],section['offset_start'],section['offset_end'],VERSION])
                db.execute('INSERT INTO paragraphs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (pid,doc['id'],section['number'],section['label'],section['offset_start'],section['offset_end'],section['body'],
                     dumps({k:v for k,v in section.items() if k != 'body'}),'reference' if reference else 'pending',None))
                new.append((section['offset_start'],section['offset_end'],pid))
                counts['paragraphs'] += 1; counts[doc['role']+'_paragraphs'] += 1
                metrics['new_le40'] += len(section['body']) <= 40
                metrics['new_le80'] += len(section['body']) <= 80
                metrics['short_complete_documents'] += section['short_complete_document']
                metrics['new_characters'] += len(section['body'])
            index = 0; old_count = 0
            old_rows = old_spans.get(doc['id'],[]) if preload else ((p['id'],p['offset_start'],p['offset_end'],hashlib.sha256(p['body'].encode()).digest(),p['status'],len(p['body']))
                for p in old.execute('SELECT id,offset_start,offset_end,body,status FROM paragraphs WHERE document_id=? ORDER BY number',(doc['id'],)))
            for pid, pstart, pend, proof, status, length in old_rows:
                old_count += 1
                while index < len(new) and new[index][1] <= pstart: index += 1
                if index == len(new) or new[index][0] > pstart or new[index][1] < pend or hashlib.sha256(body[pstart:pend].encode()).digest() != proof:
                    raise ValueError('Original passage not covered exactly: '+pid)
                db.execute('INSERT INTO legacy_paragraph_map VALUES(?,?,?)',(pid,new[index][2],status))
                metrics['old_paragraphs'] += 1; metrics['old_le40'] += length <= 40
            metrics['documents_changed'] += old_count != len(new)
            counts['documents'] += 1; counts[doc['role']+'_documents'] += 1
            if counts['documents'] % 1000 == 0:
                db.commit()
                enrichment.atomic_json(output/'planning-progress.json',{'state':'rechunking',**counts,**metrics,
                    'model_calls':0,'elapsed_seconds':round(time.monotonic()-started,1)})
        if counts['documents'] != old.execute('SELECT count(*) FROM documents').fetchone()[0]:
            raise ValueError('Incomplete document inventory')
        after = source_fingerprint(main)
        if after != before: raise ValueError('Knowledge changed during rebuilding')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('counts',dumps(dict(counts))))
        db.execute("UPDATE meta SET value_json=? WHERE key='state'",(dumps('planned'),))
        db.commit()
        report = {'state':'planned','policy':settings['policy'],'units_policy':VERSION,**counts,**metrics,
            'previous':str(previous),'output':str(output),'model_calls':0,'prior_semantic_selections_reused':False,
            'reference_selection_reused':True,'archived_requests':keep['archived_requests'],
            'all_old_passages_mapped':True,'source_data_hash_before':before,'source_data_hash_after':after,
            'elapsed_seconds':round(time.monotonic()-started,1),'read_mode':'preloaded' if preload else 'stream','cpu_rebuild_resumed':resume}
        enrichment.atomic_json(output/'plan.json',report)
        enrichment.atomic_json(output/'planning-progress.json',report)
        return report
    except Exception as error:
        db.rollback()
        enrichment.atomic_json(output/'planning-progress.json',{'state':'failed','error':str(error),**counts,'model_calls':0})
        raise
    finally:
        db.close(); old.close()
