"""Second pass: numbered paragraphs to add from retained documents.

Planning reads a completed native document run. Execution is a separate explicit
action; it never runs automatically after document curation or rewrites sources.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .curation_store import document,entity,prepare_source
from .disambiguation import fingerprint,probabilities
from .document_curation import DocumentCoverageLoop,VERSION_UNITS,NUMBERS
from .store import Store,dumps

VERSION='paragraph-additions/1-strict-gt80'


def fact_numbers(text):
    # Page citations are provenance, not additional stats. Keep the exact text
    # in the request/export; only this text-mining signal ignores their numbers.
    value=re.sub(r'\((?:p\.|pp\.|pages?)\s*\d[\d\s,;–—-]*\)', '',text,flags=re.I)
    return {n.casefold().replace(' ','') for n in NUMBERS.findall(value)}
DDL='''
CREATE TABLE meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE entities(id TEXT PRIMARY KEY,anchor_id TEXT NOT NULL,source_order_json TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE documents(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL,version_id TEXT NOT NULL,body_sha256 TEXT NOT NULL,
 revision_sha256 TEXT NOT NULL,role TEXT NOT NULL,metadata_json TEXT NOT NULL);
CREATE TABLE paragraphs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),number INTEGER NOT NULL,
 label TEXT NOT NULL,offset_start INTEGER NOT NULL,offset_end INTEGER NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,
 UNIQUE(document_id,number));
CREATE TABLE requests(id TEXT PRIMARY KEY,request_json TEXT NOT NULL,identity_json TEXT NOT NULL,response_json TEXT NOT NULL,latency_ms REAL NOT NULL);
'''


def paragraphs(body):
    """Number true blank-line paragraphs; never discard or silently clip text."""
    start=0;number=0
    for end in [m.end() for m in re.finditer(r'\n[ \t]*\n',body)]+[len(body)]:
        raw=body[start:end];a=start+len(raw)-len(raw.lstrip());b=end-(len(raw)-len(raw.rstrip()))
        if a<b:
            number+=1
            yield {'number':number,'label':f'P{number:05d}','offset_start':a,'offset_end':b,'body':body[a:b]}
        start=end


def plan(main,directory,run_id):
    run=main.db.execute('SELECT status,planner FROM jev_v1_runs WHERE id=?',(run_id,)).fetchone()
    if not run or run['status']!='complete':raise ValueError('Finish the document curation run before preparing paragraph work')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    db=sqlite3.connect(directory/'paragraphs.sqlite');db.row_factory=sqlite3.Row
    counts=Counter()
    try:
        db.execute('PRAGMA foreign_keys=ON');db.executescript(DDL)
        for key,value in {'policy':VERSION,'coverage_policy':VERSION_UNITS,'document_run_id':run_id,
                          'cleaning_run':main.db.execute("SELECT value FROM meta WHERE key='active_cleaning_run'").fetchone()[0],
                          'main_database':str(main.path.resolve()),'state':'planned','inference_started':False}.items():
            db.execute('INSERT INTO meta VALUES(?,?)',(key,dumps(value)))
        for job in main.db.execute("SELECT subject_id,result_json FROM jev_v1_jobs WHERE run_id=? AND kind='aggregate' AND status='complete' ORDER BY id",(run_id,)):
            result=json.loads(job['result_json'])
            if result.get('preview_only'):raise ValueError('A document job only contains a provisional preview')
            retained=result['retained_ids'];anchor=result['reference_id']
            if not retained or retained[0]!=anchor:raise ValueError('Explicit stable reference is required')
            db.execute('INSERT INTO entities VALUES(?,?,?,?)',(job['subject_id'],anchor,dumps(retained),'planned'))
            for rid in retained:
                source=document(main,rid)
                revision=main.db.execute('SELECT sha256 FROM record_versions WHERE id=?',(source['version_id'],)).fetchone()[0]
                metadata={key:value for key,value in source.items() if key!='body'}
                metadata['source_id']=main.db.execute('SELECT source_id FROM records WHERE id=?',(rid,)).fetchone()[0]
                editorial=main.db.execute('SELECT e.revision FROM records r LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.id=?',(rid,)).fetchone()
                metadata['editorial_revision']=editorial[0]
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',
                    (rid,job['subject_id'],source['version_id'],hashlib.sha256(source['body'].encode()).hexdigest(),revision,
                     'reference' if rid==anchor else 'candidate',dumps(metadata)))
                for paragraph in paragraphs(source['body']):
                    pid='para-'+fingerprint([rid,source['version_id'],paragraph['offset_start'],paragraph['offset_end']])
                    db.execute('INSERT INTO paragraphs VALUES(?,?,?,?,?,?,?,?,?)',
                        (pid,rid,paragraph['number'],paragraph['label'],paragraph['offset_start'],paragraph['offset_end'],paragraph['body'],
                         'reference' if rid==anchor else 'pending',None))
                    counts['paragraphs']+=1
                counts['documents']+=1
            counts['entities']+=1
        db.execute('INSERT INTO meta VALUES(?,?)',('counts',dumps(dict(counts))));db.commit()
        summary={'policy':VERSION,'document_run_id':run_id,**counts,'model_calls':0,'state':'planned'}
        (directory/'plan.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        return summary
    finally:db.close()


def verified_document(main,row):
    source=document(main,row['id'])
    revision=main.db.execute('SELECT sha256 FROM record_versions WHERE id=?',(source['version_id'],)).fetchone()[0]
    editorial=main.db.execute('SELECT e.revision FROM records r LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.id=?',(row['id'],)).fetchone()[0]
    if (editorial!=json.loads(row['metadata_json']).get('editorial_revision') or
        source['version_id']!=row['version_id'] or revision!=row['revision_sha256'] or
        hashlib.sha256(source['body'].encode()).hexdigest()!=row['body_sha256']):raise ValueError('Source revision changed; replan paragraphs')
    return source


def metadata_only(text,name):
    value=text.strip()
    if re.fullmatch(r'(?:<<\s*)?(?:previous|next)\s+page(?:\s*>>)?',value,re.I):return True
    if value.casefold()=='## structured data supplied by the public page':return True
    try:parsed=json.loads(value)
    except (ValueError,TypeError):return False
    return isinstance(parsed,dict) and set(parsed)=={'value'} and str(parsed['value']).casefold()==name.casefold()


def verify_snapshot(main,db):
    """Also check already completed entities before a resume or final export."""
    for row in db.execute('SELECT id,version_id,revision_sha256,metadata_json FROM documents'):
        current=main.db.execute('SELECT r.current_version,v.sha256,e.revision FROM records r '
            'JOIN record_versions v ON v.id=r.current_version LEFT JOIN editorial e ON e.entity_id=r.entity_id WHERE r.id=?',(row['id'],)).fetchone()
        expected=(row['version_id'],row['revision_sha256'],json.loads(row['metadata_json']).get('editorial_revision'))
        if not current or tuple(current)!=expected:raise ValueError('Source revision changed; replan paragraphs')


class Cache:
    def __init__(self,db,client,pause):self.db,self.client,self.pause=db,client,pause;self.calls=0;self.hits=0
    def __call__(self,request):
        if self.pause.exists():raise ValueError('Paragraph curation paused')
        key=fingerprint({'policy':VERSION,'coverage':VERSION_UNITS,'identity':self.client.identity,'request':request})
        row=self.db.execute('SELECT response_json FROM requests WHERE id=?',(key,)).fetchone()
        if row:
            self.client.validate_runtime();self.hits+=1
            return json.loads(row[0])
        start=time.perf_counter();response=self.client(request)
        if not response.get('deferred_reason'):
            for answer in response['answers'].values():probabilities(answer)
            self.calls+=1
        self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',
            (key,dumps(request),dumps(self.client.identity),dumps(response),1000*(time.perf_counter()-start)));self.db.commit()
        return response


def run(main,directory,client,*,limit=0):
    """Only called by the explicit run --execute command, never by plan()."""
    directory=Path(directory);db=sqlite3.connect(directory/'paragraphs.sqlite');db.row_factory=sqlite3.Row
    counts=Counter();cache=Cache(db,client,main.path.parent/'openjev.pause')
    try:
        db.execute('PRAGMA foreign_keys=ON')
        meta={r['key']:json.loads(r['value_json']) for r in db.execute('SELECT * FROM meta')}
        if meta['policy']!=VERSION or meta['coverage_policy']!=VERSION_UNITS:raise ValueError('Paragraph policy changed; replan')
        if meta['cleaning_run']!=main.db.execute("SELECT value FROM meta WHERE key='active_cleaning_run'").fetchone()[0]:raise ValueError('Aggregate revision changed; replan paragraphs')
        if meta.get('model_identity') is not None and meta['model_identity']!=client.identity:raise ValueError('Paragraph model identity changed')
        verify_snapshot(main,db)
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('model_identity',dumps(client.identity)))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('inference_started','true'));db.commit()
        for entry in db.execute("SELECT * FROM entities WHERE status!='complete' ORDER BY id LIMIT ?",(limit or -1,)).fetchall():
            if (main.path.parent/'openjev.pause').exists():raise ValueError('Paragraph curation paused')
            order=json.loads(entry['source_order_json'])
            docs={r['id']:r for r in db.execute('SELECT * FROM documents WHERE entity_id=?',(entry['id'],))}
            reference=verified_document(main,docs[entry['anchor_id']])
            loop=DocumentCoverageLoop(entity(main,entry['id']),reference,cache,model=client.model,numeric_terms=fact_numbers)
            for rid in order[1:]:
                source=verified_document(main,docs[rid])
                for paragraph in db.execute('SELECT * FROM paragraphs WHERE document_id=? ORDER BY number',(rid,)).fetchall():
                    if source['body'][paragraph['offset_start']:paragraph['offset_end']]!=paragraph['body']:raise ValueError('Paragraph excerpt changed')
                    if metadata_only(paragraph['body'],source['name']):
                        selection='metadata';result={'reason':'presentation_only','paragraph_label':paragraph['label']}
                    else:
                        candidate={**source,'id':paragraph['id'],'body':paragraph['body'],
                            'preserve_reasons':list(source.get('preserve_reasons',[])),
                            'unit':{'record_id':rid,'version_id':source['version_id'],'paragraph_number':paragraph['label'],
                                    'offset_start':paragraph['offset_start'],'offset_end':paragraph['offset_end'],'parent_unit':source['unit']}}
                        candidate=prepare_source(candidate,reference,loop.retained)
                        # Document membership was already decided by the first
                        # pass. Paragraph selection never reassigns identities.
                        candidate.pop('identity_ambiguous',None)
                        result=loop.source(candidate)
                        selection='skip_redundant' if result['merge_selection']=='exclude_redundant' else 'add'
                        gain=result.get('scores_pct',{}).get('source_contribution',{})
                        if selection=='add' and result['reason']=='information_or_uncertainty_to_preserve' and gain.get('yes',0)<=80:selection='add_uncertain'
                    result.update(document_id=rid,version_id=source['version_id'],paragraph_label=paragraph['label'],
                                  offset_start=paragraph['offset_start'],offset_end=paragraph['offset_end'])
                    db.execute('UPDATE paragraphs SET status=?,result_json=? WHERE id=?',(selection,dumps(result),paragraph['id']));db.commit()
                    counts[selection]+=1
                verified_document(main,docs[rid])
            verified_document(main,docs[entry['anchor_id']])
            db.execute("UPDATE entities SET status='complete' WHERE id=?",(entry['id'],));db.commit();counts['entities']+=1
        remaining=db.execute("SELECT count(*) FROM entities WHERE status!='complete'").fetchone()[0]
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('state',dumps('complete' if not remaining else 'partially_complete')));db.commit()
        verify_snapshot(main,db)
        export(db,directory/'additions.json')
        return {**counts,'model_calls':cache.calls,'cache_hits':cache.hits,'remaining_entities':remaining,'raw_sources_modified':False}
    finally:db.close()


def export(db,target):
    target=Path(target);temporary=target.with_suffix('.tmp')
    # Stream one document at a time: a large corpus must not be accumulated
    # again in RAM merely to write the derived JSON export.
    with temporary.open('w',encoding='utf-8') as stream:
        stream.write('{"policy":'+json.dumps(VERSION)+',"documents":[\n');first=True
        for doc in db.execute('SELECT * FROM documents ORDER BY entity_id,id'):
            metadata=json.loads(doc['metadata_json'])
            item={'document_id':doc['id'],'version_id':doc['version_id'],'entity_id':doc['entity_id'],'role':doc['role'],
                  'source_id':metadata['source_id'],'source_unit':metadata.get('unit',{}),
                  'origin':metadata.get('origin'),'scope':metadata.get('scope',{}),
                  'body_sha256':doc['body_sha256'],'preserve_structured_profile':True,'paragraphs':[]}
            for p in db.execute('SELECT * FROM paragraphs WHERE document_id=? ORDER BY number',(doc['id'],)):
                item['paragraphs'].append({'id':p['id'],'number':p['label'],'selection':p['status'],
                    'offset_start':p['offset_start'],'offset_end':p['offset_end'],'body':p['body'],
                    'evidence':json.loads(p['result_json']) if p['result_json'] else None})
            if not first:stream.write(',\n')
            json.dump(item,stream,ensure_ascii=False,indent=2);first=False
        stream.write('\n],"originals_deleted":false}\n')
    temporary.replace(target)
