"""CPU-only exact-tokenizer projection of joint calls. Never performs inference."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase import enrichment as base, grouped_paragraphs as grouped
from sw_kbase.curation_store import entity
from sw_kbase.document_units import fragments
from sw_kbase.joint_jev import JointJevClient
from sw_kbase.store import Store


def simulate(client,target,reference,documents,db,retain):
    coverage=base.Coverage(base.evidence_unit(p,f,reference['id']) for p in base.paragraph_rows(db,reference['id']) for f in fragments(p,1600))
    counts=Counter(documents=len(documents)); tokens=[]
    for source in documents:
        source={**source,'_model':client.model};paragraphs=base.paragraph_rows(db,source['id'])
        counts['passages']+=len(paragraphs); queue=[{**p,'vote_id':p['label']} for p in paragraphs]
        full=True; calls=0
        if queue:
            probe={**queue[0],'body':'x','offset_end':queue[0]['offset_start']+1}
            full=grouped.fits(client,grouped.request_for(target,source,reference,coverage,[probe]))
        while queue:
            size=min(len(queue),256);request=None
            while size:
                request=grouped.bounded_request(client,target,source,reference,coverage,queue[:size],full_possible=full)
                if request:break
                size//=2
            if not request:
                part=queue.pop(0)
                if len(part['body'])>160:
                    middle=len(part['body'])//2
                    queue[:0]=[{**part,'body':part['body'][:middle],'offset_end':part['offset_start']+middle,'vote_id':part['vote_id']+'a'},
                        {**part,'body':part['body'][middle:],'offset_start':part['offset_start']+middle,'vote_id':part['vote_id']+'b'}]
                    counts['splits']+=1;continue
                counts['unresolved_tiny_fragments']+=1
                coverage.extend(base.evidence_unit(part,f,source['id']) for f in fragments(part,1600));continue
            batch=queue[:size];del queue[:size]
            # preflight is local tokenization only. __call__/transport is never used.
            tokens.append(client.preflight(request)['total_tokens']);calls+=1
            if retain:coverage.extend(base.evidence_unit(p,f,source['id']) for p in batch for f in fragments(p,1600))
        counts['batches']+=calls;counts['single_batch_documents']+=calls==1;counts['zero_call_documents']+=calls==0
    return dict(counts),tokens


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--selection',type=Path,required=True)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--sample-entities',type=int,default=200);p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--backend',choices=['clef','perplexity'],default='clef')
    args=p.parse_args()
    if args.sample_entities<1:p.error('Positive sample size required')
    root=Path(__file__).resolve().parents[2]
    if args.report.resolve().is_relative_to(root):p.error('Runtime report must be outside repository')
    began=time.monotonic();main_store=Store(args.database,readonly=True)
    db=sqlite3.connect(args.selection.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA cache_size=-65536');db.execute('BEGIN')
    try:
        if args.backend=='perplexity':
            from sw_kbase.perplexity_jev import PerplexityParagraphClient
            client=PerplexityParagraphClient(json.loads((args.runtime/'config.json').read_text(encoding='utf-8')),verify_weights=False)
        else:client=JointJevClient.from_runtime(args.runtime,connect=False,verify_weights=False)
        entries=db.execute("SELECT e.id,e.reference_id,e.order_json FROM entities e JOIN (SELECT entity_id FROM documents WHERE role='candidate' GROUP BY entity_id) d ON d.entity_id=e.id ORDER BY e.id").fetchall()
        sample=random.Random(args.seed).sample(entries,min(args.sample_entities,len(entries)))
        sampled_ids=[r['id'] for r in sample];sampled_docs={aid:[] for aid in sampled_ids}
        for row in db.execute('SELECT id,entity_id,version_id,metadata_json FROM documents WHERE entity_id IN ('+','.join('?' for _ in sampled_ids)+')',sampled_ids):
            sampled_docs[row['entity_id']].append(row)
        total=db.execute("SELECT count(*) FROM documents WHERE role='candidate'").fetchone()[0]
        report={'units_policy':base.meta(db)['units_policy'],'model':client.model,'maximum_tokens':4096,
            'batching_code_sha256':hashlib.sha256(Path(grouped.__file__).read_bytes()).hexdigest(),
            'sample_seed':args.seed,'population_entities':len(entries),'population_candidate_documents':total,
            'sample_entities':len(sample),'completed_entities':0,'model_calls':0,'server_accessed':False,
            'tokenizer_sha256':client.identity.get('tokenizer_sha256',client.identity.get('manifest_sha256')),'scenarios':{},'rows':[]}
        totals={False:Counter(),True:Counter()};token_counts={False:[],True:[]}
        for entry in sample:
            ids=json.loads(entry['order_json']);sources={}
            for row in sampled_docs[entry['id']]:
                md=json.loads(row['metadata_json'])
                body=main_store.db.execute('SELECT body FROM record_versions WHERE id=?',(row['version_id'],)).fetchone()[0]
                sources[row['id']]={**md,'body':body}
            ref=sources[entry['reference_id']];others=[sources[rid] for rid in ids if rid!=ref['id']]
            target=entity(main_store,entry['id']);row={'entity_id':entry['id']}
            for keep,name in [(False,'no_new_evidence_retained'),(True,'all_new_evidence_retained')]:
                values,tokens=simulate(client,target,ref,others,db,keep)
                totals[keep].update(values);token_counts[keep].extend(tokens);row[name]=values
            report['rows'].append(row);report['completed_entities']+=1
            if report['completed_entities']%10==0:
                base.atomic_json(args.report.with_suffix('.progress.json'),{'completed_entities':report['completed_entities'],
                    'sample_entities':len(sample),'elapsed_seconds':round(time.monotonic()-began,1),'model_calls':0,
                    'sample_documents':totals[True]['documents'],'sample_batches':totals[True]['batches']})
        rng=random.Random(args.seed+1)
        for keep,name in [(False,'no_new_evidence_retained'),(True,'all_new_evidence_retained')]:
            counts=totals[keep];ratio=counts['batches']/max(1,counts['documents']);boot=[]
            for _ in range(2000):
                rows=[report['rows'][rng.randrange(len(sample))][name] for _ in sample]
                boot.append(total*sum(r.get('batches',0) for r in rows)/max(1,sum(r.get('documents',0) for r in rows)))
            boot.sort();tokens=sorted(token_counts[keep])
            report['scenarios'][name]={**counts,'mean_batches_per_document':ratio,'projected_calls':round(total*ratio),
                'cluster_bootstrap_95_pct':[round(boot[int(.025*len(boot))]),round(boot[int(.975*len(boot))])],
                'tokens_mean':round(sum(tokens)/max(1,len(tokens))),'tokens_p95':tokens[int(.95*(len(tokens)-1))] if tokens else 0}
        report['elapsed_seconds']=round(time.monotonic()-began,1)
        report['note']='Exact CPU tokenizer and current IDs/batching code, two hypothetical retention scenarios. '
        report['note']+='Bootstrap reflects sampling only; semantic decisions can change coverage and retrieval. Scenarios are not strict bounds. No GPU/model/server call.'
        base.atomic_json(args.report,report)
        print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=True))
    finally:db.close();main_store.close()


if __name__=='__main__':main()
