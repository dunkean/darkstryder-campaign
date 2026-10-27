"""Verify acquired public snapshots, import, wire and clean without model calls."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import time
from functools import lru_cache

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'packages'))
from sw_kbase.acquire import html_text
from sw_kbase.adapters import ingest_all, web_snapshots
from sw_kbase.audit import audit
from sw_kbase.cleaning import clean
from sw_kbase.public_sites import links
from sw_kbase.store import Store, process_lock, confined, dumps
from sw_kbase.wiring import wire

SOURCES={'opend6-srd','rpggamer-d6','d6db-compendium','d6db-library',
         'seawolf-star-wars','seawolf-atlas','seawolf-files'}


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def normalize_d6db(path):
    """Remove page chrome from searchable text; retain exact HTTP evidence."""
    import re
    backup=path.with_name(path.stem+'.before-chrome-cleaning.jsonl')
    if not backup.exists():
        import shutil
        shutil.copy2(path,backup)
    temporary=path.with_suffix('.cleaning.tmp')
    count=0
    with path.open(encoding='utf-8') as source,temporary.open('w',encoding='utf-8') as target:
        for line in source:
            row=json.loads(line);payload=row['payload']
            if payload.get('searchable_article_version')!=1:
                root=path.parent.parent
                data=confined(root,row['receipt']['path']).read_bytes()
                if sha256(data).hexdigest()!=row['receipt']['sha256']:raise ValueError('D6DB response changed')
                raw=data.decode('utf-8')
                start=raw.find('<h1')
                footer=re.search(r'<footer\b[^>]*class="[^"]*\bmt-auto\b',raw)
                if start<0 or footer is None or footer.start()<=start:
                    raise ValueError('D6DB article boundaries missing: '+row['key'])
                end=footer.start();fragment=raw[start:end]
                body=html_text(fragment)
                if payload.get('source_data'):
                    body+='\n\n## Structured data supplied by the public page\n\n'+json.dumps(payload['source_data'],ensure_ascii=False,indent=2)
                row['body']=body
                payload.update(html=fragment,html_sha256=sha256(fragment.encode()).hexdigest(),
                               markdown_sha256=sha256(body.encode()).hexdigest(),
                               outgoing_urls=sorted(links(fragment,row['url'])),searchable_article_version=1)
                row['unit'].update(offset_start=start,offset_end=end,offset_unit='characters',
                                   html_sha256=payload['html_sha256'],text_sha256=payload['markdown_sha256'])
            target.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1
    temporary.replace(path)
    return count


def verify(external,progress,source_names=SOURCES):
    checked=set();statuses=[];rows=0;byte_count=0
    snapshots=[path for path in web_snapshots(external) if path.parent.name in source_names]
    if {path.parent.name for path in snapshots}!=source_names:raise ValueError('A required acquired source is absent')
    @lru_cache(maxsize=16)
    def read_response(path):
        return path.read_bytes()
    for path in snapshots:
        status=json.loads((path.parent/'status.json').read_text(encoding='utf-8'))
        seen=set();count=0
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                row=json.loads(line);count+=1;rows+=1
                if row['key'] in seen:raise ValueError('Duplicate native key: '+row['key'])
                seen.add(row['key'])
                receipt=row['receipt'];response=confined(path.parent.parent,receipt['path'])
                if receipt['status']!=200 or not receipt.get('tls_verified'):raise ValueError('Unverified HTTP response')
                key=(str(response),receipt['sha256'])
                data=read_response(response)
                if key not in checked:
                    if sha256(data).hexdigest()!=receipt['sha256']:raise ValueError('Response hash mismatch: '+str(response))
                    checked.add(key);byte_count+=len(data)
                unit=row.get('unit',{})
                for field in ['markdown_sha256','text_sha256']:
                    if unit.get(field) and sha256(row['body'].encode()).hexdigest()!=unit[field]:
                        raise ValueError('Searchable source text hash mismatch: '+row['key'])
                if 'offset_start' in unit and unit.get('html_sha256'):
                    raw=data.decode('utf-8-sig')
                    fragment=raw[unit['offset_start']:unit['offset_end']]
                    if sha256(fragment.encode()).hexdigest()!=unit['html_sha256']:
                        raise ValueError('Exact source fragment mismatch: '+row['key'])
                if unit.get('kind')=='wordpress_object':
                    objects=json.loads(data.decode('utf-8-sig'))
                    original=next(value for value in objects if value['id']==unit['native_id'])
                    html=original.get('content',{}).get('rendered','')
                    if sha256(html.encode()).hexdigest()!=unit['html_sha256']:raise ValueError('WordPress source field mismatch')
                if rows%2000==0:progress('verifying_sources',records=rows,responses=len(checked))
        if count!=status['records']:raise ValueError('Snapshot record count mismatch: '+path.parent.name)
        statuses.append(status)
    # Check index/search/license responses too, not only files referenced by
    # admitted records. A successful response is never assumed to be a record.
    from sw_kbase.acquire import Snapshot
    for root in {path.parent.parent for path in snapshots}:
        for receipt in Snapshot(root).receipts.values():
            response=confined(root,receipt['path']);key=(str(response),receipt['sha256'])
            if key in checked:continue
            data=read_response(response)
            if sha256(data).hexdigest()!=receipt['sha256']:raise ValueError('Index/cache response hash mismatch')
            checked.add(key);byte_count+=len(data)
    return {'sources':statuses,'verified_records':rows,'verified_responses':len(checked),'downloaded_bytes':byte_count}


def archive_totals(external):
    """Count distinct checked responses across the current and older snapshots."""
    from sw_kbase.acquire import Snapshot
    checked={}
    for root in {path.parent.parent for path in web_snapshots(external)}:
        for receipt in Snapshot(root).receipts.values():
            key=(receipt['url'],receipt['sha256'])
            if key in checked:continue
            data=confined(root,receipt['path']).read_bytes()
            if sha256(data).hexdigest()!=receipt['sha256']:raise ValueError('Archived response hash mismatch')
            checked[key]=len(data)
    return {'verified_responses':len(checked),'downloaded_bytes':sum(checked.values())}


def preservation(db,backup):
    # ATTACH only understands file: URIs when the parent connection was opened
    # with uri=True. Store's writer uses a plain path. Open an independent,
    # explicitly read-only connection for both databases after the commit.
    if db.in_transaction:raise ValueError('Commit the import before checking preservation')
    main_path=next(row[2] for row in db.execute('PRAGMA database_list') if row[1]=='main')
    check=sqlite3.connect(Path(main_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        check.execute('ATTACH DATABASE ? AS previous',(backup.resolve().as_uri()+'?mode=ro',))
        failures={}
        tables=['records','record_versions','editorial','decision_history','applied_decisions','cannot_links','resolutions','annotations','proposals']
        for table in tables:
            info=list(check.execute('PRAGMA previous.table_info('+table+')'))
            columns=[row[1] for row in info]
            primary=[row[1] for row in sorted(info,key=lambda row:row[5]) if row[5]]
            if not primary:raise ValueError('Preservation comparison requires a primary key: '+table)
            predicate=' AND '.join('current."'+column+'" IS original."'+column+'"' for column in primary+ [c for c in columns if c not in primary])
            scope="original.status='accepted' AND " if table=='proposals' else ''
            count=check.execute('SELECT count(*) FROM previous.'+table+' original WHERE '+scope+
                'NOT EXISTS (SELECT 1 FROM main.'+table+' current WHERE '+predicate+')').fetchone()[0]
            if count:failures['accepted_proposals' if table=='proposals' else table]=count
        return {'ok':not failures,'differences':failures,'baseline_records':check.execute('SELECT count(*) FROM previous.records').fetchone()[0]}
    finally:check.close()


def main(args):
    args.output.mkdir(parents=True,exist_ok=True)
    def progress(stage,**fields):
        value={'stage':stage,'utc':datetime.now(timezone.utc).isoformat(),'model_calls':0,**fields}
        write(args.output/'progress.json',value)
        print(json.dumps({k:v for k,v in value.items() if k not in {'before','after','changes'}},ensure_ascii=False),flush=True)
    if args.wait_acquisition:
        progress('waiting_for_acquisition')
        while True:
            try:
                result=json.loads(args.wait_acquisition.read_text(encoding='utf-8'))
                if {row['source'] for row in result} >= {'rpggamer-d6','d6db-compendium'}:
                    if any(row.get('status')=='failed' for row in result):raise ValueError('A required acquisition failed')
                    break
            except FileNotFoundError:pass
            time.sleep(10)
    if not args.backup.is_file() or args.backup.resolve()==args.database.resolve():raise ValueError('Separate pre-import SQLite backup required')
    if not (args.database.parent/'openjev.pause').exists():raise ValueError('Keep OpenJev paused during this import')
    if args.finalize_only:
        verification=json.loads((args.output/'source-verification.json').read_text(encoding='utf-8'))
    else:
        path=next(path for path in web_snapshots(args.external_root) if path.parent.name=='d6db-compendium')
        progress('cleaning_source_chrome')
        normalize_d6db(path)
        verification=verify(args.external_root,progress);write(args.output/'source-verification.json',verification)
    with process_lock(args.database.parent):
        store=Store(args.database)
        try:
            if args.finalize_only:
                imported=json.loads((args.output/'import.json').read_text(encoding='utf-8'))
                wired=json.loads((args.output/'wiring.json').read_text(encoding='utf-8'))
                report=json.loads((args.output/'cleaning.json').read_text(encoding='utf-8'))
                run=store.db.execute("SELECT value FROM meta WHERE key='active_cleaning_run'").fetchone()
                if not run or run[0]!=report['run_id']:raise ValueError('Saved report does not match the committed cleaning run')
            else:
                progress('importing')
                imported=ingest_all(store,PROJECT,args.external_root,['web']);write(args.output/'import.json',imported)
                progress('wiring_native_references')
                wired=wire(store,args.external_root);write(args.output/'wiring.json',wired)
                report=clean(store,progress);write(args.output/'cleaning.json',report)
            progress('checking_preservation')
            preserved=preservation(store.db,args.backup);write(args.output/'preservation.json',preserved)
            if not preserved['ok']:raise ValueError('Baseline preservation check failed: '+dumps(preserved))
            progress('auditing_database')
            checked=audit(store);write(args.output/'database-audit.json',checked)
            if not checked['ok']:raise ValueError('SQLite/hash/foreign-key audit failed')
            missing=store.db.execute('SELECT count(*) FROM records r LEFT JOIN aggregate_members m ON m.record_id=r.id WHERE r.active=1 AND (m.record_id IS NULL OR m.version_id!=r.current_version)').fetchone()[0]
            if missing:raise ValueError('Current records missing from the rebuilt aggregates')
            coverage=[]
            for path in web_snapshots(args.external_root):
                status=json.loads((path.parent/'status.json').read_text(encoding='utf-8'));status['imported_records']=status['records'];coverage.append(status)
            # Preserve explicit unacquired sites rather than reporting successful
            # proxy transport as a complete acquisition of their documents.
            old=store.db.execute("SELECT value FROM meta WHERE key='acquisition_summary'").fetchone()
            historical=json.loads(old[0]) if old else {}
            actual={row['source'] for row in coverage}
            replaced={'rpggamer','seawolf-darkstryder','seawolf-astrogation','d6db','rancor-pit'}
            coverage.extend(row for row in historical.get('sources',[]) if row.get('source') not in actual|replaced)
            coverage.append({'source':'rancor-pit','status':'robots_disallowed','imported_records':0,
                             'url':'https://rancorpit.com/forums/downloads/','reason':'robots.txt: User-agent: * Disallow: /; the explicit exception applies to D6DB only'})
            summary={'captured_utc':datetime.now(timezone.utc).isoformat(),**verification,'sources':coverage,
                     **archive_totals(args.external_root),
                     'model_calls':0,'strata_started':False,'source_count_is_not_independence':True}
            store.db.execute("INSERT OR REPLACE INTO meta VALUES('acquisition_summary',?)",(dumps(summary),));store.db.commit()
            final={'import':imported,'wiring':wired,'cleaning':report,'preservation':preserved,
                   'database_audit_ok':True,'source_verification':{key:value for key,value in verification.items() if key!='sources'},
                   'model_calls':0,'strata_started':False,'openjev_pause_preserved':True}
            write(args.output/'result.json',final)
            progress('complete',records=report['records'],aggregates=report['aggregates'],remaining_questions=report['remaining_questions'])
        finally:store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--external-root',type=Path,required=True)
    p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--wait-acquisition',type=Path)
    p.add_argument('--finalize-only',action='store_true',help='Resume audits of an already committed import; do not import or rebuild')
    args=p.parse_args()
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main(args)
