"""Small, bounded read-only review queries for the local SW knowledge service."""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import quote

from .semantic import job_spec, TASKS
from .pipeline import make_job
from .grouped_pipeline import group_job
from . import type_mining


PAGE = 30
MAX_PAGE = 100
AUTOMATIC_STATUSES = {'auto_grouped', 'auto_linked', 'auto_typed',
                      'auto_universe',
                      'dismissed_lexical_candidate', 'superseded_text_mining',
                      'deferred_to_merger'}


def _json(value, default=None):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _small(value, size=10000):
    if isinstance(value, str):
        return value[:size] + ('… [texte tronqué]' if len(value) > size else '')
    if isinstance(value, list):
        return [_small(item, size) for item in value[:100]]
    if isinstance(value, dict):
        return {key: _small(item, size) for key, item in value.items() if key.casefold() not in {'path', 'file', 'filename', 'database', 'download_root', 'runtime_root', 'manifest_locator'} and not key.casefold().endswith('_path')}
    return value


def _page(params):
    def num(name, default, maximum):
        raw = params.get(name, str(default))
        if not raw.isdecimal() or len(raw) > 10 or int(raw) > maximum:
            raise ValueError('Invalid ' + name)
        return int(raw)
    limit = num('limit', PAGE, MAX_PAGE)
    if not limit:
        raise ValueError('limit must be positive')
    return num('offset', 0, 2_147_483_647), limit


def filters(store):
    sources = [dict(row) for row in store.db.execute('SELECT id,dataset,title,origin FROM sources ORDER BY dataset LIMIT 2000')]
    tasks = [row[0] for row in store.db.execute('SELECT DISTINCT task FROM proposals ORDER BY task LIMIT 50')]
    statuses = [row[0] for row in store.db.execute('SELECT DISTINCT status FROM proposals ORDER BY status LIMIT 50')]
    return {'sources': sources, 'tasks': tasks, 'statuses': statuses}


def questions(store, params):
    offset, limit = _page(params)
    where, args = [], []
    for key in ('task', 'status'):
        value = params.get(key, '')
        if value:
            if len(value) > 60:
                raise ValueError('Invalid ' + key)
            where.append('p.' + key + '=?'); args.append(value)
    if params.get('source_id'):
        where.append('(ra.source_id=? OR rb.source_id=?)'); args.extend([params['source_id']] * 2)
    if params.get('record_id'):
        has_inputs = bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposal_inputs'").fetchone())
        if has_inputs:
            where.append('(p.a=? OR p.b=? OR EXISTS (SELECT 1 FROM proposal_inputs pi WHERE pi.proposal_id=p.id AND pi.record_id=?))')
            args.extend([params['record_id']] * 3)
        else:
            where.append('(p.a=? OR p.b=?)')
            args.extend([params['record_id']] * 2)
    scope = params.get('scope', '').strip()
    if scope:
        if len(scope) > 100:
            raise ValueError('Invalid scope')
        where.append("va.scope_json LIKE ? ESCAPE '\\'")
        args.append('%' + scope.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    predicate = ' WHERE ' + ' AND '.join(where) if where else ''
    # With a free-text scope/source filter, SQLite otherwise sorts millions of
    # status-index entries before applying LIMIT. Walk newest rowids instead.
    proposal_table = 'proposals p NOT INDEXED' if (scope or params.get('source_id') or params.get('status') in AUTOMATIC_STATUSES) and not params.get('task') else 'proposals p'
    sql = ('SELECT p.id,p.task,p.status,p.decision,p.score,p.a,p.b,p.predicate,p.updated_at,'
           'va.name a_name,vb.name b_name,sa.dataset,ra.source_id '
           'FROM ' + proposal_table + ' JOIN records ra ON ra.id=p.a JOIN record_versions va ON va.id=ra.current_version '
           'JOIN sources sa ON sa.id=ra.source_id LEFT JOIN records rb ON rb.id=p.b '
           'LEFT JOIN record_versions vb ON vb.id=rb.current_version' + predicate +
           ' ORDER BY p.rowid DESC LIMIT ? OFFSET ?')
    rows = [dict(row) for row in store.db.execute(sql, (*args, limit + 1, offset))]
    return {'items': rows[:limit], 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def documents(store, params):
    offset, limit = _page(params)
    q = params.get('q', '').strip()
    if len(q) > 200:
        raise ValueError('Invalid query')
    where, args = ['r.active=1'], []
    join = ''
    if q:
        # FTS supplies a bounded candidate stream; do not scan full document bodies.
        from .store import normalize
        terms = normalize(q).split()[:8]
        if not terms:
            return {'items': [], 'offset': offset, 'limit': limit, 'has_more': False}
        where.append('r.id IN (SELECT record_id FROM records_fts WHERE records_fts MATCH ?)')
        args.append(' OR '.join('"' + term + '"' for term in terms))
    if params.get('source_id'):
        where.append('r.source_id=?'); args.append(params['source_id'])
    typed = type_mining.aggregate_table(store) == 'typed_aggregates'
    kind_expr = '(SELECT rt.kind FROM typed_record_types rt WHERE rt.record_id=r.id)' if typed else 'v.kind'
    if params.get('kind'):
        where.append(kind_expr + '=?'); args.append(params['kind'])
    scope = params.get('scope', '').strip()
    if scope:
        if len(scope) > 100:
            raise ValueError('Invalid scope')
        where.append("v.scope_json LIKE ? ESCAPE '\\'")
        args.append('%' + scope.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    sql = ('SELECT r.id,r.entity_id,r.source_id,r.current_version,v.name,'+kind_expr+' kind,v.universe,v.origin,'
           'v.scope_json,length(v.body) body_characters,s.dataset,s.title source_title '
           'FROM records r' + join + ' JOIN record_versions v ON v.id=r.current_version '
           'JOIN sources s ON s.id=r.source_id WHERE ' + ' AND '.join(where) +
           ' ORDER BY r.rowid DESC LIMIT ? OFFSET ?')
    rows = [dict(row) for row in store.db.execute(sql, (*args, limit + 1, offset))]
    for row in rows:
        row['scope'] = _json(row.pop('scope_json'), {})
    return {'items': rows[:limit], 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def entities(store, params):
    offset, limit = _page(params)
    q = params.get('q', '').strip()
    if len(q) > 200:
        raise ValueError('Invalid query')
    join, where, args = '', ['e.redirect_to IS NULL'], []
    if q:
        from .store import normalize
        terms = normalize(q).split()[:8]
        if not terms:
            return {'items': [], 'offset': offset, 'limit': limit, 'has_more': False}
        where.append('e.anchor_record_id IN (SELECT record_id FROM records_fts WHERE records_fts MATCH ?)')
        args.append(' OR '.join('"' + term + '"' for term in terms))
    if params.get('source_id'):
        where.append('r.source_id=?'); args.append(params['source_id'])
    if params.get('scope'):
        scope = params['scope'].strip()
        if len(scope) > 100:
            raise ValueError('Invalid scope')
        where.append("v.scope_json LIKE ? ESCAPE '\\'")
        args.append('%' + scope.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    sql = ('SELECT e.id,e.name,e.kind,e.revision,e.anchor_record_id,r.source_id,s.dataset,'
           'v.origin,v.universe FROM entities e' + join +
           ' JOIN records r ON r.id=e.anchor_record_id JOIN record_versions v ON v.id=r.current_version '
           'JOIN sources s ON s.id=r.source_id WHERE ' + ' AND '.join(where) +
           ' ORDER BY e.rowid DESC LIMIT ? OFFSET ?')
    rows = [dict(row) for row in store.db.execute(sql, (*args, limit + 1, offset))]
    return {'items': rows[:limit], 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def entity(store, eid, params):
    row = store.db.execute('SELECT id,name,kind,anchor_record_id,redirect_to,revision FROM entities WHERE id=?', (eid,)).fetchone()
    if row is None:
        raise KeyError(eid)
    result = dict(row)
    offset, limit = _page(params)
    ids = [r[0] for r in store.db.execute('SELECT id FROM records WHERE entity_id=? AND active=1 ORDER BY id LIMIT ? OFFSET ?',
                                          (eid, limit + 1, offset))]
    result['documents'] = [document(store, rid) for rid in ids[:limit]]
    result['offset'] = offset
    result['limit'] = limit
    result['has_more'] = len(ids) > limit
    return result


def document(store, rid):
    from . import provenance
    row = store.db.execute('SELECT r.id,r.entity_id,r.source_id,r.source_key,r.current_version,r.active,'
        'v.name,v.kind,v.universe,v.origin,v.sha256,v.aliases_json,v.scope_json,v.locator_json,'
        'length(v.body) body_characters,s.dataset,s.title source_title,s.origin source_origin '
        'FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id WHERE r.id=?', (rid,)).fetchone()
    if row is None:
        raise KeyError(rid)
    result = dict(row)
    result['provenance'] = provenance.annotation(store, rid, result['current_version'])
    for key in ('aliases', 'scope', 'locator'):
        result[key] = _small(_json(result.pop(key + '_json'), [] if key == 'aliases' else {}))
    result['annotations'] = [{'task': r['task'], 'proposal_id': r['proposal_id'], 'value': _small(_json(r['value_json']))}
        for r in store.db.execute('SELECT task,proposal_id,value_json FROM annotations WHERE record_id=? ORDER BY task', (rid,))]
    typed = type_mining.record_annotation(store, rid, result['current_version'])
    if typed:
        result['original_kind'] = result['kind']; result['kind'] = typed['kind']; result['type_annotation'] = _small(typed, 1500)
    result['entity_url'] = '/entities/' + quote(result['entity_id'], safe='')
    if _aggregates_available(store):
        aggregate_row = store.db.execute('SELECT am.aggregate_id,am.rule,a.key '
            'FROM aggregate_members am JOIN aggregates a ON a.id=am.aggregate_id WHERE am.record_id=?', (rid,)).fetchone()
        result['aggregate_id'] = aggregate_row['aggregate_id'] if aggregate_row else None
        result['aggregate_rule'] = aggregate_row['rule'] if aggregate_row else None
        result['aggregate_key'] = aggregate_row['key'] if aggregate_row else None
    return result


def question(store, pid):
    row = store.db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
    if row is None:
        raise KeyError(pid)
    result = {key: row[key] for key in ('id','task','a','b','predicate','score','decision','status','error','updated_at','signature')}
    result['reasons'] = _json(row['reasons_json'], [])
    result['context'] = _small(_json(row['context_json'], {}))
    result['result'] = _small(_json(row['result_json']))
    result['parent_group_id'] = result['context'].get('parent_group_id') if isinstance(result['context'], dict) else None
    result['covered_by_proposal'] = result['result'].get('covered_by_proposal') if isinstance(result['result'], dict) else None
    ids = [row['a'], row['b']]
    group = _json(row['context_json'], {}).get('group')
    if isinstance(group, dict):
        ids.extend(group.get('record_ids', []))
        ids.extend(group.get('versions', {}).keys())
    try:
        inputs = [dict(r) for r in store.db.execute('SELECT record_id,version_id FROM proposal_inputs WHERE proposal_id=? LIMIT 32', (pid,))]
    except sqlite3.OperationalError as error:
        if 'no such table: proposal_inputs' not in str(error):
            raise
        inputs = []
    ids.extend(r['record_id'] for r in inputs)
    result['documents'] = [document(store, rid) for rid in dict.fromkeys(ids) if rid]
    result['input_versions'] = inputs
    result['automatic_heuristic'] = result['status'] in AUTOMATIC_STATUSES
    if result['task'] in TASKS and not result['automatic_heuristic']:
        try:
            if result['task'] in ('identity_tuple', 'mention_choice') and isinstance(group, dict):
                job = group_job(store, group, pid)
            elif result['task'] not in ('identity_tuple', 'mention_choice'):
                job = make_job(store, row)
            else:
                job = {'id': pid, 'task': result['task'], 'context': result['context']}
            spec = job_spec(job)
            result['openjev'] = {'instructions': spec['instructions'], 'criteria': spec['criteria'],
                                 'context': _small(job['context'], 6000), 'context_truncated_for_display': True}
        except (KeyError, ValueError, TypeError):
            result['openjev'] = {'instructions': TASKS[result['task']]['instructions'],
                                 'criteria': TASKS[result['task']]['criteria'], 'context': result['context'],
                                 'unavailable': 'Contexte du groupe incomplet ou périmé'}
    return result


def _aggregates_available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='aggregates'").fetchone())


def aggregate_types(store, params=None):
    if not _aggregates_available(store):
        return {'available': False, 'types': []}
    params=params or {}; section=params.get('section','')
    sectioned=bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='aggregate_sections'").fetchone())
    predicate=' WHERE id IN (SELECT aggregate_id FROM aggregate_sections WHERE section=?)' if section and sectioned else ''
    return {'available': True, 'types': [dict(row) for row in store.db.execute(
        'SELECT kind,count(*) count FROM '+type_mining.aggregate_table(store)+predicate+' GROUP BY kind ORDER BY count DESC,kind',(section,) if predicate else ())],
        'sections':[dict(r) for r in store.db.execute('SELECT section,count(*) aggregates,sum(documents) documents FROM aggregate_sections GROUP BY section')] if sectioned else []}


def coverage(store):
    families=[dict(r) for r in store.db.execute("SELECT CASE WHEN s.dataset LIKE 'web/%' THEN s.dataset WHEN instr(s.dataset,'/')>0 THEN substr(s.dataset,1,instr(s.dataset,'/')-1) ELSE s.dataset END family,count(*) documents,count(distinct s.id) datasets FROM records r JOIN sources s ON s.id=r.source_id WHERE r.active=1 GROUP BY family ORDER BY documents DESC")]
    receipt=store.db.execute("SELECT value FROM meta WHERE key='acquisition_summary'").fetchone()
    return {'families':families,'documents':sum(r['documents'] for r in families),
            'acquisition':_small(_json(receipt[0],{}) if receipt else {}),
            'aggregates':store.db.execute('SELECT count(*) FROM aggregates').fetchone()[0],
            'multiple_document_aggregates':store.db.execute('SELECT count(*) FROM (SELECT aggregate_id FROM aggregate_members GROUP BY aggregate_id HAVING count(*)>1)').fetchone()[0]}


def native_curation_jobs(store,params):
    """Only the new policy's jobs; old task results are deliberately excluded."""
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_v1_jobs'").fetchone():
        return {'available':False,'items':[],'paused':(store.path.parent/'openjev.pause').exists()}
    offset,limit=_page(params);where='';args=[]
    if params.get('aggregate_id'):
        where=' WHERE j.subject_id=?';args.append(params['aggregate_id'])
    rows=[]
    for row in store.db.execute('SELECT j.*,r.policy,r.planner,r.model_identity_json FROM jev_v1_jobs j '
        'JOIN jev_v1_runs r ON r.id=j.run_id'+where+' ORDER BY j.updated_at DESC,j.id LIMIT ? OFFSET ?',(*args,limit+1,offset)):
        from .disambiguation import fingerprint
        result=_json(row['result_json'],{})
        rows.append({'id':row['id'],'run_id':row['run_id'],'policy':row['policy'],'kind':row['kind'],
            'redundancy_threshold_pct':80 if 'gt80' in row['planner'] else 98,
            'redundancy_comparison':'strictly_greater' if 'gt80' in row['planner'] else 'at_least',
            'subject_id':row['subject_id'],'object_id':row['object_id'],'status':row['status'],
            'runtime_fingerprint':fingerprint(_json(row['model_identity_json'],{})),
            'selection':_small(result.get('source_selection',[])[:20]),
            'selection_total':len(result.get('source_selection',[])),
            'preview_only':bool(result.get('preview_only')),
            'link_selection':result.get('selection'),'link_score_pct':result.get('link_score_pct'),
            'entity_match_score_pct':result.get('entity_match_score_pct'),'correction_proposal':result.get('correction_proposal'),
            'model_calls':result.get('model_calls',0),'cache_hits':result.get('cache_hits',0),
            'updated_at':row['updated_at'],'has_error':bool(row['error'])})
    return {'available':True,'items':rows[:limit],'offset':offset,'limit':limit,'has_more':len(rows)>limit,
            'paused':(store.path.parent/'openjev.pause').exists(),'legacy_decisions_reused':False}


def native_curation_previews(store,params):
    offset,limit=_page(params);job=params.get('job_id','')
    if not job or len(job)>128:raise ValueError('Native job ID required')
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_v1_previews'").fetchone():return {'items':[],'available':False}
    rows=[]
    for row in store.db.execute('SELECT step,tasks_json,request_json,tokens_json,deferred_reason FROM jev_v1_previews WHERE job_id=? ORDER BY step LIMIT ? OFFSET ?',(job,limit+1,offset)):
        rows.append({'step':row['step'],'tasks':_json(row['tasks_json'],[]),'request':_small(_json(row['request_json'],{}),4000),
                     'question_tokens':_json(row['tokens_json'],{}),'reason':row['deferred_reason'],'inference_performed':False})
    return {'available':True,'items':rows[:limit],'offset':offset,'limit':limit,'has_more':len(rows)>limit,
            'preview_only':True,'context_display':'bounded UI preview; actual stored request is unchanged'}


def aggregates(store, params):
    offset, limit = _page(params)
    if not _aggregates_available(store):
        return {'available': False, 'items': [], 'offset': offset, 'limit': limit, 'has_more': False}
    kind = params.get('kind', '').strip()
    q = params.get('q', '').strip()
    if len(kind) > 60 or len(q) > 200:
        raise ValueError('Invalid aggregate filter')
    where, args = [], []
    section=params.get('section','')
    if section:
        if section not in {'scenario','reference','source','metadata'}:raise ValueError('Invalid section')
        where.append('a.id IN (SELECT aggregate_id FROM aggregate_sections WHERE section=?)');args.append(section)
    if params.get('source_id'):
        where.append('a.id IN (SELECT m.aggregate_id FROM aggregate_members m JOIN records r ON r.id=m.record_id WHERE r.source_id=?)');args.append(params['source_id'])
    if kind:
        where.append('a.kind=?'); args.append(kind)
    if q:
        from .store import normalize
        form = normalize(q)
        if not form:
            return {'available': True, 'items': [], 'offset': offset, 'limit': limit, 'has_more': False}
        # Alias form is indexed as the first primary-key field. Prefix search
        # covers Doctor/Dr aliases without scanning document bodies.
        where.append('(a.id=? OR a.id IN (SELECT aggregate_id FROM aggregate_aliases WHERE form>=? AND form<?))')
        args.extend([q, form, form + '\uffff'])
    predicate = ' WHERE ' + ' AND '.join(where) if where else ''
    if params.get('duplicates')=='1':
        where.append('(SELECT count(*) FROM aggregate_members m WHERE m.aggregate_id=a.id)>1')
        predicate = ' WHERE ' + ' AND '.join(where)
    rows = [dict(row) for row in store.db.execute(
        'SELECT a.id,a.key,a.name,a.kind,a.universe,a.anchor_record_id,a.warnings_json,'
        '(SELECT count(*) FROM aggregate_members m WHERE m.aggregate_id=a.id) member_count,'
        '(SELECT count(distinct r.source_id) FROM aggregate_members m JOIN records r ON r.id=m.record_id WHERE m.aggregate_id=a.id) source_count '
        'FROM '+type_mining.aggregate_table(store)+' a' + predicate + ' ORDER BY member_count DESC,a.name,a.id LIMIT ? OFFSET ?',
        (*args, limit + 1, offset))]
    for row in rows:
        row['warnings'] = _json(row.pop('warnings_json'), [])
    return {'available': True, 'items': rows[:limit], 'offset': offset,
            'limit': limit, 'has_more': len(rows) > limit}


def _aggregate_connections(store, aid, direction, offset, limit):
    if direction == 'outgoing':
        join = 'al.object_id=other.id'; where = 'al.subject_id=?'
    else:
        join = 'al.subject_id=other.id'; where = 'al.object_id=?'
    rows = [dict(row) for row in store.db.execute(
        'SELECT al.subject_id,al.object_id,al.kind,al.document_count,al.occurrences,al.flags_json,'
        'other.id other_id,other.name other_name,other.kind other_kind '
        'FROM aggregate_links al JOIN aggregates other ON ' + join + ' WHERE ' + where +
        ' ORDER BY al.kind,other.name,other.id LIMIT ? OFFSET ?', (aid, limit + 1, offset))]
    for row in rows:
        row['flags'] = _json(row.pop('flags_json'), [])
    return {'items': rows[:limit], 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def aggregate(store, aid, params):
    if not _aggregates_available(store):
        raise KeyError(aid)
    row = store.db.execute('SELECT id,key,name,kind,universe,anchor_record_id,warnings_json,run_id '
                           'FROM '+type_mining.aggregate_table(store)+' WHERE id=?', (aid,)).fetchone()
    if row is None:
        raise KeyError(aid)
    result = dict(row)
    result['warnings'] = _json(result.pop('warnings_json'), [])
    result['resolution_status'] = 'heuristic_aggregate'
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='aggregate_sections'").fetchone():
        result['sections']=[dict(r) for r in store.db.execute('SELECT section,documents FROM aggregate_sections WHERE aggregate_id=?',(aid,))]
        result['text_merge']=dict(store.db.execute('SELECT count(*) blocks,coalesce(sum(length(text)),0) characters FROM aggregate_text_blocks WHERE aggregate_id=?',(aid,)).fetchone())
        result['text_merge']['url']='/aggregates/'+aid+'/text'
    result['aliases'] = [dict(r) for r in store.db.execute(
        'SELECT form,label FROM aggregate_aliases WHERE aggregate_id=? ORDER BY label LIMIT 100', (aid,))]
    section = params.get('section', 'all')
    if section not in {'all', 'members', 'outgoing', 'incoming'}:
        raise ValueError('Invalid aggregate section')
    offset, limit = _page(params)
    if section in {'all', 'members'}:
        members = [dict(r) for r in store.db.execute(
            'SELECT record_id,version_id,match_key,rule FROM aggregate_members '
            'WHERE aggregate_id=? ORDER BY record_id LIMIT ? OFFSET ?', (aid, limit + 1, offset))]
        for member in members[:limit]:
            try:
                member['document'] = document(store, member['record_id'])
            except KeyError:
                member['document'] = None
        result['members'] = {'items': members[:limit], 'offset': offset, 'limit': limit,
                             'has_more': len(members) > limit}
    for direction in ('outgoing', 'incoming'):
        if section in {'all', direction}:
            result[direction] = _aggregate_connections(store, aid, direction, offset, limit)
    return result


def aggregate_text(store, aid, params):
    offset,limit=_page(params)
    if not store.db.execute('SELECT 1 FROM aggregates WHERE id=?',(aid,)).fetchone():raise KeyError(aid)
    rows=[dict(r) for r in store.db.execute('SELECT hash,sequence,text FROM aggregate_text_blocks WHERE aggregate_id=? ORDER BY sequence LIMIT ? OFFSET ?',(aid,limit+1,offset))]
    for row in rows[:limit]:
        row['sources']=[dict(r) for r in store.db.execute('SELECT e.record_id,e.version_id,e.offset_start,e.offset_end,v.name,s.dataset FROM aggregate_text_sources e JOIN records r ON r.id=e.record_id JOIN record_versions v ON v.id=e.version_id JOIN sources s ON s.id=r.source_id WHERE e.aggregate_id=? AND e.hash=? ORDER BY e.record_id,e.offset_start LIMIT 21',(aid,row['hash']))]
        row['more_sources']=len(row['sources'])>20;row['sources']=row['sources'][:20]
    return {'aggregate_id':aid,'method':'exact_whitespace_normalized_block_union','items':rows[:limit],'offset':offset,'limit':limit,'has_more':len(rows)>limit}


def aggregate_evidence(store, params):
    offset, limit = _page(params)
    if not _aggregates_available(store):
        return {'available': False, 'items': [], 'offset': offset, 'limit': limit, 'has_more': False}
    subject, object_id, kind = (params.get(key, '') for key in ('subject_id', 'object_id', 'kind'))
    if not subject or not object_id or not kind or max(map(len, (subject, object_id, kind))) > 128:
        raise ValueError('Subject, object and kind required')
    rows = [dict(r) for r in store.db.execute(
        'SELECT id,record_id,version_id,offset_start,excerpt,rule,context_json '
        'FROM aggregate_link_evidence WHERE subject_id=? AND object_id=? AND kind=? '
        'ORDER BY id LIMIT ? OFFSET ?', (subject, object_id, kind, limit + 1, offset))]
    for row in rows:
        row['context'] = _small(_json(row.pop('context_json'), {}), 4000)
        row['excerpt'] = _small(row['excerpt'], 4000)
    return {'available': True, 'items': rows[:limit], 'offset': offset,
            'limit': limit, 'has_more': len(rows) > limit}
