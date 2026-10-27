"""Explicit inference, resolution, synthesis and portable export stages."""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from . import VERSION
from .semantic import SemanticClient, WriterClient, TASKS
from .store import Store, digest, dumps, stable
from .mining import apply_decisions


def record_context(store, rid, excerpt=None):
    record = store.record(rid)
    result = {k: record[k] for k in ['id', 'name', 'aliases', 'kind', 'universe', 'origin', 'scope', 'sha256']}
    if excerpt is None:
        result['body'] = record['body']
    else:
        result['description_excerpt'] = record['body'][:excerpt]
        result['excerpt_only'] = len(record['body']) > excerpt
    return result


def make_job(store, proposal):
    context = json.loads(proposal['context_json'])
    task = proposal['task']
    if task == 'identity':
        context.update(TARGET=record_context(store, proposal['a']), CANDIDATE=record_context(store, proposal['b']))
    elif task in {'relation', 'mention'}:
        context.update(SUBJECT=record_context(store, proposal['a'], 1200), CANDIDATE=record_context(store, proposal['b'], 2000))
        if task == 'relation':
            context['OBJECT'] = context.pop('CANDIDATE')
            if proposal['predicate']:
                context['predicate'] = proposal['predicate']
    elif task == 'merge_utility':
        context['records'] = [record_context(store, proposal['a']), record_context(store, proposal['b'])]
    else:
        context['SUBJECT' if task == 'importance' else 'RECORD'] = record_context(store, proposal['a'])
    return {'id': proposal['id'], 'task': task, 'context': context}


def classifier_signature(client):
    # A sentinel covers the whole contract catalogue, including tasks not yet encountered.
    catalogue = getattr(client, 'legacy_tasks', None) or TASKS
    return digest({task: client.signature({'id': 'contract', 'task': task, 'context': {}}) for task in catalogue})


def scope_selection(scope, store=None):
    """Read each record once, instead of rescanning its body for every candidate pair."""
    if not scope:return '', []
    prefix = ('WITH scope_records AS MATERIALIZED (SELECT rr.id FROM records rr JOIN record_versions vv ON vv.id=rr.current_version '
            'JOIN sources ss ON ss.id=rr.source_id WHERE rr.active=1 AND '
            '(ss.dataset LIKE ? OR vv.scope_json LIKE ? OR vv.body LIKE ?)) ')
    params = ['%' + scope + '%'] * 3
    if store is None:return prefix, params
    revision = store.db.execute("SELECT value FROM meta WHERE key='data_revision'").fetchone()[0]
    key = (scope, revision)
    cache = getattr(store, '_scope_cache', {})
    if key not in cache:
        # Cache only IDs for this exact input revision, never semantic decisions.
        cache[key] = dumps([row[0] for row in store.db.execute(prefix + 'SELECT id FROM scope_records', params)])
        store._scope_cache = {key: cache[key]}
    return 'WITH scope_records AS MATERIALIZED (SELECT value id FROM json_each(?)) ', [cache[key]]


SCOPE_MATCH = ' AND (p.a IN (SELECT id FROM scope_records) OR p.b IN (SELECT id FROM scope_records))'


def pending_count(store, tasks=None, scope=None, retry_errors=False):
    statuses = ['pending', 'error'] if retry_errors else ['pending']
    prefix, params = scope_selection(scope, store)
    query = prefix + 'SELECT count(*) FROM proposals p WHERE status IN (' + ','.join('?' for _ in statuses) + ')'
    query += " AND p.task NOT IN ('identity_tuple','mention_choice')"
    params.extend(statuses)
    if tasks:
        query += ' AND task IN (' + ','.join('?' for _ in tasks) + ')';params.extend(tasks)
    if scope:
        query += SCOPE_MATCH
    return store.db.execute(query, params).fetchone()[0]


def invalidate_decisions(store):
    grouped = [row[0] for row in store.db.execute('SELECT min(id) FROM records WHERE active=1 GROUP BY entity_id HAVING count(*)>1')]
    for rid in grouped:
        store.detach(rid, 'semantic_configuration_changed')
    store.db.execute("UPDATE proposals SET status='pending',decision=NULL,error=NULL WHERE status NOT IN ('stale','manual_rejected')")
    store.db.execute("UPDATE links SET status='stale'")
    store.db.execute('DELETE FROM annotations')
    store.db.execute('DELETE FROM applied_decisions')
    store.db.execute('DELETE FROM application_queue')
    store.db.execute("UPDATE syntheses SET status='stale'")
    store.db.execute("DELETE FROM meta WHERE key='semantic_contract'")
    store.db.commit()
    return {'groups_reset': len(grouped)}


def continue_contract(store, previous_config, config, reason):
    """Explicit runtime transition; retain old verdicts with their own fingerprints."""
    if not reason or not reason.strip():
        raise ValueError('A runtime transition reason is required')
    previous = store.db.execute("SELECT value FROM meta WHERE key='semantic_contract'").fetchone()
    old = classifier_signature(SemanticClient(previous_config))
    new = classifier_signature(SemanticClient(config))
    if not previous or previous[0] != old:
        raise ValueError('Previous configuration must match the active semantic contract')
    # This operation is for changing local runtime capacity, not task semantics,
    # thresholds, model identity or provenance. Default classify remains strict.
    allowed = {'semantic_revision', 'semantic_batch_size', 'semantic_batch_chars', 'timeout', 'attempts',
               'semantic_legacy_signature_version', 'semantic_legacy_tasks'}
    changed = {key for key in previous_config.keys() | config.keys()
               if previous_config.get(key) != config.get(key)}
    if not changed <= allowed:
        raise ValueError('Only runtime revision and batch capacity may change')
    entry = {'previous': old, 'current': new, 'reason': reason.strip(),
             'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'previous_model_revision': SemanticClient(previous_config).revision,
             'current_model_revision': SemanticClient(config).revision,
             'changed_fields': sorted(changed), 'existing_decisions_retained': True}
    history = store.db.execute("SELECT value FROM meta WHERE key='semantic_contract_history'").fetchone()
    entries = json.loads(history[0]) if history else []
    entries.append(entry)
    store.db.execute("INSERT OR REPLACE INTO meta VALUES('semantic_contract_history',?)", (dumps(entries),))
    store.db.execute("INSERT OR REPLACE INTO meta VALUES('semantic_contract',?)", (new,))
    store.db.commit()
    return entry


def classify(store, config, limit=100, tasks=None, scope=None, retry_errors=False):
    if tasks and set(tasks) & {'identity_tuple', 'mention_choice'}:
        raise ValueError('Use curate-groups for grouped semantic tasks')
    client = SemanticClient(config)
    signature = classifier_signature(client)
    previous = store.db.execute("SELECT value FROM meta WHERE key='semantic_contract'").fetchone()
    if previous and previous[0] != signature:
        raise ValueError('Semantic contract changed: run invalidate-decisions to revoke old assignments before reclassification')
    store.db.execute("INSERT OR REPLACE INTO meta VALUES('semantic_contract',?)", (signature,))
    statuses = ['pending', 'error'] if retry_errors else ['pending']
    work_index = 'proposals_task_work' if tasks and len(tasks) == 1 else 'proposals_work'
    prefix, params = scope_selection(scope, store)
    query = prefix + 'SELECT p.* FROM proposals p INDEXED BY ' + work_index + ' JOIN records r ON r.id=p.a JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE p.status IN (' + ','.join('?' for _ in statuses) + ')'
    query += " AND p.task NOT IN ('identity_tuple','mention_choice')"
    params.extend(statuses)
    if tasks:
        if not set(tasks) <= set(TASKS):
            raise ValueError('Unknown semantic task')
        query += ' AND p.task IN (' + ','.join('?' for _ in tasks) + ')'; params.extend(tasks)
    if scope:
        query += SCOPE_MATCH
    priority = 'p.task' if tasks and len(tasks) == 1 else "CASE p.task WHEN 'identity' THEN 0 WHEN 'mention' THEN 1 WHEN 'relation' THEN 2 WHEN 'merge_utility' THEN 3 ELSE 4 END"
    query += ' ORDER BY p.status,' + priority + ',p.score DESC,p.a,p.id'
    if limit:
        query += ' LIMIT ?';params.append(limit)
    proposals = store.db.execute(query, params)
    results = collections.Counter();inferred = deferred = 0
    while batch := proposals.fetchmany(client.batch_size):
        if config.get('pause_file') and Path(config['pause_file']).exists():
            return {'classified': sum(results.values()), 'evaluated_by_model': inferred, 'deferred_without_inference': deferred, 'decisions': dict(results), 'paused': True}
        jobs = [make_job(store, proposal) for proposal in batch]
        try:
            verdicts = client.evaluate(jobs)
            for proposal, verdict in zip(batch, verdicts):
                if proposal['task'] == 'relation' and proposal['predicate'] and verdict['decision'] not in {proposal['predicate'], 'none', 'uncertain'}:
                    verdict.update(accepted=False, decision='uncertain', status='predicate_mismatch')
                store.store_result(proposal['id'], verdict)
                results[verdict['decision']] += 1
                if verdict.get('inference_performed') is False:deferred += 1
                else:inferred += 1
            store.db.commit()
        except Exception as error:
            store.db.rollback()
            for proposal in batch:
                store.db.execute("UPDATE proposals SET status='error',error=? WHERE id=?", (str(error)[:2000], proposal['id']))
            store.db.commit()
            raise RuntimeError('Semantic batch checkpointed as error; retry explicitly: ' + str(error)) from error
    return {'classified': sum(results.values()), 'evaluated_by_model': inferred, 'deferred_without_inference': deferred, 'decisions': dict(results)}


def curate(store, config, limit=0, tasks=None, scope=None, retry_errors=False, continuous=False, work_batch=100):
    """Drain inference/application queues incrementally. Never invokes the writer."""
    if work_batch < 1 or work_batch > 1000:
        raise ValueError('work_batch must be in 1..1000')
    pause = store.path.parent / 'openjev.pause'
    config = {**config, 'pause_file': str(pause)}
    totals = collections.Counter();decisions = collections.Counter();last_rounds = collections.deque(maxlen=5)
    rounds = 0;state = 'running'
    started = time.monotonic();started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    def report():
        value = {'state': state, 'rounds': rounds, 'classified': totals['classified'], 'decisions': dict(decisions),
                 'evaluated_by_model': totals['evaluated_by_model'], 'deferred_without_inference': totals['deferred_without_inference'],
                 'started_utc': started_utc, 'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'elapsed_seconds': round(time.monotonic() - started, 2),
                 'last_rounds': list(last_rounds), 'pending': pending_count(store, tasks, scope, retry_errors),
                 'scope': scope, 'tasks': tasks, 'writer_started': False}
        target = store.path.parent / 'openjev-progress.json'
        temporary = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        temporary.replace(target)
        return value
    report()
    while continuous or rounds < 64:
        if pause.exists():state = 'paused';break
        remaining = limit - totals['classified'] if limit else work_batch
        try:
            classified = classify(store, config, min(remaining, work_batch), tasks, scope, retry_errors)
            resolved = apply_decisions(store)
        except Exception:
            state = 'error';report();raise
        totals['classified'] += classified['classified'];decisions.update(classified['decisions']);rounds += 1
        totals.update({key: classified.get(key, 0) for key in ('evaluated_by_model', 'deferred_without_inference')})
        last_rounds.append({'classification': classified, 'resolution': resolved})
        if classified.get('paused'):state = 'paused';break
        if limit and totals['classified'] >= limit:state = 'budget_reached';break
        pending = pending_count(store, tasks, scope, retry_errors)
        if not pending:state = 'complete';break
        report()
    if state == 'running':state = 'round_limit'
    return report()


def utility_jobs(store):
    count = 0
    for entity in store.db.execute('SELECT entity_id FROM records WHERE active=1 GROUP BY entity_id HAVING count(*)>1'):
        anchor = store.db.execute('SELECT anchor_record_id FROM entities WHERE id=?', (entity[0],)).fetchone()[0]
        for row in store.db.execute('SELECT id FROM records WHERE entity_id=? AND id!=? AND active=1', (entity[0], anchor)):
            store.enqueue('merge_utility', anchor, row[0], context={'purpose': 'editorial utility within resolved identity'}, reasons=['resolved_group']); count += 1
    store.db.commit()
    return count


def compose(store, config, limit=10, entity_id=None):
    if config.get('writer_enabled') is False:
        raise ValueError('Strata/synthesis is disabled for the current aggregation and OpenJev pass')
    client = WriterClient(config)
    entities = [entity_id] if entity_id else [row[0] for row in store.db.execute('SELECT entity_id FROM records WHERE active=1 GROUP BY entity_id HAVING count(*)>1')]
    result = collections.Counter()
    processed = 0
    for eid in entities:
        entity = store.entity(eid)
        ids = [r['id'] for r in entity['records']]
        if len(ids) < 2:
            result['single_record_no_synthesis_needed'] += 1;continue
        marks = ','.join('?' for _ in ids)
        utilities = store.db.execute(f"SELECT * FROM proposals WHERE task='merge_utility' AND a IN ({marks}) AND b IN ({marks}) AND status='accepted'", (*ids, *ids)).fetchall()
        positive = [row for row in utilities if row['decision'] in {'complementary', 'conflicting'}]
        if not positive:
            result['awaiting_utility_or_keep_separate'] += 1;continue
        context = {'entity_id': eid, 'name': entity['entity']['name'], 'language': 'French',
                   'records': [record_context(store, rid) for rid in ids],
                   'conflicts': [{'text': 'OpenJev signals contradictory source records; retain alternatives.', 'record_ids': [row['a'], row['b']]} for row in positive if row['decision'] == 'conflicting']}
        signature = client.signature(context)
        if store.db.execute('SELECT 1 FROM syntheses WHERE entity_id=? AND signature=? AND status=?', (eid, signature, 'ready')).fetchone():
            result['cached'] += 1;continue
        # Retain a complete group as the atomic synthesis unit. Oversized groups stay readable as linked records.
        # Explicitly defer rather than silently summarizing away stats or cutting a record to fit a writer budget.
        try:
            synthesis = client.synthesize(context)
        except ValueError as error:
            if 'budget' in str(error).casefold():
                store.gap(ids[0], 'compose', 'writer_budget', {'entity_id': eid, 'records': len(ids), 'error': str(error)})
                result['budget_deferred'] += 1;continue
            raise
        referenced = {rid for paragraph in synthesis['paragraphs'] for rid in paragraph['record_ids']}
        if not set(ids) <= referenced:
            raise ValueError('Writer omitted source records; linked raw records retained, synthesis rejected')
        store.db.execute('INSERT OR REPLACE INTO syntheses VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)',
                         (stable('syn', eid, signature), eid, signature, dumps(synthesis), 'ready'))
        store.db.commit(); result['written'] += 1;processed += 1
        if limit and processed >= limit:
            break
    return dict(result)


def export(store, target, reference_only=False):
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError('Exports are immutable; choose a new destination: ' + str(target))
    if target == store.path.parent or target in store.path.parents:
        raise ValueError('Export destination must be separate from the database')
    temporary = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
    temporary.mkdir(parents=True)
    # One read transaction keeps all files on the same WAL snapshot during a concurrent import.
    store.db.execute('BEGIN')
    tables = {
        'sources': 'SELECT * FROM sources ORDER BY id',
        'aggregates': 'SELECT * FROM aggregates ORDER BY id',
        'aggregate_members': 'SELECT * FROM aggregate_members ORDER BY aggregate_id,record_id',
        'aggregate_aliases': 'SELECT * FROM aggregate_aliases ORDER BY aggregate_id,form',
        'record_sections':'SELECT * FROM record_sections ORDER BY section,record_id',
        'aggregate_sections':'SELECT * FROM aggregate_sections ORDER BY section,aggregate_id',
        'aggregate_text_blocks':'SELECT * FROM aggregate_text_blocks ORDER BY aggregate_id,sequence',
        'aggregate_text_sources':'SELECT * FROM aggregate_text_sources ORDER BY aggregate_id,hash,record_id,offset_start',
        'aggregate_links': 'SELECT * FROM aggregate_links ORDER BY subject_id,object_id,kind',
        'aggregate_link_evidence': 'SELECT * FROM aggregate_link_evidence ORDER BY id',
        'cleaning_runs': 'SELECT * FROM cleaning_runs ORDER BY created_at',
        'records': 'SELECT r.id record_id,r.source_id,r.source_key,r.entity_id,r.current_version version_id,r.active,v.sha256,v.name,v.kind,v.universe,v.origin,v.aliases_json,v.scope_json,v.locator_json,v.payload_json,v.body,v.created_at FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1 ORDER BY r.id',
        'passages': 'SELECT p.* FROM passages p JOIN records r ON r.current_version=p.version_id WHERE r.active=1 ORDER BY p.version_id,p.sequence',
        'profiles': 'SELECT p.* FROM profiles p JOIN records r ON r.id=p.record_id WHERE r.active=1 AND p.version_id=r.current_version ORDER BY p.id',
        'profile_values': 'SELECT pv.* FROM profile_values pv JOIN profiles p ON p.id=pv.profile_id JOIN records r ON r.id=p.record_id WHERE r.active=1 AND p.version_id=r.current_version ORDER BY pv.profile_id,pv.field,pv.source_path',
        'entities': 'SELECT e.*,(SELECT count(*) FROM records r WHERE r.entity_id=e.id AND r.active=1) active_records FROM entities e ORDER BY e.id',
        'relations': "SELECT l.*,a.entity_id subject_entity_id,b.entity_id object_entity_id FROM links l JOIN records a ON a.id=l.subject_record_id JOIN records b ON b.id=l.object_record_id WHERE l.status='accepted' ORDER BY l.id",
        'proposals': 'SELECT * FROM proposals ORDER BY id',
        'annotations': 'SELECT * FROM annotations ORDER BY record_id,task',
        'editorial': 'SELECT * FROM editorial ORDER BY entity_id',
        'syntheses': "SELECT * FROM syntheses WHERE status='ready' ORDER BY id",
        'resolution_events': 'SELECT * FROM resolutions ORDER BY id',
        'document_links': 'SELECT l.* FROM document_links l JOIN records a ON a.id=l.subject_record_id JOIN records b ON b.id=l.object_record_id WHERE a.active=1 AND b.active=1 AND a.current_version=l.subject_version AND b.current_version=l.object_version ORDER BY subject_record_id,object_record_id,kind',
        'mining_gaps': 'SELECT * FROM mining_gaps ORDER BY record_id,stage,reason',
        'decision_history': 'SELECT * FROM decision_history ORDER BY id',
        'record_registry': 'SELECT * FROM records ORDER BY id',
        'version_registry': 'SELECT id,record_id,sha256,name,kind,origin,locator_json,created_at FROM record_versions ORDER BY id',
    }
    if reference_only:
        for name in ('proposals', 'decision_history', 'resolution_events', 'record_registry', 'version_registry'):
            del tables[name]
    metadata = dict(store.db.execute("SELECT key,value FROM meta WHERE key IN ('data_revision','semantic_contract','semantic_contract_history','identity_resolution_policy','active_cleaning_run')"))
    manifest = {'schema_version': VERSION, 'evidence_mode': 'imported_records', 'embedding': None,
                'working_identity':'aggregate_id','active_cleaning_run':metadata.get('active_cleaning_run'),
                'export_mode': 'reference_only' if reference_only else 'reference_and_audit',
                'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'data_revision': metadata.get('data_revision'), 'semantic_contract': metadata.get('semantic_contract'),
                'semantic_contract_history': json.loads(metadata.get('semantic_contract_history', '[]')),
                'identity_resolution_policy': metadata.get('identity_resolution_policy'),
                'semantic_tasks': [dict(row) for row in store.db.execute('SELECT task,status,count(*) count FROM proposals GROUP BY task,status')],
                'history': ('Curation audit streams, historical records and undo state remain authoritative in SQLite.' if reference_only else
                            'Audit registries retain historical IDs; full previous bodies and undo state remain authoritative in SQLite.'), 'files': {}}
    for name, query in tables.items():
        path = temporary / (name + '.jsonl'); hashed = hashlib.sha256(); count = 0
        with path.open('wb') as stream:
            for row in store.db.execute(query):
                value = dict(row)
                for key in list(value):
                    if key.endswith('_json') and value[key] is not None:
                        value[key[:-5]] = json.loads(value.pop(key))
                data = (dumps(value) + '\n').encode('utf-8');stream.write(data);hashed.update(data);count += 1
        manifest['files'][path.name] = {'records': count, 'sha256': hashed.hexdigest(), 'bytes': path.stat().st_size}
    (temporary / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    store.db.commit()
    return manifest
