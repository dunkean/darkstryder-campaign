"""Bounded lexical candidate mining. Every nontrivial semantic link remains a proposal."""
from __future__ import annotations

import collections
import json
import math
import re
from functools import lru_cache

from .store import digest, dumps, grams, normalize

VERSION = 'sw-mining/4'
NON_SUBJECTS = {'mixed_document'}
RELATION_FIELDS = {
    'species': 'species_of', 'race': 'species_of', 'homeworld': 'originates_from', 'planet': 'originates_from',
    'planetoforigin': 'originates_from', 'system': 'located_in', 'location': 'located_in',
    'affiliation': 'member_of', 'affiliations': 'member_of', 'faction': 'member_of',
    'manufacturer': 'manufactured_by', 'manufacturers': 'manufactured_by',
    'equipment': 'equipped_with', 'weapons': 'equipped_with', 'equipped': 'equipped_with',
    'prerequisites': 'requires', 'prereqs': 'requires', 'prereq': 'requires', 'skill': 'requires',
    'model': 'instance_of', 'craft': 'instance_of',
}


def fts(store, query, limit, exclude):
    words = normalize(query).split()
    if not words:
        return []
    expression = 'name : (' + ' OR '.join('"' + word + '"' for word in words[:8]) + ')'
    return [row[0] for row in store.db.execute(
        'SELECT f.record_id FROM records_fts f JOIN records r ON r.id=f.record_id '
        'WHERE records_fts MATCH ? AND r.id!=? AND r.active=1 ORDER BY bm25(records_fts) LIMIT ?', (expression, exclude, limit))]


def field_mentions(payload):
    """Keep relation phrases and their exact field locator; never infer from mere co-occurrence."""
    for key, value in payload.items():
        predicate = RELATION_FIELDS.get(re.sub('[^a-z]', '', key.casefold()))
        if not predicate:
            continue
        values = value if isinstance(value, list) else [value]
        for number, candidate in enumerate(values[:32]):
            if isinstance(candidate, dict):
                candidate = candidate.get('name', '')
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            # Short equipment lists can contain multiple supported model names; preserve original phrase.
            for phrase in re.split(r'[,;\n]|<br\s*/?>', candidate):
                phrase = phrase.strip()
                if 2 <= len(phrase) <= 180 and not phrase.startswith(('http:', 'https:')):
                    begin = candidate.find(phrase)
                    passage = candidate[max(0, begin - 180):begin + len(phrase) + 250]
                    yield {'text': phrase, 'field': key, 'index': number, 'predicate': predicate, 'passage': f'{key}: {passage}'}


class Names:
    def __init__(self, store):
        self.exact = collections.defaultdict(list)
        self.by_first = collections.defaultdict(set)
        self.short_names = set()
        self.store = store
        self.metadata = {}
        self.gram_frequency = dict(store.db.execute('SELECT gram,count(*) FROM name_grams GROUP BY gram'))
        for row in store.db.execute('SELECT n.form,n.record_id,v.kind,v.name,v.universe FROM names n JOIN records r ON r.id=n.record_id JOIN record_versions v ON v.id=r.current_version WHERE r.active=1'):
            self.metadata[row['record_id']] = {'kind': row['kind'], 'name': row['name'], 'normal_name': normalize(row['name']), 'universe': row['universe']}
            if row['kind'] in NON_SUBJECTS:
                continue
            self.exact[row['form']].append(row['record_id'])
            if row['kind'] in {'person', 'droid_individual', 'creature_individual', 'craft_individual', 'location', 'organization', 'species'}:
                self.short_names.add(row['form'])
            words = row['form'].split()
            if 1 <= len(words) <= 9:
                self.by_first[words[0]].add(len(words))

    @lru_cache(maxsize=8192)
    def tokens(self, rid):
        row = self.store.db.execute('SELECT substr(v.body,1,2000) FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.id=?', (rid,)).fetchone()
        return frozenset(normalize(row[0][:2000]).split())

    @lru_cache(maxsize=8192)
    def lexical(self, form, limit):
        return fts(self.store, form, limit + 1, '')

    @lru_cache(maxsize=8192)
    def near(self, name, limit):
        trigrams = sorted(grams(name))
        if len(trigrams) < 4:
            return []
        threshold = max(3, math.ceil(len(trigrams) * .5))
        # Pigeonhole bound: every candidate reaching threshold shares at least one of these rare seeds.
        seeds = sorted(trigrams, key=lambda g: (self.gram_frequency.get(g, 0), g))[:len(trigrams) - threshold + 1]
        seed_marks = ','.join('?' for _ in seeds)
        marks = ','.join('?' for _ in trigrams)
        return [(row[0], 10 + 10 * row[1] / len(trigrams)) for row in self.store.db.execute(
            f'WITH seeds AS MATERIALIZED (SELECT DISTINCT ng.record_id FROM name_grams ng JOIN records r ON r.id=ng.record_id WHERE ng.gram IN ({seed_marks}) AND r.active=1) SELECT ng.record_id,count(*) n FROM seeds CROSS JOIN name_grams ng WHERE ng.record_id=seeds.record_id AND ng.gram IN ({marks}) GROUP BY ng.record_id HAVING count(*)>=? ORDER BY n DESC,ng.record_id LIMIT ?',
            (*seeds, *trigrams, threshold, limit + 1))]

    def occurrences(self, text, max_mentions=24):
        # Word offsets retain actual context; normalized matching handles punctuation/OCR variants.
        words = list(re.finditer(r'[^\W_]+', text, re.UNICODE))
        normal = [m.group().casefold() if m.group().isascii() else normalize(m.group()) for m in words]
        found = set()
        for i, word in enumerate(normal):
            for length in sorted(self.by_first.get(word, ()), reverse=True):
                if i + length > len(words):
                    continue
                form = ' '.join(normal[i:i + length])
                if form not in self.exact or (form, i) in found or len(form) < 4:
                    continue
                # Single common words are queried only through structured fields, not global text mentions.
                if length == 1 and len(form) < 7 and form not in self.short_names:
                    continue
                begin, end = words[i].start(), words[i + length - 1].end()
                # Serialized attribute keys are not narrative mentions of the corresponding rule.
                if form in {'strength', 'dexterity', 'knowledge', 'mechanical', 'technical', 'perception', 'control', 'sense', 'alter', 'description'} and re.match(r'"\s*:', text[end:end + 8]):
                    continue
                found.add((form, i))
                yield {'text': text[begin:end], 'form': form, 'offset': begin,
                       'passage': text[max(0, begin - 180):min(len(text), end + 250)]}
                if len(found) >= max_mentions:
                    return

    def candidates(self, text, limit, exclude, current=None):
        form = normalize(text)
        exact = self.exact.get(form, [])
        if current and exact:
            # Representatives are ordered by shared lexical context, not merely an arbitrary ID prefix.
            tokens = self.tokens(current['id'])
            def overlap(rid):
                return len(tokens.intersection(self.tokens(rid)))
            if len(exact) <= 128:
                exact = sorted(exact, key=lambda rid: (-overlap(rid), rid))
        selected = [rid for rid in exact if rid != exclude][:limit]
        return selected, max(0, len(exact) - (exclude in exact) - limit)


def mine(store, limit=0, neighbors=12, mention_limit=24, scope=None, restart=False, annotations=True):
    if neighbors < 1 or neighbors > 64 or mention_limit < 1 or mention_limit > 128:
        raise ValueError('Mining budgets must be bounded: neighbors 1..64, mentions 1..128')
    names = Names(store)
    # Data changes invalidate this cursor without deleting earlier validated jobs.
    revision = store.db.execute("SELECT value FROM meta WHERE key='data_revision'").fetchone()[0]
    snapshot = digest([revision, [list(row) for row in store.db.execute('SELECT id,current_version FROM records WHERE active=1 ORDER BY id')]])
    key = 'mine:' + digest([VERSION, snapshot, neighbors, mention_limit, scope, annotations])
    previous = store.db.execute('SELECT value_json FROM checkpoints WHERE key=?', (key,)).fetchone()
    state = json.loads(previous[0]) if previous and not restart else {'cursor': '', 'complete': False}
    if state.get('complete'):
        return {'already_complete': True, 'processed': 0}
    processed = 0; before = store.db.execute('SELECT count(*) FROM proposals').fetchone()[0]
    query = ('SELECT r.id FROM records r JOIN record_versions v ON v.id=r.current_version JOIN sources s ON s.id=r.source_id '
             'WHERE r.active=1 AND r.id>?')
    params = [state['cursor']]
    if scope:
        query += ' AND (s.dataset LIKE ? OR v.scope_json LIKE ? OR v.body LIKE ?)'
        params += ['%' + scope + '%'] * 3
    query += ' ORDER BY r.id'
    rows = store.db.execute(query, params)
    for row in rows:
        record = store.record(row['id']); rid = record['id']
        if record['kind'] not in NON_SUBJECTS:
            candidates = {}
            for form in [record['name'], *record['aliases']]:
                hits, omitted = names.candidates(form, neighbors, rid, record)
                for target in hits:
                    candidates[target] = (100, ['same_name_or_alias'])
                if omitted:
                    store.gap(rid, 'identity', 'large_name_bucket', {'name': form, 'omitted': omitted, 'budget': neighbors})
                for target in [target for target in names.lexical(normalize(form), neighbors) if target != rid][:neighbors]:
                    candidates.setdefault(target, (20, ['name_fts']))
            # Name trigrams catch near names without requiring embeddings.
            eligible = lambda target: names.metadata[target]['kind'] not in NON_SUBJECTS and {record['universe'], names.metadata[target]['universe']} != {'crossover', 'star_wars'}
            higher_priority = sum(score >= 20 and eligible(target) for target, (score, _) in candidates.items())
            if higher_priority < neighbors:
                for target, score in names.near(record['name'], neighbors):
                    if target != rid:
                        candidates.setdefault(target, (score, ['name_trigrams']))
            else:
                store.gap(rid, 'identity', 'lower_priority_trigrams_deferred', {'higher_priority_candidates': higher_priority, 'candidate_budget': neighbors})
            for target, (score, reasons) in sorted(candidates.items(), key=lambda pair: (-pair[1][0], pair[0]))[:neighbors]:
                other = names.metadata[target]
                if other['kind'] in NON_SUBJECTS or record['universe'] == 'crossover' and other['universe'] == 'star_wars' or other['universe'] == 'crossover' and record['universe'] == 'star_wars':
                    continue
                a, b = sorted([rid, target])
                store.enqueue('identity', a, b, context={'reason': 'lexical_candidate'}, score=score, reasons=reasons)
        if annotations:
            # Classifications remain annotations; the raw adapter's labels are never silently overwritten.
            store.enqueue('type', rid, context={'purpose': 'classify principal subject for retrieval'}, reasons=['record_typing'])
            if record['universe'] in {'unknown', 'crossover'}:
                store.enqueue('universe', rid, context={'purpose': 'separate Star Wars, foreign universes and generic D6'}, reasons=['universe_triage'])
            if record['kind'] in {'person', 'archetype', 'droid_individual'} and re.search('darkstryder|kathol', dumps(record['scope']) + record['name'] + record['body'][:3000], re.I):
                store.enqueue('importance', rid, context={'campaign_scope': 'DarkStryder published campaign and labelled personal adaptations'}, reasons=['campaign_relevance'])
        # Relations from structured fields and anchored occurrences; bodies are bounded for mining with explicit gaps.
        fields = list(field_mentions(record['payload']))
        body = record['body'][:16000]
        occurrences = list(names.occurrences(body, mention_limit))
        if len(record['body']) > len(body):
            store.gap(rid, 'mentions', 'body_budget', {'characters_indexed': len(record['body']), 'characters_mined': len(body)})
        if len(occurrences) >= mention_limit:
            store.gap(rid, 'mentions', 'mention_budget', {'budget': mention_limit})
        for mention in [*fields[:mention_limit], *occurrences]:
            targets, omitted = names.candidates(mention['text'], min(neighbors, 3), rid, record)
            if not targets and 'field' in mention:
                targets = [target for target in names.lexical(normalize(re.sub(r'\([^)]*\)', '', mention['text'])), 2) if target != rid][:2]
            if omitted:
                store.gap(rid, 'mentions', 'ambiguous_referent', {'text': mention['text'], 'omitted': omitted})
            for target in targets:
                other = names.metadata[target]
                if other['kind'] in NON_SUBJECTS or other['normal_name'] == normalize(record['name']):
                    continue
                context = {'mention': mention['text'], 'passage': mention['passage'], 'field': mention.get('field'), 'offset': mention.get('offset')}
                store.enqueue('mention', rid, target, context=context, score=20, reasons=['structured_field' if 'field' in mention else 'text_occurrence'])
                predicate = mention.get('predicate')
                if predicate and predicate != 'instance_of' or predicate == 'instance_of' and record['kind'].endswith('_individual'):
                    relation_context = {**context, 'predicate': predicate}
                    store.enqueue('relation', rid, target, predicate=predicate, context=relation_context, score=30, reasons=['structured_relation'])
        processed += 1; state['cursor'] = rid
        if processed % 100 == 0:
            store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)', (key, dumps(state)));store.db.commit()
        if limit and processed >= limit:
            break
    else:
        state['complete'] = True
    store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)', (key, dumps(state)));store.db.commit()
    after = store.db.execute('SELECT count(*) FROM proposals').fetchone()[0]
    return {'processed': processed, 'new_proposals': after - before, 'complete': state['complete'], 'cursor': state['cursor'], 'snapshot': snapshot}


def apply_decisions(store, max_members=64):
    stats = collections.Counter()
    store.db.execute("DELETE FROM application_queue WHERE proposal_id IN (SELECT q.proposal_id FROM application_queue q JOIN proposals p ON p.id=q.proposal_id WHERE p.status!='accepted')")
    for proposal in store.db.execute("SELECT p.* FROM application_queue q CROSS JOIN proposals p WHERE p.id=q.proposal_id AND p.status='accepted' ORDER BY p.score DESC,p.id"):
        task, decision = proposal['task'], proposal['decision']
        group_parent = json.loads(proposal['context_json']).get('parent_group_id')
        if group_parent:
            parent = store.db.execute('SELECT task,status,decision FROM proposals WHERE id=?', (group_parent,)).fetchone()
            valid = parent and parent['status'] == 'accepted' and (
                task == 'identity' and parent['task'] == 'identity_tuple' and parent['decision'] == 'all_same'
                or task == 'mention' and parent['task'] == 'mention_choice' and parent['decision'].startswith('c'))
            stale_input = store.db.execute('SELECT 1 FROM proposal_inputs i JOIN records r ON r.id=i.record_id WHERE i.proposal_id=? AND (r.active=0 OR r.current_version!=i.version_id) LIMIT 1', (group_parent,)).fetchone()
            if not valid or stale_input:
                store.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (proposal['id'],))
                store.db.execute("UPDATE links SET status='stale' WHERE proposal_id=?", (proposal['id'],))
                store.uncover(proposal['id'])
                store.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (proposal['id'],))
                stats['group_prerequisite_revoked'] += 1
                continue
        prerequisite = json.loads(proposal['context_json']).get('mention_proposal_id')
        if prerequisite:
            prior = store.db.execute('SELECT status,decision FROM proposals WHERE id=?', (prerequisite,)).fetchone()
            if not prior or prior['status'] != 'accepted' or prior['decision'] != 'refers_to':
                store.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (proposal['id'],))
                store.db.execute("UPDATE links SET status='stale' WHERE proposal_id=?", (proposal['id'],))
                stats['prerequisite_revoked'] += 1
                continue
        if task == 'identity':
            if decision == 'same_entity':
                outcome = store.resolve_identity(proposal['id'], max_members)
                stats[outcome] += 1
                if outcome in {'assigned', 'already_assigned'}:
                    store.db.execute("DELETE FROM mining_gaps WHERE record_id=? AND stage='resolve' AND json_extract(details_json,'$.proposal')=?", (proposal['a'], proposal['id']))
                    store.enqueue('merge_utility', proposal['a'], proposal['b'], context={'identity_proposal_id': proposal['id'], 'purpose': 'decide whether synthesis adds useful information'}, reasons=['validated_identity'])
                elif outcome in {'known_conflict', 'manual_conflict', 'group_limit', 'anchor_abstained', 'anchor_manually_rejected'}:
                    store.gap(proposal['a'], 'resolve', 'identity_resolution_blocked', {'proposal': proposal['id'], 'outcome': outcome})
                else:
                    # Unresolved direct-anchor proofs and revisable conflicts must be reconsidered later.
                    continue
            else:
                stats['identity_distinct_or_mention'] += 1
        elif task in {'type', 'classification', 'importance', 'universe'}:
            store.db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?,?,?)', (proposal['a'], 'type' if task == 'classification' else task, proposal['id'], dumps(decision)))
            stats['annotated'] += 1
        elif task == 'relation' and decision == proposal['predicate']:
            store.db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,context_json=excluded.context_json',
                             (proposal['id'], proposal['a'], decision, proposal['b'], proposal['id'], 'accepted', proposal['context_json']))
            stats['relation'] += 1
            store.cover_equivalent_targets(proposal)
        elif task == 'mention' and decision == 'refers_to':
            store.db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,context_json=excluded.context_json',
                             (proposal['id'], proposal['a'], 'mentions', proposal['b'], proposal['id'], 'accepted', proposal['context_json']))
            # A grounded occurrence can now be examined for a specific semantic relation.
            store.enqueue('relation', proposal['a'], proposal['b'], context={**json.loads(proposal['context_json']), 'mention_proposal_id': proposal['id']}, reasons=['resolved_mention'])
            stats['mention'] += 1
            store.cover_equivalent_targets(proposal)
        elif task == 'relation' and decision not in {'none', 'uncertain'} and not proposal['predicate']:
            store.db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,context_json=excluded.context_json',
                             (proposal['id'], proposal['a'], decision, proposal['b'], proposal['id'], 'accepted', proposal['context_json']))
            stats['relation'] += 1
            store.cover_equivalent_targets(proposal)
        store.db.execute('INSERT OR REPLACE INTO applied_decisions VALUES(?,?)', (proposal['id'], digest(proposal['result_json'])))
        store.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (proposal['id'],))
    store.db.commit()
    return dict(stats)
