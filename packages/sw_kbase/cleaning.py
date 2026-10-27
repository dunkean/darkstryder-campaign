"""CPU-only name aggregation and permissive lexical reference graph.

These are user-authorized working aggregates, not model-verified identities.
Original records, semantic decisions, profiles and editorial overlays are retained.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import datetime
import difflib
import json
import re
import time
import hashlib

from .store import dumps, normalize, stable
from .mining import field_mentions
from .sections import section_for, category_kind

VERSION = 'sw-cleaning/2-sectioned-alias-guards'
TITLES = {'dr', 'doctor', 'docteur', 'mr', 'mrs', 'ms', 'mister', 'madame', 'prof', 'professor',
          'capt', 'captain', 'capitaine', 'commander', 'commandant', 'cmdr', 'lt', 'lieutenant',
          'admiral', 'amiral', 'general', 'colonel', 'major', 'sergeant', 'sgt', 'corporal',
          'governor', 'gouverneur', 'moff', 'grand', 'senator', 'senateur', 'president',
          'princess', 'prince', 'queen', 'king', 'emperor', 'imperatrice', 'lord', 'lady'}
BUCKETS = {'craft_model': 'craft', 'craft_individual': 'craft', 'equipment_model': 'equipment',
           'item_individual': 'equipment', 'profile_component': 'equipment',
           'droid_model': 'droid', 'droid_individual': 'droid', 'creature_type': 'creature',
           'creature_individual': 'creature', 'mixed_document': 'document', 'concept': 'rule'}
WORDS = re.compile(r'[^\W_]+', re.UNICODE)


def name_key(name, kind='unknown'):
    value = str(name).strip()
    value=re.sub(r'/(?:Legends|Canon)$','',value,flags=re.I)
    value=re.sub(r'^(?:NRS[S]?\.?\s+|New Republic Starship\s+)','',value,flags=re.I)
    first = normalize(value).split()[:1]
    person = kind == 'person' or bool(first and first[0] in TITLES)
    # Person loadouts/epochs stay in their source records, under one working key.
    if person:
        value = re.sub(r'\s*\([^)]*\)', '', value)
    else:
        value = re.sub(r'\s*\((?:REUP|[12](?:nd)?\s*[Ee](?:dition)?|WEG|D6|stats?)[^)]*\)', '', value, flags=re.I)
    words = normalize(value).split()
    if person:
        while len(words) > 1 and words[0] in TITLES and words[1] != 's':words.pop(0)
    if kind == 'profile_component':
        while len(words) > 1 and (words[0].isdigit() or words[0] == 'x'):words.pop(0)
        plurals = {'cannons':'cannon','batteries':'battery','guns':'gun','blasters':'blaster','turbolasers':'turbolaser'}
        words = [plurals.get(word, word) for word in words]
    return ' '.join(words)


def bucket(kind):
    return BUCKETS.get(kind, kind if kind in {'person','organization','location','species','archetype',
                                            'rule','skill','force_power','scenario','event',
                                            'craft','vehicle','equipment','droid','creature','document','system','stellar_route'} else 'unknown')


def infer_bucket(record, body=''):
    result = bucket(record['kind'])
    if result != 'unknown':return result
    inferred=category_kind(re.findall(r'\[\[Category:([^\]|]+)',body,re.I))
    if inferred!='unknown':return bucket(inferred)
    for pattern, inferred in [
        (r'category\s*:\s*(?:characters?|people|npcs?)\b','person'),
        (r'category\s*:\s*(?:starships?|ships?|vehicles?|fighters?)\b','craft'),
        (r'category\s*:\s*(?:weapons?|equipment|armor|armour)\b','equipment'),
        (r'category\s*:\s*(?:droids?)\b','droid'),
        (r'category\s*:\s*(?:planets?|systems?|locations?|sectors?)\b','location'),
        (r'category\s*:\s*(?:species|races?|aliens?)\b','species'),
        (r'category\s*:\s*(?:creatures?|animals?)\b','creature')]:
        if re.search(pattern, body, re.I):return inferred
    if re.match(r'^(?:D6 Holocron Wiki|Category|Template|Forum|Help|User|Talk)\s*:', record['name'], re.I):return 'document'
    return result


def refresh_aggregate_types(store, report):
    """Recompute navigation types from explicit source hints, without regrouping."""
    groups=defaultdict(list)
    for row in store.db.execute('SELECT m.aggregate_id,v.name,v.kind,substr(v.body,1,10000) body '
                               'FROM aggregate_members m JOIN records r ON r.id=m.record_id '
                               'JOIN record_versions v ON v.id=r.current_version'):
        groups[row['aggregate_id']].append(infer_bucket(dict(row),row['body']))
    for row in store.db.execute('SELECT id,warnings_json FROM aggregates'):
        values=groups[row['id']];counts=Counter(k for k in values if k not in {'unknown','document'})
        kind=counts.most_common(1)[0][0] if counts else ('document' if 'document' in values else 'unknown')
        flags=set(json.loads(row['warnings_json']));flags.discard('kind_conflict')
        if len(counts)>1:flags.add('kind_conflict')
        store.db.execute('UPDATE aggregates SET kind=?,warnings_json=? WHERE id=?',(kind,dumps(sorted(flags)),row['id']))
    unknown=("SELECT p.id FROM proposals p JOIN aggregate_members m ON m.record_id=p.a "
             "JOIN aggregates a ON a.id=m.aggregate_id WHERE p.task IN ('type','classification') "
             "AND p.status='auto_typed' AND a.kind='unknown'")
    reopened=store.db.execute('SELECT count(*) FROM ('+unknown+')').fetchone()[0]
    store.db.execute("UPDATE cleaning_decisions SET new_status='pending',reason='No explicit source type: keep unknown and leave the question open' WHERE run_id=? AND proposal_id IN ("+unknown+')',(report['run_id'],))
    store.db.execute("UPDATE proposals SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE id IN ("+unknown+')')
    report['after']=[dict(r) for r in store.db.execute('SELECT task,status,count(*) count FROM proposals GROUP BY task,status')]
    report['changes']=[dict(r) for r in store.db.execute('SELECT new_status,count(*) count FROM cleaning_decisions WHERE run_id=? AND new_status!=previous_status GROUP BY new_status',(report['run_id'],))]
    report['remaining_questions']=sum(r['count'] for r in report['after'] if r['status'] in {'pending','uncertain','error','deferred_by_group'})
    report['aggregates_with_warnings']=store.db.execute("SELECT count(*) FROM aggregates WHERE warnings_json!='[]'").fetchone()[0]
    report['unknown_type_questions_reopened']=reopened
    store.db.execute('UPDATE cleaning_runs SET report_json=? WHERE id=?',(dumps(report),report['run_id']))
    store.db.commit()
    return report


class Union:
    def __init__(self, ids):self.parent = {rid: rid for rid in ids}
    def find(self, rid):
        if self.parent[rid] != rid:self.parent[rid] = self.find(self.parent[rid])
        return self.parent[rid]
    def join(self, a, b):
        a,b = self.find(a),self.find(b)
        if a != b:self.parent[max(a,b)] = min(a,b)


def one_typo(a, b):
    if min(len(a), len(b)) < 12 or abs(len(a)-len(b)) > 1:return False
    if re.findall(r'\d+', a) != re.findall(r'\d+', b):return False
    if len(a.split()) != len(b.split()):return False
    if a == b:return True
    if len(a) == len(b):return sum(x != y for x,y in zip(a,b)) <= 1
    if len(a) > len(b):a,b = b,a
    i = j = mistakes = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:i += 1;j += 1
        else:
            mistakes += 1;j += 1
            if mistakes > 1:return False
    return True


def compatible(a, b):
    return a['universe'] == b['universe'] or {a['universe'],b['universe']} <= {'unknown','star_wars'}


def build_aggregates(store, run_id):
    records = {}
    for row in store.db.execute('SELECT r.id,r.entity_id,r.current_version,r.source_id,v.name,v.kind,v.universe,v.origin,'
                               'v.aliases_json,v.scope_json,v.locator_json,substr(v.body,1,10000) body FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1'):
        record = dict(row);record['aliases'] = json.loads(record.pop('aliases_json'))
        record['scope']=json.loads(record.pop('scope_json'));record['locator']=json.loads(record.pop('locator_json'))
        record['section'],record['section_rule']=section_for(record)
        record['key'] = name_key(record['name'],record['kind']) or normalize(record['name'])
        record['bucket'] = infer_bucket(record,record.pop('body'))
        records[record['id']] = record
    union = Union(records); primary = defaultdict(list); reasons = {}
    def join(a,b,rule):
        if compatible(records[a],records[b]):union.join(a,b);reasons[b] = rule
    known = defaultdict(list)
    def identity_compatible(a,b):
        return (a['section']==b['section'] and compatible(a,b)
                and (a['bucket']==b['bucket'] or 'unknown' in {a['bucket'],b['bucket']}))
    def close_alias(a,b):
        # Declared source aliases can mean class, employer, affiliation or a
        # former organization. They remain navigable, but never create an
        # unrestricted transitive identity bridge.
        return a==b or one_typo(a,b)
    for rid, record in records.items():
        for other in known[record['entity_id']]:
            if identity_compatible(record,records[other]) and close_alias(record['key'],records[other]['key']):join(other,rid,'existing_identity_lexically_confirmed')
        known[record['entity_id']].append(rid)
        pkey=(record['section'],record['key'])
        for other in primary[pkey]:
            if compatible(record,records[other]):join(other,rid,'normalized_name')
        primary[pkey].append(rid)
    # Explicit aliases are reusable name evidence; reject broad class/member bridges.
    for rid, record in records.items():
        for alias in record['aliases']:
            form = name_key(alias,record['kind'])
            if len(form) < 4:continue
            for other in primary.get((record['section'],form), []):
                if identity_compatible(record,records[other]) and close_alias(record['key'],records[other]['key']):
                    join(rid,other,'declared_alias')
    for row in store.db.execute("SELECT subject_record_id,object_record_id FROM document_links WHERE kind='wiki_redirect'"):
        if row[0] in records and row[1] in records:
            a,b=records[row[0]],records[row[1]]
            if identity_compatible(a,b) and close_alias(a['key'],b['key']):join(row[0],row[1],'declared_redirect')
    # Correct one-character spelling variants only in the pre-existing candidate
    # graph; model numbers and short names are deliberately not conflated.
    for row in store.db.execute("SELECT a,b FROM proposals WHERE task='identity' AND status IN ('pending','uncertain')"):
        if row['a'] in records and row['b'] in records:
            a,b = records[row['a']],records[row['b']]
            if identity_compatible(a,b) and one_typo(a['key'],b['key']):join(a['id'],b['id'],'single_character_variant')
    families = defaultdict(list)
    for rid in records:families[union.find(rid)].append(rid)
    previous = dict(store.db.execute('SELECT record_id,aggregate_id FROM aggregate_members'))
    oldanchors=dict(store.db.execute('SELECT id,anchor_record_id FROM aggregates'))
    store.db.execute('DELETE FROM aggregate_text_sources');store.db.execute('DELETE FROM aggregate_text_blocks')
    store.db.execute('DELETE FROM aggregate_sections');store.db.execute('DELETE FROM record_sections')
    store.db.execute('DELETE FROM aggregate_link_evidence');store.db.execute('DELETE FROM aggregate_links')
    store.db.execute('DELETE FROM aggregate_aliases');store.db.execute('DELETE FROM aggregate_members');store.db.execute('DELETE FROM aggregates')
    membership = {}; aliases = defaultdict(set); flags = {}; used_ids = set()
    for ids in families.values():
        keys = Counter(records[rid]['key'] for rid in ids)
        key = min(keys, key=lambda k:(-keys[k], len(k),k))
        universe = next((records[rid]['universe'] for rid in ids if records[rid]['universe'] != 'unknown'),'unknown')
        # Type and corpus partition prevent e.g. a ship called Alliance from
        # becoming the organization, or an adventure title becoming a place.
        concrete=Counter(records[rid]['bucket'] for rid in ids if records[rid]['bucket']!='unknown')
        preferred=Counter(records[rid]['bucket'] for rid in ids if records[rid]['bucket'] not in {'unknown','document'} and records[rid]['origin'] in {'fan_compilation','statbase_compilation','reup_adaptation','film_reference','modern_canon_summary','fan_conversion'})
        group_kind=(preferred or concrete).most_common(1)[0][0] if concrete else 'unknown'
        fullkey = universe + ':' + records[ids[0]]['section'] + ':' + group_kind + ':' + key
        old = Counter(previous[rid] for rid in ids if rid in previous and previous[rid] not in used_ids and oldanchors.get(previous[rid]) in ids)
        aid = old.most_common(1)[0][0] if old else stable('agg',fullkey)
        if aid in used_ids:aid = stable('agg',fullkey,sorted(ids))
        used_ids.add(aid)
        kinds = Counter(records[rid]['bucket'] for rid in ids if records[rid]['bucket'] not in {'unknown','document'})
        kind = (preferred or kinds).most_common(1)[0][0] if kinds else ('document' if any(records[rid]['bucket']=='document' for rid in ids) else 'unknown')
        anchor = min(ids,key=lambda rid:(records[rid]['key'] != key,records[rid]['bucket'] != kind,len(records[rid]['name']),rid))
        warnings = set()
        if len(kinds) > 1:warnings.add('kind_conflict')
        if len(ids) > 64:warnings.add('large_aggregate')
        flags[aid] = warnings
        store.db.execute('INSERT INTO aggregates VALUES(?,?,?,?,?,?,?,?)',
                         (aid,fullkey,records[anchor]['name'],kind,universe,anchor,dumps(sorted(warnings)),run_id))
        for rid in ids:
            record = records[rid];membership[rid] = aid
            store.db.execute('INSERT INTO record_sections VALUES(?,?,?)',(rid,record['section'],record['section_rule']))
            store.db.execute('INSERT INTO aggregate_members VALUES(?,?,?,?,?)',
                             (rid,aid,record['current_version'],record['key'],reasons.get(rid,'principal_name')))
            for label in [record['name'],record['key'],*record['aliases']]:
                for form in {normalize(label),name_key(label,record['kind'])}:
                    if form:
                        aliases[form].add(aid)
                        store.db.execute('INSERT OR IGNORE INTO aggregate_aliases VALUES(?,?,?)',(form,aid,label))
        for section,count in Counter(records[r]['section'] for r in ids).items():
            store.db.execute('INSERT INTO aggregate_sections VALUES(?,?,?)',(aid,section,count))
    for row in store.db.execute("SELECT a,b FROM proposals WHERE task='identity' AND status='accepted' AND decision IN ('different_entity','mention_only')"):
        if membership.get(row['a']) == membership.get(row['b']) and row['a'] in membership:
            flags[membership[row['a']]].add('prior_model_conflict')
    for row in store.db.execute('SELECT a,b FROM cannot_links'):
        if membership.get(row['a']) == membership.get(row['b']) and row['a'] in membership:
            flags[membership[row['a']]].add('prior_manual_conflict')
    old_entities=defaultdict(set)
    for rid,record in records.items():old_entities[record['entity_id']].add(membership[rid])
    for targets in old_entities.values():
        if len(targets)>1:
            for aid in targets:flags[aid].add('prior_identity_disagrees')
    for aid,warnings in flags.items():store.db.execute('UPDATE aggregates SET warnings_json=? WHERE id=?',(dumps(sorted(warnings)),aid))
    return records,membership,aliases,flags


def merge_texts(store,membership,progress):
    """Lossless text collation; identical whitespace-normalized blocks stored once.

    Conflicting or differently phrased blocks stay separate for the later LLM.
    Every occurrence retains its exact source version and offsets.
    """
    seen=set();sequence=Counter();blocks=[];evidence=[];occurrences=0;characters=0
    for n,row in enumerate(store.db.execute('SELECT r.id,r.current_version,v.body FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1'),1):
        rid,vid,body=row;aid=membership[rid];boundaries=[0]
        boundaries.extend(m.end() for m in re.finditer(r'\n[ \t]*\n',body));boundaries.append(len(body))
        for start,end in zip(boundaries,boundaries[1:]):
            raw=body[start:end];start+=len(raw)-len(raw.lstrip());end-=len(raw)-len(raw.rstrip())
            for pos in range(start,end,4000):
                stop=min(pos+4000,end);text=body[pos:stop]
                if not text.strip():continue
                hashed=hashlib.sha256(' '.join(text.split()).encode('utf-8')).hexdigest()
                key=(aid,hashed)
                if key not in seen:
                    seen.add(key);blocks.append((aid,hashed,sequence[aid],text));sequence[aid]+=1;characters+=len(text)
                evidence.append((aid,hashed,rid,vid,pos,stop));occurrences+=1
        if n%500==0:
            store.db.executemany('INSERT INTO aggregate_text_blocks VALUES(?,?,?,?)',blocks);blocks=[]
            store.db.executemany('INSERT OR IGNORE INTO aggregate_text_sources VALUES(?,?,?,?,?,?)',evidence);evidence=[]
        if n%5000==0:progress('collating_text',documents=n,unique_blocks=len(seen),source_occurrences=occurrences)
    store.db.executemany('INSERT INTO aggregate_text_blocks VALUES(?,?,?,?)',blocks)
    store.db.executemany('INSERT OR IGNORE INTO aggregate_text_sources VALUES(?,?,?,?,?,?)',evidence)
    return {'merged_unique_text_blocks':len(seen),'merged_text_source_occurrences':occurrences,
            'duplicate_text_blocks_collapsed':occurrences-len(seen),'merged_text_characters':characters}


def remaining_candidates(store,records,membership):
    """Only unresolved aggregate-level aliases and missing classifications.

    No quadratic pair generation and no model execution.
    """
    primary=defaultdict(set);anchors={r['id']:dict(r) for r in store.db.execute('SELECT id,anchor_record_id,kind,universe FROM aggregates')}
    for rid,r in records.items():primary[(r['section'],r['key'])].add(membership[rid])
    candidates={};generic={'starship','ship','vaisseau','creature','alien','human','droid','weapon','blaster','rebel','imperial','captain','pilot','technician','unknown'}
    for rid,r in records.items():
        source=membership[rid]
        for alias in r['aliases']:
            form=name_key(alias,r['kind'])
            if len(form)<5 or form in generic:continue
            for target in primary.get((r['section'],form),set()):
                if target==source or anchors[source]['kind']!=anchors[target]['kind']:continue
                if not compatible(r,records[anchors[target]['anchor_record_id']]):continue
                pair=tuple(sorted((source,target)))
                candidates.setdefault(pair,form)
    for (a,b),form in candidates.items():
        store.enqueue('identity',anchors[a]['anchor_record_id'],anchors[b]['anchor_record_id'],
                      context={'kind':'semantic_alias','alias':form},score=100,
                      reasons=['Explicit source alias spans two different principal names; one aggregate-level comparison'])
    for aid,a in anchors.items():
        rid=a['anchor_record_id']
        if records[rid]['section'] in {'metadata','source'}:continue
        if a['kind']=='unknown':store.enqueue('type',rid)
        if a['universe']=='unknown':store.enqueue('universe',rid)
    return {'semantic_alias_aggregate_candidates':len(candidates)}


@lru_cache(maxsize=100000)
def word_form(word):return word.casefold() if word.isascii() else normalize(word)


def build_graph(store, records, membership, aliases, progress):
    trie = {}; ambiguous = 0
    for form, targets in aliases.items():
        words = form.split()
        if len(words)>16 or len(form)<3:continue
        node = trie
        for word in words:node = node.setdefault(word,{})
        node[None] = targets
        ambiguous += len(targets)>1
    edges = defaultdict(lambda:[0,0,set()]); evidence_rows = []; processed = 0; lexical = 0
    structural_seen = set()
    def emit(rid,target,kind,offset,excerpt,rule,context,count=1):
        source = membership[rid]
        if source == target:return
        key = (source,target,kind);entry = edges[key]
        # A record contributes only once to the document count of this edge.
        token = (target,kind)
        if kind == 'mentions':
            if token not in per_record_seen:entry[0] += 1;per_record_seen.add(token)
        elif (rid,target,kind) not in structural_seen:
            entry[0] += 1;structural_seen.add((rid,target,kind))
        entry[1] += count
        if context.get('ambiguous_alias'):entry[2].add('ambiguous_alias')
        eid = stable('ev-auto',rid,records[rid]['current_version'],target,kind,offset,rule)
        evidence_rows.append((eid,source,target,kind,rid,records[rid]['current_version'],offset,excerpt,rule,dumps(context)))
    for row in store.db.execute('SELECT r.id,v.body,v.payload_json FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1'):
        rid = row['id'];body = row['body'];per_record_seen = set(); matches = defaultdict(list); occurrence_count=Counter()
        words = list(WORDS.finditer(body));forms = [word_form(m.group()) for m in words];i=0
        while i<len(words):
            node=trie.get(forms[i]);j=i+1;best=None
            while node is not None:
                if None in node:best=(j,node[None])
                if j>=len(words):break
                node=node.get(forms[j]);j+=1
            if best:
                end,targets=best;start_offset=words[i].start();end_offset=words[end-1].end()
                # Serialized field names are already represented by structured
                # values; they are not narrative uses of the identically named NPC.
                if start_offset and body[start_offset-1] in {'"', "'"} and re.match(r'\s*["\']\s*:',body[end_offset:end_offset+10]):i=end;continue
                for target in targets:
                    if target==membership[rid]:continue
                    occurrence_count[target]+=1
                    if len(matches[target])<3:matches[target].append((start_offset,body[max(0,start_offset-160):min(len(body),end_offset+220)],
                        {'surface':body[start_offset:end_offset],'ambiguous_alias':len(targets)>1,'candidate_count':len(targets)}))
                i=end
            else:i+=1
        for target, examples in matches.items():
            for n,(offset,excerpt,context) in enumerate(examples):
                emit(rid,target,'mentions',offset,excerpt,'exact_alias',context,occurrence_count[target] if n==0 else 0)
            lexical+=1
        # Field-level statements create permissive equipment/related links.
        for value in field_mentions(json.loads(row['payload_json'])):
            form=normalize(value['text']);targets=aliases.get(form) or aliases.get(name_key(value['text'])) or set()
            kind='has_item' if value['predicate'] in {'equipped_with','part_of','instance_of'} else 'related'
            for target in targets:emit(rid,target,kind,None,value['passage'],'structured_field',
                                      {'field':value['field'],'source_predicate':value['predicate'],'ambiguous_alias':len(targets)>1})
        if len(evidence_rows)>5000:
            store.db.executemany('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',evidence_rows);evidence_rows=[]
        processed+=1
        if processed%1000==0:progress('text_mining',processed=processed,lexical_document_links=lexical)
    store.db.executemany('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',evidence_rows)
    evidence_rows=[]
    # Native references and every already-mined structured relation are useful
    # navigation, without asking a neural model what "has this cannon" means.
    progress('native_references',documents_scanned=processed,lexical_document_links=lexical)
    for row in store.db.execute('SELECT * FROM document_links'):
        a,b=row['subject_record_id'],row['object_record_id']
        if a not in membership or b not in membership:continue
        kind='source_reference';source,target=a,b
        if row['kind']=='profile_component_of':source,target=b,a;kind='has_item'
        per_record_seen=set()
        emit(source,membership[target],kind,None,records[target]['name'],'native_reference',{'native_kind':row['kind'],'target_record_id':target})
    store.db.executemany('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',evidence_rows);evidence_rows=[]
    for row in store.db.execute("SELECT a,b,predicate,context_json FROM proposals WHERE task='relation' AND status IN ('pending','uncertain','accepted')"):
        a,b=row['a'],row['b']
        if a not in membership or b not in membership:continue
        context=json.loads(row['context_json']);per_record_seen=set()
        kind='has_item' if row['predicate'] in {'equipped_with','part_of','instance_of'} else 'related'
        emit(a,membership[b],kind,context.get('offset'),context.get('passage') or context.get('mention') or records[b]['name'],
             'mined_relation',{'source_predicate':row['predicate'],'context':context})
        if len(evidence_rows)>5000:
            store.db.executemany('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',evidence_rows);evidence_rows=[]
    store.db.executemany('INSERT OR IGNORE INTO aggregate_link_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',evidence_rows)
    progress('materializing_graph',edges=len(edges))
    store.db.executemany('INSERT INTO aggregate_links VALUES(?,?,?,?,?,?)',
                         ((a,b,kind,entry[0],entry[1],dumps(sorted(entry[2]))) for (a,b,kind),entry in edges.items()))
    return {'documents_scanned':processed,'lexical_document_links':lexical,'graph_edges':len(edges),'ambiguous_alias_forms':ambiguous}


def reduce_questions(store,run_id,records,membership,progress):
    progress('classifying_old_questions')
    def action(where,status,reason):
        store.db.execute('INSERT OR IGNORE INTO cleaning_decisions SELECT ?,p.id,p.status,?,? FROM proposals p '+where,(run_id,status,reason))
    editable="p.status IN ('pending','uncertain','error','deferred_by_group')"
    action("JOIN aggregate_members a ON a.record_id=p.a JOIN aggregate_members b ON b.record_id=p.b WHERE p.task='identity' AND "+editable+" AND a.aggregate_id=b.aggregate_id",'auto_grouped','Normalized principal name, alias or spelling variant; documents share a working aggregate')
    # Weak token/FTS collisions are not identity questions. Keep close unresolved
    # names for inspection instead of declaring those subjects different.
    rows=[]
    for p in store.db.execute("SELECT id,a,b,status,context_json FROM proposals WHERE task='identity' AND status IN ('pending','uncertain','error')"):
        if p['a'] not in records or p['b'] not in records or membership[p['a']]==membership[p['b']]:continue
        a,b=records[p['a']]['key'],records[p['b']]['key'];shared=set(a.split())&set(b.split())
        close=(bool(shared) and difflib.SequenceMatcher(None,a,b).ratio()>=.75) or json.loads(p['context_json']).get('kind')=='semantic_alias'
        if not close:rows.append((run_id,p['id'],p['status'],'dismissed_lexical_candidate','Weak lexical collision; retained as history, not an assertion of different identity'))
    store.db.executemany('INSERT OR IGNORE INTO cleaning_decisions VALUES(?,?,?,?,?)',rows)
    action("JOIN aggregate_members a ON a.record_id=p.a JOIN aggregate_members b ON b.record_id=p.b WHERE p.task='mention' AND "+editable+" AND (a.aggregate_id=b.aggregate_id OR EXISTS (SELECT 1 FROM aggregate_link_evidence e WHERE e.record_id=p.a AND e.object_id=b.aggregate_id))",'auto_linked','Replaced by exact-alias document links or an internal same-key reference')
    action("WHERE p.task='mention' AND "+editable,'superseded_text_mining','Old occurrence candidate is superseded by the full-text lexical graph; no semantic inequality asserted')
    action("WHERE p.task='relation' AND "+editable,'auto_linked','Replaced by permissive typed navigation links with source excerpts')
    action("WHERE p.task='merge_utility' AND "+editable,'deferred_to_merger','Document synthesis and inconsistency warnings belong to the later merger')
    action("JOIN aggregate_members m ON m.record_id=p.a JOIN aggregates a ON a.id=m.aggregate_id WHERE p.task IN ('type','classification') AND "+editable+" AND a.kind!='unknown'",'auto_typed','Type inherited from structured source categories and aggregate members')
    action("JOIN aggregate_members m ON m.record_id=p.a JOIN aggregates a ON a.id=m.aggregate_id WHERE p.task='universe' AND "+editable+" AND a.universe!='unknown'",'auto_universe','Universe explicitly supplied by matching source records or categories')
    progress('updating_questions',actions=store.db.execute('SELECT count(*) FROM cleaning_decisions WHERE run_id=?',(run_id,)).fetchone()[0])
    store.db.execute('UPDATE proposals SET status=(SELECT new_status FROM cleaning_decisions c WHERE c.run_id=? AND c.proposal_id=proposals.id),updated_at=CURRENT_TIMESTAMP WHERE id IN (SELECT proposal_id FROM cleaning_decisions WHERE run_id=?)',(run_id,run_id))
    return [dict(r) for r in store.db.execute('SELECT new_status,count(*) count FROM cleaning_decisions WHERE run_id=? GROUP BY new_status',(run_id,))]


def clean(store,progress=None):
    start=time.monotonic();created=datetime.datetime.now(datetime.timezone.utc).isoformat();run_id=stable('clean',VERSION,created)
    progress=progress or (lambda *a,**kw:None)
    before=[dict(r) for r in store.db.execute('SELECT task,status,count(*) count FROM proposals GROUP BY task,status')]
    store.db.execute('PRAGMA cache_size=-1048576')
    store.db.execute('BEGIN IMMEDIATE')
    try:
        store.db.execute('INSERT INTO cleaning_runs VALUES(?,?,?,?)',(run_id,VERSION,created,'{}'))
        # Revisit earlier automatic identity/type closures after repairing the
        # alias policy; original NN verdicts and their entity IDs stay intact.
        store.db.execute('CREATE TEMP TABLE rechecked_statuses AS SELECT id,status FROM proposals WHERE status IN (\'auto_grouped\',\'auto_typed\')')
        store.db.execute('CREATE UNIQUE INDEX rechecked_status_id ON rechecked_statuses(id)')
        store.db.execute("UPDATE proposals SET status='pending' WHERE id IN (SELECT id FROM rechecked_statuses)")
        progress('aggregating_names')
        records,membership,aliases,flags=build_aggregates(store,run_id)
        candidates=remaining_candidates(store,records,membership)
        progress('aggregates_ready',records=len(records),aggregates=len(flags))
        graph=build_graph(store,records,membership,aliases,progress)
        text_merge=merge_texts(store,membership,progress)
        changes=reduce_questions(store,run_id,records,membership,progress)
        store.db.execute('INSERT OR IGNORE INTO cleaning_decisions SELECT ?,p.id,r.status,p.status,? FROM rechecked_statuses r JOIN proposals p ON p.id=r.id',(run_id,'Rechecked earlier automatic closure with the revised identity policy'))
        store.db.execute('UPDATE cleaning_decisions SET previous_status=(SELECT status FROM rechecked_statuses r WHERE r.id=proposal_id) WHERE run_id=? AND proposal_id IN (SELECT id FROM rechecked_statuses)',(run_id,))
        store.db.execute('DROP TABLE rechecked_statuses')
        changes=[dict(r) for r in store.db.execute('SELECT new_status,count(*) count FROM cleaning_decisions WHERE run_id=? GROUP BY new_status',(run_id,))]
        after=[dict(r) for r in store.db.execute('SELECT task,status,count(*) count FROM proposals GROUP BY task,status')]
        report={'run_id':run_id,'version':VERSION,'created_utc':created,'records':len(records),'aggregates':len(flags),
                'aggregates_with_warnings':sum(bool(v) for v in flags.values()),**graph,**text_merge,**candidates,'changes':changes,
                'before':before,'after':after,'remaining_questions':sum(r['count'] for r in after if r['status'] in {'pending','uncertain','error','deferred_by_group'}),
                'seconds':round(time.monotonic()-start,2),'model_calls':0,'strata_started':False,
                'identity_semantics':'Permissive working document aggregates; originals and model identities retained'}
        store.db.execute('UPDATE cleaning_runs SET report_json=? WHERE id=?',(dumps(report),run_id))
        store.db.execute("INSERT OR REPLACE INTO meta VALUES('active_cleaning_run',?)",(run_id,))
        store.db.commit();progress('complete',**report)
        return report
    except BaseException:
        store.db.rollback();raise
