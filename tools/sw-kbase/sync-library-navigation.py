"""Connect acquired books, PDF pages and SRD cards to their native parent documents."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.store import Store,process_lock,stable,dumps


def sync(store):
    rows=[dict(row) for row in store.db.execute("SELECT r.id,r.source_key,r.current_version,v.name,"
        "json_extract(v.payload_json,'$.book_slug') book_slug,json_extract(v.payload_json,'$.website_unit') website_unit "
        "FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id "
        "WHERE r.active=1 AND s.dataset='web/d6db-library'")]
    by_key={row['source_key']:row for row in rows}
    members=dict(store.db.execute('SELECT record_id,aggregate_id FROM aggregate_members'))
    affected=set();units=0;new_evidence=0
    def connect(subject,obj,kind,context):
        nonlocal new_evidence
        if subject['id'] not in members or obj['id'] not in members:raise ValueError('Rebuild aggregates before syncing source navigation')
        store.db.execute('INSERT OR REPLACE INTO document_links VALUES(?,?,?,?,?,?)',
            (subject['id'],obj['id'],kind,subject['current_version'],obj['current_version'],dumps(context)))
        source,target=members[subject['id']],members[obj['id']]
        if source==target:return
        affected.add((source,target))
        eid=stable('ev-auto',subject['id'],subject['current_version'],target,'source_reference',None,'native_reference')
        context={'native_kind':kind,'target_record_id':obj['id'],**context}
        new_evidence+=store.db.execute('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
            (eid,source,target,'source_reference',subject['id'],subject['current_version'],None,obj['name'],'native_reference',dumps(context))).rowcount
    for unit in rows:
        if not unit['book_slug']:continue
        parent=by_key.get('/library/'+unit['book_slug']+'#index')
        if not parent:raise ValueError('Reading unit has no acquired book index')
        context={'verification':'declared_native_book_route_and_pagination','book_slug':unit['book_slug'],
                 'website_unit':json.loads(unit['website_unit']) if unit['website_unit'] else {}}
        connect(parent,unit,'library_contains_unit',context);units+=1
    other=[dict(row) for row in store.db.execute("SELECT r.id,r.source_key,r.current_version,v.name,v.payload_json,s.dataset "
        "FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id "
        "WHERE r.active=1 AND s.dataset IN ('web/seawolf-files','web/seawolf-star-wars','web/opend6-srd')")]
    keys={(row['dataset'],row['source_key']):row for row in other}
    pages=set();cards=0
    for row in other:
        payload=json.loads(row['payload_json'])
        if row['dataset']=='web/seawolf-files':
            for parent in payload.get('parent_articles',[]):
                target=keys.get(('web/seawolf-star-wars',str(parent['key'])))
                if not target:raise ValueError('PDF attachment parent article is missing')
                connect(row,target,'attachment_page_of',{'verification':'declared_public_article_attachment',
                        'legacy_urls':payload.get('legacy_urls',[]),'file_name':payload.get('file_name')})
                pages.add(row['id'])
        elif row['dataset']=='web/opend6-srd' and payload.get('category'):
            parent=keys.get(('web/opend6-srd','/srd'))
            if not parent:raise ValueError('SRD index is missing')
            connect(parent,row,'srd_contains_card',{'verification':'declared_srd_category_and_card','category':payload['category']});cards+=1
    for source,target in affected:
        documents,occurrences=store.db.execute("SELECT count(distinct d.subject_record_id),count(*) "
            "FROM aggregate_members a JOIN document_links d ON d.subject_record_id=a.record_id "
            "JOIN aggregate_members b ON b.record_id=d.object_record_id "
            "WHERE a.aggregate_id=? AND b.aggregate_id=? AND d.kind!='profile_component_of'",(source,target)).fetchone()
        store.db.execute('INSERT OR REPLACE INTO aggregate_links VALUES(?,?,?,?,?,?)',
            (source,target,'source_reference',documents,occurrences,'[]'))
    run=store.db.execute("SELECT value FROM meta WHERE key='active_cleaning_run'").fetchone()[0]
    report=json.loads(store.db.execute('SELECT report_json FROM cleaning_runs WHERE id=?',(run,)).fetchone()[0])
    report['graph_edges']=store.db.execute('SELECT count(*) FROM aggregate_links').fetchone()[0]
    report['library_units_explicitly_linked']=units
    report['attachment_pages_explicitly_linked']=len(pages)
    report['srd_cards_explicitly_linked']=cards
    store.db.execute('UPDATE cleaning_runs SET report_json=? WHERE id=?',(dumps(report),run))
    result={'library_units_linked':units,'book_indexes':len({u['book_slug'] for u in rows if u['book_slug']}),
            'attachment_pages_linked':len(pages),'srd_cards_linked':cards,
            'new_navigation_evidence':new_evidence,'graph_edges':report['graph_edges'],'model_calls':0}
    if list(store.db.execute('PRAGMA foreign_key_check')):raise ValueError('Foreign key check failed')
    store.db.commit()
    return result,report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    with process_lock(args.database.parent):
        store=Store(args.database)
        try:
            store.db.execute('BEGIN IMMEDIATE')
            result,report=sync(store)
            (args.output/'library-navigation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            (args.output/'cleaning.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            path=args.output/'result.json'
            if path.exists():
                summary=json.loads(path.read_text(encoding='utf-8'));summary['cleaning']=report;summary['library_navigation']=result
                path.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(result),flush=True)
        finally:store.close()
