"""Read-only, bounded plans for fewer semantic questions. No verdicts or writes.

Plans are single-use iterables. Their metrics are final after consumption. A runner
must revalidate versions, entity snapshots and pending statuses before execution.
``complete`` on a group means its candidate list was not truncated by this planner;
it does not certify that a stored occurrence excerpt contains every relevant fact.
``proposal_ids`` denotes directly represented questions; ``family_proposal_ids``
is informational and must never be treated as an inferred identity closure.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
import json

from .store import dumps, normalize, stable

PLANNER_VERSION = 'group-planner/1'


@dataclass
class GroupPlan:
    groups: object
    metrics: dict

    def __iter__(self):
        return iter(self.groups)


class _Planner:
    def __init__(self, store, limit, scope, max_members, max_chars, tasks, candidate_window):
        if limit < 0 or candidate_window < 0 or not 3 <= max_members <= 32 or max_chars < 500:
            raise ValueError('Invalid grouped planning bounds')
        self.db = store.db
        self.limit, self.scope = limit, scope
        self.max_members, self.max_chars = max_members, max_chars
        self.tasks = set(tasks or ('identity_tuple', 'mention_choice'))
        if not self.tasks <= {'identity_tuple', 'mention_choice'}:
            raise ValueError('Unknown grouped task')
        self.candidate_window = candidate_window
        self.metrics = {'planner_version': PLANNER_VERSION, 'groups': 0,
                        'by_task': {}, 'proposal_ids': 0, 'estimated_questions_saved': 0,
                        'scanned_proposals': 0, 'skipped': {}, 'gap_examples': [],
                        'complete': False, 'limit_reached': False, 'window_limited': False}
        self.records, self.entities = {}, {}
        self._evidence_cache = OrderedDict()

    def skip(self, reason, **details):
        counts = self.metrics['skipped']; counts[reason] = counts.get(reason, 0) + 1
        if len(self.metrics['gap_examples']) < 20:
            self.metrics['gap_examples'].append({'reason': reason, **details})

    def load(self):
        # Metadata only; no corpus bodies or payloads retained in RAM.
        rows = self.db.execute('SELECT r.id,r.source_id,r.current_version,r.entity_id,'
                               'v.name,v.kind,v.universe,length(v.body) body_chars '
                               'FROM records r JOIN record_versions v ON v.id=r.current_version '
                               'WHERE r.active=1').fetchall()
        self.records = {r['id']: dict(r) for r in rows}
        self.by_entity = defaultdict(list)
        for row in rows:
            self.by_entity[row['entity_id']].append(row['id'])
        self.entities = {r['id']: dict(r) for r in self.db.execute(
            'SELECT id,anchor_record_id,revision,redirect_to FROM entities')}
        self.scope_ids = None
        if self.scope:
            pattern = '%' + self.scope + '%'
            self.scope_ids = {r[0] for r in self.db.execute(
                'SELECT r.id FROM records r JOIN record_versions v ON v.id=r.current_version '
                'JOIN sources s ON s.id=r.source_id WHERE r.active=1 AND '
                '(s.dataset LIKE ? OR v.scope_json LIKE ? OR v.body LIKE ?)', (pattern,) * 3)}

    def in_scope(self, rows):
        return self.scope_ids is None or any(p['a'] in self.scope_ids or p['b'] in self.scope_ids for p in rows)

    def snapshot(self, entity_ids):
        result = {}
        for eid in sorted(entity_ids):
            entity = self.entities.get(eid)
            if not entity or entity['redirect_to'] or entity['anchor_record_id'] not in self.records:
                return None
            result[eid] = {'anchor_record_id': entity['anchor_record_id'], 'revision': entity['revision'],
                           'members': {rid: self.records[rid]['current_version']
                                       for rid in sorted(self.by_entity[eid])}}
        return result

    def evidence(self, rid, excerpt=None):
        cache_key = (rid, excerpt)
        if cache_key in self._evidence_cache:
            self._evidence_cache.move_to_end(cache_key)
            return self._evidence_cache[cache_key]
        body_sql = 'body' if excerpt is None else 'substr(body,1,?) AS body'
        params = (self.records[rid]['current_version'],) if excerpt is None else (excerpt, self.records[rid]['current_version'])
        row = self.db.execute('SELECT name,kind,universe,origin,aliases_json,scope_json,sha256,' + body_sql +
                              ' FROM record_versions WHERE id=?', params).fetchone()
        if row is None:
            return None
        value = {k: row[k] for k in ('name', 'kind', 'universe', 'origin', 'sha256')}
        value.update(id=rid, aliases=json.loads(row['aliases_json']), scope=json.loads(row['scope_json']))
        if excerpt is None:
            value['body'] = row['body']
        else:
            value['description_excerpt'] = row['body'][:excerpt]
            value['excerpt_only'] = self.records[rid]['body_chars'] > excerpt
        self._evidence_cache[cache_key] = value
        if len(self._evidence_cache) > 4096:
            self._evidence_cache.popitem(last=False)
        return value

    def finish(self, task, record_ids, proposals, context, snapshots, evidence, **extra):
        ids = sorted(set(record_ids))
        versions = {rid: self.records[rid]['current_version'] for rid in ids}
        chars = len(dumps(evidence))
        if chars > self.max_chars:
            self.skip(task + '_budget', chars=chars, proposal_ids=[p['id'] for p in proposals[:3]])
            return None
        proposal_ids = sorted({p['id'] for p in proposals})
        key = [PLANNER_VERSION, task, versions, snapshots, context, proposal_ids]
        return {'id': stable('group', *key), 'task': task, 'record_ids': ids,
                'versions': versions, 'source_ids': {rid: self.records[rid]['source_id'] for rid in ids},
                'proposal_ids': proposal_ids, 'context': context, 'entity_snapshots': snapshots,
                'chars': chars, 'complete': True, 'requires_revalidation': True, **extra}

    def conflicts(self, members):
        # All members of the represented identities participate in exclusions,
        # even when only canonical anchors will be shown to the classifier.
        marks = ','.join('?' for _ in members)
        args = (*members, *members)
        if self.db.execute(f'SELECT 1 FROM cannot_links WHERE a IN ({marks}) AND b IN ({marks}) LIMIT 1', args).fetchone():
            return 'manual_conflict'
        if self.db.execute(f"SELECT 1 FROM proposals INDEXED BY proposal_pair WHERE task='identity' AND status='accepted' "
                           f"AND decision IN ('different_entity','mention_only') AND a IN ({marks}) AND b IN ({marks}) LIMIT 1", args).fetchone():
            return 'accepted_conflict'
        return None

    def identity_groups(self):
        families = defaultdict(list)
        for rid, record in self.records.items():
            if record['kind'] == 'mixed_document':
                continue
            name = normalize(record['name'])
            if name:
                families[(name, record['kind'], record['universe'])].append(rid)
        seen = set()
        for family, ids in sorted(families.items()):
            if len(ids) < 3:
                continue
            if len(ids) > self.max_members:
                self.skip('identity_family_size', size=len(ids)); continue
            eids = {self.records[rid]['entity_id'] for rid in ids}
            if len(eids) < 3:
                self.skip('identity_fewer_than_three_entities'); continue
            marks = ','.join('?' for _ in ids)
            proposals = [dict(p) for p in self.db.execute(
                f"SELECT id,a,b FROM proposals INDEXED BY proposal_pair WHERE task='identity' AND status='pending' AND a IN ({marks}) AND b IN ({marks})",
                (*ids, *ids))]
            if not proposals or not self.in_scope(proposals):
                continue
            self.metrics['scanned_proposals'] += len(proposals)
            snapshots = self.snapshot(eids)
            if snapshots is None:
                self.skip('identity_missing_anchor'); continue
            anchors = sorted(s['anchor_record_id'] for s in snapshots.values())
            if tuple(anchors) in seen:
                continue
            seen.add(tuple(anchors))
            members = sorted({rid for s in snapshots.values() for rid in s['members']})
            conflict = self.conflicts(members)
            if conflict:
                self.skip('identity_' + conflict); continue
            if sum(self.records[rid]['body_chars'] for rid in anchors) > self.max_chars:
                self.skip('identity_tuple_budget'); continue
            # Only edges explicitly evaluated by this tuple can be covered.
            directly = [p for p in proposals if p['a'] in anchors and p['b'] in anchors]
            # Match resolver preference: largest existing group, then entity ID.
            anchor_entity = min(snapshots, key=lambda eid: (-len(snapshots[eid]['members']), eid))
            chosen_anchor = snapshots[anchor_entity]['anchor_record_id']
            context = {'anchor_record_id': chosen_anchor, 'record_ids': anchors,
                       'family': {'name': family[0], 'kind': family[1], 'universe': family[2]}}
            evidence = {'records': [self.evidence(rid) for rid in anchors], **context}
            group = self.finish('identity_tuple', anchors, directly, context, snapshots, evidence,
                                anchor_record_id=chosen_anchor, family_record_ids=sorted(ids),
                                family_proposal_ids=sorted(p['id'] for p in proposals))
            if group:
                yield group

    def mention_groups(self):
        # Per-subject index probes avoid sorting millions of passage-bearing rows
        # or keeping the complete mention queue in memory/a long read snapshot.
        scanned = 0
        for subject in sorted(self.records):
            rows = [dict(p) for p in self.db.execute(
                "SELECT id,a,b,predicate,context_json FROM proposals INDEXED BY proposal_pair "
                "WHERE a=? AND task='mention' AND status='pending'", (subject,))]
            if not rows:
                continue
            self.metrics['scanned_proposals'] += len(rows)
            scanned += len(rows)
            occurrences = defaultdict(list)
            for p in rows:
                context = json.loads(p['context_json'])
                context.pop('mention_proposal_id', None)
                occurrences[(p['predicate'], dumps(context))].append(p)
            for (predicate, encoded_context), proposals in sorted(occurrences.items()):
                if len(proposals) < 2 or not self.in_scope(proposals):
                    continue
                if any(p['b'] not in self.records for p in proposals):
                    self.skip('mention_missing_candidate'); continue
                context = json.loads(encoded_context)
                if not isinstance(context.get('passage'), str) or not context['passage'].strip():
                    self.skip('mention_missing_passage'); continue
                by_entity = defaultdict(list)
                for p in proposals:
                    by_entity[self.records[p['b']]['entity_id']].append(p)
                if len(by_entity) > self.max_members:
                    self.skip('mention_candidate_size', size=len(by_entity)); continue
                snapshots = self.snapshot(by_entity)
                if snapshots is None:
                    self.skip('mention_missing_anchor'); continue
                candidates = []
                for eid, ps in sorted(by_entity.items()):
                    candidates.append({'entity_id': eid, 'record_id': snapshots[eid]['anchor_record_id'],
                                       'target_record_ids': sorted({p['b'] for p in ps}),
                                       'member_record_ids': sorted({p['b'] for p in ps}),
                                       'proposal_ids': sorted(p['id'] for p in ps)})
                anchors = [candidate['record_id'] for candidate in candidates]
                if len(dumps(context)) > self.max_chars:
                    self.skip('mention_choice_budget'); continue
                context = {**context, 'subject_record_id': subject, 'predicate': predicate,
                           'candidate_entity_ids': sorted(by_entity), 'candidate_set_exhaustive': False,
                           'candidate_evidence': 'bounded_descriptions', 'passage_truncated_by_planner': False,
                           'evidence_scope': 'stored_occurrence_excerpt'}
                evidence = {'SUBJECT': self.evidence(subject, excerpt=1200), 'occurrence': context,
                            'candidates': [self.evidence(rid, excerpt=2000) for rid in anchors]}
                # Retain versions of original target profiles as well as representatives.
                ids = [subject, *anchors, *(p['b'] for p in proposals)]
                group = self.finish('mention_choice', ids, proposals, context, snapshots, evidence,
                                    subject_record_id=subject, candidates=candidates)
                if group:
                    yield group
            if self.candidate_window and scanned >= self.candidate_window:
                self.metrics['window_limited'] = True
                return

    def run(self):
        self.load()
        for task, generator in (('identity_tuple', self.identity_groups), ('mention_choice', self.mention_groups)):
            if task not in self.tasks:
                continue
            for group in generator():
                self.metrics['groups'] += 1
                self.metrics['by_task'][task] = self.metrics['by_task'].get(task, 0) + 1
                self.metrics['proposal_ids'] += len(group['proposal_ids'])
                # A group with no directly represented pending pair costs one
                # additional question; retain that cost in the net estimate.
                self.metrics['estimated_questions_saved'] += len(group['proposal_ids']) - 1
                yield group
                if self.limit and self.metrics['groups'] >= self.limit:
                    self.metrics['limit_reached'] = True
                    return
        self.metrics['complete'] = not self.metrics['window_limited']


def plan_groups(store, limit=0, scope=None, max_members=8, max_chars=12000, tasks=None, candidate_window=0):
    """Plan without enqueueing. Bounds skip whole groups, never truncate evidence.

    ``limit`` caps emitted groups; ``candidate_window`` caps mention scan work at
    subject boundaries (an occurrence is never split). Zero means unlimited.
    Original occurrence text is never truncated; candidate descriptions use the
    explicit 1200/2000-character windows used by the binary mention client.
    Groups do not claim candidate-set exhaustiveness or inferred pair negatives.
    """
    planner = _Planner(store, limit, scope, max_members, max_chars, tasks, candidate_window)
    return GroupPlan(planner.run(), planner.metrics)


def analyze_groups(store, sample_limit=5, **kwargs):
    """Consume a streaming plan; keep only bounded metadata examples."""
    if not 0 <= sample_limit <= 100:
        raise ValueError('sample_limit must be in 0..100')
    plan = plan_groups(store, **kwargs)
    examples = []
    for group in plan:
        if len(examples) < sample_limit:
            examples.append({key: group[key] for key in ('id', 'task', 'record_ids', 'proposal_ids', 'chars')})
    return {'metrics': plan.metrics, 'examples': examples}
