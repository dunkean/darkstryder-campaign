"""Durable native Jev selections, separate from the historical proposal worker.

Only merge *selections* are published. Original records, memberships, lexical
links, editorial overlays and the historical decision cache are never changed.
Long contexts are deferred intact, not truncated or treated as redundant.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import time
import uuid

from .cleaning import name_key
from .disambiguation import DisambiguationLoop, PROMPTS, VERSION, fingerprint, probabilities
from .document_curation import DocumentCoverageLoop
from .curation_profiles import normalize_profile,preserve_reasons
from .store import dumps

PLANNER = 'native-curation-planner/3-document-units-strict-gt80'
DDL = """
CREATE TABLE IF NOT EXISTS jev_v1_runs(
 id TEXT PRIMARY KEY,policy TEXT NOT NULL,planner TEXT NOT NULL,
 model_identity_json TEXT NOT NULL,cleaning_run TEXT NOT NULL,
 created_at TEXT NOT NULL,status TEXT NOT NULL,metrics_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jev_v1_jobs(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES jev_v1_runs(id),
 kind TEXT NOT NULL,subject_id TEXT NOT NULL,object_id TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,
 result_json TEXT,error TEXT,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jev_v1_jobs_run ON jev_v1_jobs(run_id,status,kind);
CREATE INDEX IF NOT EXISTS jev_v1_jobs_subject ON jev_v1_jobs(subject_id,kind,updated_at);
CREATE TABLE IF NOT EXISTS jev_v1_requests(
 id TEXT PRIMARY KEY,policy TEXT NOT NULL,model_identity_json TEXT NOT NULL,
 request_json TEXT NOT NULL,status TEXT NOT NULL,response_json TEXT,
 error TEXT,latency_ms REAL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jev_v1_job_requests(
 job_id TEXT NOT NULL REFERENCES jev_v1_jobs(id),step INTEGER NOT NULL,
 request_id TEXT NOT NULL REFERENCES jev_v1_requests(id),
 PRIMARY KEY(job_id,step));
CREATE TABLE IF NOT EXISTS jev_v1_previews(
 job_id TEXT NOT NULL REFERENCES jev_v1_jobs(id),step INTEGER NOT NULL,
 tasks_json TEXT NOT NULL,request_json TEXT NOT NULL,tokens_json TEXT NOT NULL,
 deferred_reason TEXT,PRIMARY KEY(job_id,step));
"""


def now():return datetime.now(timezone.utc).isoformat()


def initialize(store):
    store.db.executescript(DDL)
    store.db.commit()


def active_run(store):
    row=store.db.execute("SELECT value FROM meta WHERE key='active_cleaning_run'").fetchone()
    if not row:raise ValueError('Build the algorithmic aggregates first')
    return row[0]


def member_snapshot(store,aid):
    anchor=store.db.execute('SELECT anchor_record_id FROM aggregates WHERE id=?',(aid,)).fetchone()
    if not anchor:raise ValueError('Aggregate is absent: '+aid)
    members=[dict(row) for row in store.db.execute(
        'SELECT m.record_id,m.version_id,r.current_version,r.active,e.revision editorial_revision '
        'FROM aggregate_members m JOIN records r ON r.id=m.record_id '
        'LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE m.aggregate_id=? ORDER BY m.record_id',(aid,))]
    if any(not m['active'] or m['version_id']!=m['current_version'] for m in members):
        raise ValueError('Aggregate membership is stale; rebuild before planning')
    return {'aggregate_id':aid,'anchor_record_id':anchor[0],'members':members}


def snapshot(store,kind,subject,obj=''):
    if kind=='aggregate':return {'aggregate':member_snapshot(store,subject)}
    if kind=='alias':return {'reference':member_snapshot(store,subject),'candidate':member_snapshot(store,obj)}
    if kind=='link':
        row=store.db.execute('SELECT * FROM aggregate_link_evidence WHERE id=?',(obj,)).fetchone()
        if not row:raise ValueError('Link evidence is absent')
        evidence=dict(row)
        return {'evidence':evidence,'source':member_snapshot(store,evidence['subject_id']),
                'target':member_snapshot(store,evidence['object_id'])}
    raise ValueError('Unknown native curation job kind')


def fresh_alias_pairs(store):
    """Recompute candidates from declared aliases, never old model verdicts."""
    primary=defaultdict(set);aggregates={}
    for row in store.db.execute('SELECT a.id,a.kind,a.universe,m.match_key,s.section FROM aggregates a '
        'JOIN aggregate_members m ON m.aggregate_id=a.id JOIN record_sections s ON s.record_id=m.record_id'):
        primary[(row['section'],row['match_key'])].add(row['id'])
        aggregates[row['id']]={'kind':row['kind'],'universe':row['universe']}
    generic={'starship','ship','vaisseau','creature','alien','human','droid','weapon','blaster','rebel','imperial','captain','pilot','technician','unknown'}
    seen=set()
    for row in store.db.execute('SELECT m.aggregate_id,v.kind,v.aliases_json,s.section FROM aggregate_members m '
        'JOIN record_versions v ON v.id=m.version_id JOIN record_sections s ON s.record_id=m.record_id'):
        source=row['aggregate_id'];a=aggregates[source]
        for alias in json.loads(row['aliases_json']):
            form=name_key(alias,row['kind'])
            if len(form)<5 or form in generic:continue
            for target in primary.get((row['section'],form),()):
                b=aggregates[target]
                if source==target or a['kind']!=b['kind']:continue
                if a['universe']!=b['universe'] and {a['universe'],b['universe']}-{'unknown','star_wars'}:continue
                pair=tuple(sorted((source,target)))
                if pair not in seen:seen.add(pair);yield pair


def plan(store,model_identity,*,aggregate_ids=None,include_aliases=False,include_links=False,limit=0):
    """Persist inputs, not verdicts. limit=0 means every candidate in scope."""
    if not isinstance(model_identity,dict) or not model_identity:raise ValueError('Effective model identity is required')
    if limit<0:raise ValueError('Invalid plan limit')
    initialize(store)
    run='jev-v1-'+str(uuid.uuid4());created=now();counts=Counter();total=0
    store.db.execute('INSERT INTO jev_v1_runs VALUES(?,?,?,?,?,?,?,?)',
        (run,VERSION,PLANNER,dumps(model_identity),active_run(store),created,'planning','{}'))
    def add(kind,subject,obj=''):
        nonlocal total
        if limit and total>=limit:return False
        value=snapshot(store,kind,subject,obj);hashed=fingerprint(value)
        jid='jev-job-'+fingerprint([run,kind,subject,obj])
        store.db.execute('INSERT INTO jev_v1_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (jid,run,kind,subject,obj,dumps(value),hashed,'planned',None,None,now()))
        counts[kind]+=1;total+=1
        return True
    # A single multi-document job owns a deterministic, progressive loop. Its
    # eventual number of questions depends on the sources actually retained.
    if aggregate_ids is None:
        ids=(row[0] for row in store.db.execute('SELECT aggregate_id FROM aggregate_members '
             'GROUP BY aggregate_id HAVING count(*)>1 ORDER BY aggregate_id'))
    else:ids=sorted(set(aggregate_ids))
    for aid in ids:
        if not add('aggregate',aid):break
    if include_aliases:
        for a,b in fresh_alias_pairs(store):
            if not add('alias',a,b):break
    if include_links:
        # Only explicitly ambiguous lexical/structured evidence; ordinary links
        # and already useful native source navigation need no model question.
        for row in store.db.execute("SELECT id,subject_id FROM aggregate_link_evidence WHERE "
            "rule IN ('exact_alias','structured_field') AND json_extract(context_json,'$.ambiguous_alias')=1 ORDER BY rowid"):
            if not add('link',row['subject_id'],row['id']):break
    metrics={'jobs':total,'by_kind':dict(counts),'model_calls':0,'limit':limit,
             'complete_scope':not limit or total<limit,'legacy_decisions_reused':False,
             'question_count':'dynamic: cumulative retained coverage and exact token budget must be applied'}
    store.db.execute("UPDATE jev_v1_runs SET status='planned',metrics_json=? WHERE id=?",(dumps(metrics),run))
    store.db.commit()
    return {'run_id':run,**metrics}


def revise_plan(store,previous_run):
    """Keep old selections archived; reuse unchanged inputs and native logits only."""
    initialize(store)
    previous=store.db.execute('SELECT * FROM jev_v1_runs WHERE id=?',(previous_run,)).fetchone()
    if not previous or previous['cleaning_run']!=active_run(store):raise ValueError('Prior plan is absent or stale')
    run='jev-v1-'+str(uuid.uuid4());metrics=json.loads(previous['metrics_json'])
    metrics.update(previous_run=previous_run,redundancy_threshold_pct=80,redundancy_comparison='strictly_greater',
                   prior_selections_reused=False,native_probabilities_reusable_only_for_identical_requests=True)
    store.db.execute('INSERT INTO jev_v1_runs VALUES(?,?,?,?,?,?,?,?)',
        (run,VERSION,PLANNER,previous['model_identity_json'],previous['cleaning_run'],now(),'prepared',dumps(metrics)))
    # Preserve deterministic ordering for a measured before/after policy change.
    for row in store.db.execute('SELECT * FROM jev_v1_jobs WHERE run_id=?',(previous_run,)).fetchall():
        jid=row['id']+'-r80-'+run[-8:]
        store.db.execute('INSERT INTO jev_v1_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (jid,run,row['kind'],row['subject_id'],row['object_id'],row['snapshot_json'],row['input_hash'],'prepared',None,None,now()))
        store.db.execute('INSERT INTO jev_v1_previews SELECT ?,step,tasks_json,request_json,tokens_json,deferred_reason FROM jev_v1_previews WHERE job_id=?',(jid,row['id']))
    store.db.commit()
    return run


def document(store,rid):
    row=store.db.execute('SELECT r.id,r.current_version,v.name,v.body,v.kind,v.origin,v.scope_json,v.locator_json,'
        'p.stats_json,e.revision editorial_revision FROM records r JOIN record_versions v ON v.id=r.current_version '
        'LEFT JOIN profiles p ON p.version_id=v.id LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.id=? AND r.active=1',(rid,)).fetchone()
    if row is None:raise ValueError('Source is absent or inactive')
    scope=json.loads(row['scope_json']);locator=json.loads(row['locator_json'])
    # Original payload/HTML is evidence in SQLite, not repeated as prompt chrome.
    return {'id':row['id'],'version_id':row['current_version'],'name':row['name'],'body':row['body'],
            'kind':row['kind'],'origin':row['origin'],'scope':scope,
            'unit':locator.get('unit',{}),'profile':normalize_profile(row['stats_json']),
            'preserve_reasons':['personal_overlay'] if row['editorial_revision'] is not None else [],
            'context_complete':True}


def entity(store,aid):
    row=store.db.execute('SELECT id,name,kind FROM aggregates WHERE id=?',(aid,)).fetchone()
    if not row:raise ValueError('Aggregate is absent')
    return dict(row)


def ordered_sources(store,value):
    reference=document(store,value['anchor_record_id'])
    others=[member for member in value['members'] if member['record_id']!=reference['id']]
    return reference,others


def prepare_source(source,reference,retained):
    concrete=lambda kind:kind not in {'unknown','mixed_document','concept'}
    if concrete(source['kind']) and concrete(reference['kind']) and source['kind']!=reference['kind']:
        source['identity_ambiguous']=True
    source['preserve_reasons'].extend(preserve_reasons(source['scope'],[doc.get('scope',{}) for doc in retained]))
    return source


def prepare_documents(store,run_id,*,preflight=None,model=None,limit=0,progress=None):
    """Algorithmic selection draft and exact token census. Zero model calls.

    Pending contribution questions simulate unknown, so their sources remain
    provisionally retained. Their actual future outcomes can change the next
    request. Previews are deliberately NOT inserted into the inference cache.
    """
    counts=Counter();tokens=Counter();by_reason=Counter();started=time.perf_counter()
    rows=store.db.execute("SELECT * FROM jev_v1_jobs WHERE run_id=? AND kind='aggregate' AND status IN ('planned','prepared') ORDER BY id LIMIT ?",(run_id,limit or -1)).fetchall()
    for job in rows:
        current=snapshot(store,'aggregate',job['subject_id'])
        if fingerprint(current)!=job['input_hash']:raise ValueError('Preparation inputs changed; replan')
        previews=[]
        def preview(request):
            question_tokens={};reason='awaiting_jev'
            if preflight:
                try:question_tokens=preflight(request)
                except Exception as error:
                    if getattr(error,'reason',None)!='context_budget':raise
                    question_tokens=error.token_counts;reason='token_budget_exceeded'
            counts['first_pass_requests']+=1
            for task in request['questions']:counts['question_'+task]+=1
            counts['token_limited_requests']+=int(reason=='token_budget_exceeded')
            for number in question_tokens.values():
                tokens['questions_counted']+=1;tokens['input_tokens']+=number
                tokens['max_question_tokens']=max(tokens['max_question_tokens'],number)
                tokens['within_2048']+=int(number<=2048)
            previews.append((len(previews)+1,list(request['questions']),request,question_tokens,reason))
            return {'deferred_reason':reason}
        reference,others=ordered_sources(store,current['aggregate'])
        loop=DocumentCoverageLoop(entity(store,job['subject_id']),reference,preview,model=model)
        for member in others:
            source=prepare_source(document(store,member['record_id']),reference,loop.retained)
            result=loop.source(source);by_reason[result['reason']]+=1
            counts['candidate_documents']+=1;counts['candidate_units_gross_ceiling']+=result['unit_count']
            counts[result['merge_selection']]+=1
        result=loop.plan();result.update(preview_only=True,model_calls=0,legacy_decisions_reused=False,
            estimate_note='First-pass requests with pending results treated as unknown; actual cumulative coverage will be recomputed after each decision.')
        store.db.execute('DELETE FROM jev_v1_previews WHERE job_id=?',(job['id'],))
        store.db.executemany('INSERT INTO jev_v1_previews VALUES(?,?,?,?,?,?)',
            [(job['id'],step,dumps(tasks),dumps(request),dumps(values),reason) for step,tasks,request,values,reason in previews])
        store.db.execute("UPDATE jev_v1_jobs SET status='prepared',result_json=?,updated_at=? WHERE id=?",(dumps(result),now(),job['id']));store.db.commit()
        counts['aggregates_prepared']+=1
        if progress and counts['aggregates_prepared']%250==0:progress({**counts,'model_calls':0,'seconds':round(time.perf_counter()-started,1)})
    metrics={'preparation':dict(counts),'by_reason':dict(by_reason),'tokens':dict(tokens),'model_calls':0,
             'seconds':round(time.perf_counter()-started,2),'semantic_decisions_made':False,
             'legacy_decisions_reused':False,'coverage_is_provisional':True}
    old=store.db.execute('SELECT metrics_json FROM jev_v1_runs WHERE id=?',(run_id,)).fetchone()
    if not old:raise KeyError(run_id)
    value=json.loads(old[0]);value['document_preparation']=metrics
    store.db.execute("UPDATE jev_v1_runs SET status='prepared',metrics_json=? WHERE id=?",(dumps(value),run_id));store.db.commit()
    return metrics


class CachedEvaluator:
    """Namespace includes effective weights/runtime/template AND the full request."""
    def __init__(self,store,job_id,identity,evaluate):
        self.store,self.job_id,self.identity,self.evaluate=store,job_id,identity,evaluate
        self.step=0;self.calls=0;self.cache_hits=0

    def __call__(self,request):
        key='jev-v1-request-'+fingerprint({'policy':VERSION,'prompts':PROMPTS,'model_identity':self.identity,'request':request})
        db=self.store.db;self.step+=1
        cached=db.execute("SELECT response_json FROM jev_v1_requests WHERE id=? AND status IN ('answered','deferred')",(key,)).fetchone()
        if cached:
            validator=getattr(self.evaluate,'validate_runtime',None) or getattr(getattr(self.evaluate,'__self__',None),'validate_runtime',None)
            if validator:validator()
            response=json.loads(cached[0]);self.cache_hits+=1
        else:
            db.execute('INSERT OR REPLACE INTO jev_v1_requests VALUES(?,?,?,?,?,?,?,?,?)',
                (key,VERSION,dumps(self.identity),dumps(request),'running',None,None,None,now()))
            db.commit();started=time.perf_counter()
            try:
                response=self.evaluate(request)
                if not response.get('deferred_reason'):
                    if set(response.get('answers',{}))!=set(request['questions']):raise ValueError('Native response question IDs differ')
                    for answer in response['answers'].values():probabilities(answer)
            except Exception as error:
                db.execute("UPDATE jev_v1_requests SET status='error',error=?,latency_ms=? WHERE id=?",
                    (str(error)[:2000],1000*(time.perf_counter()-started),key));db.commit();raise
            self.calls+=int(not response.get('deferred_reason'))
            db.execute('UPDATE jev_v1_requests SET status=?,response_json=?,error=NULL,latency_ms=? WHERE id=?',
                ('deferred' if response.get('deferred_reason') else 'answered',dumps(response),1000*(time.perf_counter()-started),key))
        db.execute('INSERT OR REPLACE INTO jev_v1_job_requests VALUES(?,?,?)',(self.job_id,self.step,key));db.commit()
        return response


def execute(store,job_id,evaluate,model_identity,*,model=None,check_pause=True):
    """Resume one job. Cache replay rebuilds cumulative coverage in the same order."""
    if check_pause and (store.path.parent/'openjev.pause').exists():raise ValueError('Curation is paused; leave it paused until the cost review')
    row=store.db.execute('SELECT j.*,r.model_identity_json,r.cleaning_run,r.planner FROM jev_v1_jobs j '
        'JOIN jev_v1_runs r ON r.id=j.run_id WHERE j.id=?',(job_id,)).fetchone()
    if not row:raise KeyError(job_id)
    if row['planner']!=PLANNER:raise ValueError('Planner revision changed; create a fresh native plan')
    if json.loads(row['model_identity_json'])!=model_identity:raise ValueError('Effective runtime identity differs from the planned model')
    validator=getattr(evaluate,'validate_runtime',None) or getattr(getattr(evaluate,'__self__',None),'validate_runtime',None)
    if validator:validator()
    current=snapshot(store,row['kind'],row['subject_id'],row['object_id'])
    if active_run(store)!=row['cleaning_run'] or fingerprint(current)!=row['input_hash']:
        store.db.execute("UPDATE jev_v1_jobs SET status='stale',error='Input revision changed',updated_at=? WHERE id=?",(now(),job_id));store.db.commit()
        raise ValueError('Job inputs are stale; replan from the current documents')
    if row['status']=='complete':return json.loads(row['result_json'])
    evaluator=CachedEvaluator(store,job_id,model_identity,evaluate)
    store.db.execute("UPDATE jev_v1_jobs SET status='running',error=NULL,updated_at=? WHERE id=?",(now(),job_id));store.db.commit()
    try:
        if row['kind']=='aggregate':
            reference,others=ordered_sources(store,current['aggregate'])
            loop=DocumentCoverageLoop(entity(store,row['subject_id']),reference,evaluator,model=model)
            for member in others:
                source=prepare_source(document(store,member['record_id']),reference,loop.retained)
                loop.source(source)
            result=loop.plan()
        elif row['kind']=='alias':
            reference=document(store,current['reference']['anchor_record_id'])
            source=document(store,current['candidate']['anchor_record_id']);source['identity_ambiguous']=True
            loop=DisambiguationLoop(entity(store,row['subject_id']),reference,evaluator,model=model)
            scores=loop.ask({'ENTITY':entity(store,row['subject_id']),'REFERENCE':reference,'SOURCE':source},['entity_match']).get('entity_match',{})
            result={'policy':VERSION,'entity_match_score_pct':scores.get('yes'),'scores_pct':scores,
                    'correction_proposal':'same_entity' if scores.get('yes',0)>=95 else 'different_entity' if scores.get('no',0)>=95 else 'unresolved',
                    'observations':loop.observations,'memberships_modified':False}
        else:
            evidence=current['evidence'];source=document(store,evidence['record_id'])
            excerpt=evidence['excerpt'];offset=evidence['offset_start']
            start=max(0,offset-160) if offset is not None else source['body'].find(excerpt)
            if start<0 or source['body'][start:start+len(excerpt)]!=excerpt:
                result={'selection':'unresolved','reason':'passage_not_verified_in_source_version','model_calls':0}
            else:
                target=document(store,current['target']['anchor_record_id'])
                link={'id':evidence['id'],'needs_review':True,'subject':{'id':source['id'],'name':source['name'],'kind':source['kind']},
                      'target':target,'predicate':'lexical_reference' if evidence['kind']=='mentions' else evidence['kind'],
                      'original_context':json.loads(evidence['context_json'])}
                passage={'body':excerpt,'record_id':source['id'],'version_id':source['version_id'],
                         'offset_start':start,'offset_end':start+len(excerpt),'unit':source['unit'],'context_complete':True}
                loop=DisambiguationLoop(entity(store,row['subject_id']),source,evaluator,model=model)
                result=loop.link(link,passage);result['observations']=loop.observations
        # An inference may run while another application edits the documents.
        # Publish no selection if any version, membership or editorial revision changed.
        if active_run(store)!=row['cleaning_run'] or fingerprint(snapshot(store,row['kind'],row['subject_id'],row['object_id']))!=row['input_hash']:
            raise ValueError('Input revision changed during inference; selection not published')
        result.update(model_calls=evaluator.calls,cache_hits=evaluator.cache_hits,model_identity=model_identity,
                      originals_modified=False,selection_persisted=True,legacy_decisions_reused=False)
        store.db.execute("UPDATE jev_v1_jobs SET status='complete',result_json=?,updated_at=? WHERE id=?",(dumps(result),now(),job_id));store.db.commit()
        pending=store.db.execute("SELECT count(*) FROM jev_v1_jobs WHERE run_id=? AND status!='complete'",(row['run_id'],)).fetchone()[0]
        store.db.execute('UPDATE jev_v1_runs SET status=? WHERE id=?',('partially_complete' if pending else 'complete',row['run_id']));store.db.commit()
        return result
    except Exception as error:
        store.db.execute("UPDATE jev_v1_jobs SET status='error',error=?,updated_at=? WHERE id=?",(str(error)[:2000],now(),job_id));store.db.commit();raise


def status(store,run_id=None):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_v1_runs'").fetchone():return {'available':False}
    if run_id:
        row=store.db.execute('SELECT * FROM jev_v1_runs WHERE id=?',(run_id,)).fetchone()
    else:row=store.db.execute('SELECT * FROM jev_v1_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row:return {'available':True,'run':None}
    value=dict(row)
    value['model_identity']=json.loads(value.pop('model_identity_json'));value['metrics']=json.loads(value.pop('metrics_json'))
    value['jobs']=[dict(r) for r in store.db.execute('SELECT kind,status,count(*) count FROM jev_v1_jobs WHERE run_id=? GROUP BY kind,status',(row['id'],))]
    return {'available':True,'run':value,'paused':(store.path.parent/'openjev.pause').exists(),'legacy_decisions_reused':False}
