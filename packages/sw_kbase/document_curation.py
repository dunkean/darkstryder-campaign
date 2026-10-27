"""Cumulative document deduplication with complete, bounded evidence units.

Exact unit coverage needs no model. Only lexically comparable, still uncovered
units get the contribution question. A single useful/uncertain unit retains the
document; exclusion requires coverage of every unit. No future source is used.
"""
from __future__ import annotations

from collections import Counter,defaultdict
import copy
import math
import re

from .disambiguation import DisambiguationLoop,PROTECTED,VERSION,complete,fingerprint,text_key

VERSION_UNITS='document-coverage/2-strict-gt80'
STOP=set('the a an of to in and or for with is are was were as on by from it its this that these those be has have at et de du des le la les un une au aux en dans pour avec est sont ce cette ces sur'.split())
WORDS=re.compile(r'[^\W_]+',re.UNICODE)
NUMBERS=re.compile(r'\b\d+(?:[.,]\d+)?(?:[dD](?:\s*\+\s*\d+)?)?\b')


def units(source,maximum=1400):
    """Partition all characters; whitespace-only spans cannot carry new text."""
    body=source['body'];result=[];start=0
    boundaries=[m.end() for m in re.finditer(r'\n[ \t]*\n',body)]+[len(body)]
    for boundary in boundaries:
        for pos in range(start,boundary,maximum):
            end=min(pos+maximum,boundary);text=body[pos:end]
            if not text.strip():continue
            result.append({'id':source['id']+'@'+str(pos)+':'+str(end),'record_id':source['id'],
                'version_id':source.get('version_id'),'body':text,'offset_start':pos,'offset_end':end,
                'source_unit':source.get('unit',{}),'context_complete':True})
        start=boundary
    return result


class DocumentCoverageLoop:
    def __init__(self,entity,reference,evaluate,*,model=None,max_unit_chars=1400,max_coverage_units=3,numeric_terms=None):
        self.entity=copy.deepcopy(entity);self.reference=copy.deepcopy(reference)
        self.loop=DisambiguationLoop(entity,reference,evaluate,model=model,redundancy_threshold=.80)
        if max_unit_chars<200 or max_coverage_units<1:raise ValueError('Invalid evidence unit bounds')
        self.maximum=max_unit_chars;self.neighbors=max_coverage_units
        self.numeric_terms=numeric_terms or (lambda text:{n.casefold().replace(' ','') for n in NUMBERS.findall(text)})
        self.coverage={};self.index=defaultdict(set);self.numbers=set();self.selection=[]
        self.retained=[self.reference];self.seen={reference['id']};self.metrics=Counter()
        self.full_text={}
        self.entity_words={w.casefold() for w in WORDS.findall(entity.get('name',''))}
        self.add_coverage(reference)

    def terms(self,text):return {w.casefold() for w in WORDS.findall(text)}-STOP-self.entity_words

    def add_coverage(self,source):
        if not complete(source):return
        if text_key(source):self.full_text.setdefault(text_key(source),source)
        for unit in units(source,self.maximum):
            key=text_key(unit)
            if key in self.coverage:continue
            self.coverage[key]=unit
            for term in self.terms(unit['body']):self.index[term].add(key)
            self.numbers.update(self.numeric_terms(unit['body']))

    def retrieve(self,unit):
        scores=Counter();terms=self.terms(unit['body'])
        for term in terms:
            keys=self.index.get(term,())
            weight=math.log(2+len(self.coverage)/(1+len(keys)))
            for key in keys:scores[key]+=weight
        return [self.coverage[key] for key,_ in sorted(scores.items(),key=lambda pair:(-pair[1],pair[0]))[:self.neighbors]]

    def source(self,source):
        if source['id'] in self.seen:raise ValueError('Source already processed')
        source=copy.deepcopy(source);source_units=units(source,self.maximum)
        row={'source_id':source['id'],'policy':VERSION,'coverage_policy':VERSION_UNITS,
             'unit_count':len(source_units),
             'version_id':source.get('version_id'),'entity_score_pct':None,'adds_information_score_pct':None,
             'reference_ids':[d['id'] for d in self.retained],'scores_pct':{},'unit_proofs':[],
             'evidence_fingerprint':fingerprint({'entity':self.entity,
                 'retained_versions':[(d['id'],d.get('version_id')) for d in self.retained],
                 'source':source,'coverage_policy':VERSION_UNITS})}
        def finish(selection,reason):
            row.update(merge_selection=selection,reason=reason)
            if selection=='retain':self.retained.append(source);self.add_coverage(source)
            if selection=='exclude_redundant':row['covered_by']=sorted({p['record_id'] for proof in row['unit_proofs'] for p in proof.get('covering_units',[])})
            self.selection.append(row);self.seen.add(source['id']);self.metrics[selection]+=1
            return row
        if source.get('identity_ambiguous'):
            fit=self.loop.ask({'ENTITY':self.entity,'REFERENCE':self.reference,'SOURCE':source},['entity_match']).get('entity_match',{})
            row['entity_score_pct']=fit.get('yes');row['scores_pct']['entity_match']=fit
            if fit.get('yes',0)<100*self.loop.identity_threshold:return finish('hold','different_entity' if fit.get('no',0)>=100*self.loop.identity_threshold else 'identity_unresolved')
        protected=bool(PROTECTED.intersection(source.get('preserve_reasons',[])))
        profile=source.get('profile',{})
        if profile and all(profile!=doc.get('profile',{}) for doc in self.retained):protected=True
        if protected:return finish('retain','protected_information')
        if not complete(source):return finish('retain','incomplete_source_preserved')
        if not source_units:return finish('retain','nontext_or_empty_source_preserved')
        same_text=self.full_text.get(text_key(source))
        if same_text:
            row['unit_proofs']=[{'selection':'exact_normalized_document','covering_units':[
                {'record_id':same_text['id'],'version_id':same_text.get('version_id')}]}]
            self.metrics['exact_documents']+=1
            return finish('exclude_redundant','exact_normalized_text')
        # First test whole-source exact coverage, before any semantic question.
        exact=[self.coverage.get(text_key(unit)) for unit in source_units]
        if all(exact):
            row['unit_proofs']=[{'unit_id':u['id'],'selection':'exact_coverage','covering_units':[covered]} for u,covered in zip(source_units,exact)]
            self.metrics['exact_documents']+=1
            return finish('exclude_redundant','exact_unit_coverage')
        for unit,covered in zip(source_units,exact):
            if covered:
                row['unit_proofs'].append({'unit_id':unit['id'],'selection':'exact_coverage','covering_units':[covered]});continue
            new_numbers=self.numeric_terms(unit['body'])-self.numbers
            if new_numbers:return finish('retain','new_numeric_or_dice_information')
            evidence=self.retrieve(unit)
            if not evidence:return finish('retain','no_comparable_retained_unit')
            scores=self.loop.ask({'ENTITY':self.entity,
                'REFERENCE':{'id':self.reference['id'],'name':self.reference.get('name'),
                    'role':'stable identity anchor; retained evidence units are supplied below'},
                'RETAINED_SOURCES':evidence,'SOURCE':unit,
                'COVERAGE_CONTRACT':'SOURCE is one complete bounded unit. The other units are exact passages from previously retained sources only. '
                    'Answer no only if these supplied units fully cover SOURCE; a retrieval match alone is not coverage. '
                    'Every source unit must be covered before its whole document can be excluded.'},['source_contribution']).get('source_contribution',{})
            self.metrics['unit_questions']+=1
            proof={'unit_id':unit['id'],'scores_pct':scores,'covering_units':evidence}
            row['unit_proofs'].append(proof)
            if len(source_units)==1:
                row['adds_information_score_pct']=scores.get('yes');row['scores_pct']['source_contribution']=scores
            if scores.get('no',0)<=100*self.loop.redundancy_threshold:
                proof['selection']='retain_or_uncertain'
                return finish('retain','information_or_uncertainty_to_preserve')
            proof['selection']='semantic_coverage'
        return finish('exclude_redundant','all_units_covered')

    def plan(self):
        return {'policy':VERSION,'coverage_policy':VERSION_UNITS,'entity_id':self.entity['id'],
                'reference_id':self.reference['id'],'retained_ids':[doc['id'] for doc in self.retained],
                'source_selection':self.selection,'observations':self.loop.observations,'metrics':dict(self.metrics),
                'raw_sources_deleted':False,'memberships_modified':False}
