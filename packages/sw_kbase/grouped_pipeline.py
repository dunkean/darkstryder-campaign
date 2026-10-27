"""Tuple/occurrence inference with reversible, explicitly derived pair proofs."""
from collections import Counter, deque
from itertools import combinations
import json
import os
import time

from .grouping import plan_groups
from .mining import apply_decisions
from .pipeline import record_context, classifier_signature, make_job
from .semantic import SemanticClient
from .store import digest, dumps


def current_group(store, group):
    for rid, version in group['versions'].items():
        row = store.db.execute('SELECT current_version,active FROM records WHERE id=?', (rid,)).fetchone()
        if not row or not row['active'] or row['current_version'] != version:
            return False
    for eid, snapshot in group.get('entity_snapshots', {}).items():
        row = store.db.execute('SELECT anchor_record_id,revision,redirect_to FROM entities WHERE id=?', (eid,)).fetchone()
        if not row or row['redirect_to'] or row['revision'] != snapshot['revision'] or row['anchor_record_id'] != snapshot['anchor_record_id']:
            return False
        members = dict(store.db.execute('SELECT id,current_version FROM records WHERE entity_id=? AND active=1', (eid,)))
        if members != snapshot['members']:
            return False
    return True


def group_job(store, group, pid):
    if group['task'] == 'identity_tuple':
        context = {'RECORDS': [record_context(store, rid) for rid in group['record_ids']],
                   'family': group['context'].get('family', {})}
    else:
        occurrence = group['context'].get('occurrence', group['context'])
        context = {'MENTION': occurrence,
                   'SUBJECT': record_context(store, group['subject_record_id'], 1200),
                   'CANDIDATES': [{**record_context(store, candidate['record_id'], 2000),
                                   'entity_id': candidate['entity_id']}
                                  for candidate in group['candidates']],
                   'candidate_set_exhaustive': False}
    return {'id': pid, 'task': group['task'], 'context': context}


def register_group(store, group):
    subject = group.get('anchor_record_id') or group['subject_record_id']
    pid = store.enqueue(group['task'], subject, context={'group': group, 'group_id': group['id']},
                        score=150, reasons=['text_mining_group'])
    store.db.executemany('INSERT OR REPLACE INTO proposal_inputs VALUES(?,?,?)',
                         ((pid, rid, version) for rid, version in group['versions'].items()))
    return pid


def apply_group(store, group, pid, verdict, client):
    """Only a positive all-same/selected-entity observation entails pair proofs."""
    parent = store.db.execute('SELECT task,status,result_json,context_json FROM proposals WHERE id=?', (pid,)).fetchone()
    if (not parent or parent['task'] != group['task'] or parent['status'] not in {'accepted', 'uncertain'}
            or parent['result_json'] != dumps(verdict) or dumps(json.loads(parent['context_json']).get('group')) != dumps(group)):
        raise ValueError('Grouped application requires the matching persisted parent observation')
    if not current_group(store, group):
        return {'fallback_groups': 1}
    if group['task'] == 'mention_choice' and (not verdict.get('accepted') or verdict['decision'] in {'none', 'generic_usage'}):
        unresolved = not verdict.get('accepted')
        state = 'deferred_by_group' if unresolved else 'resolved_by_group'
        reason = ('Grouped evidence is insufficient; retain all candidates for review without repeated inference'
                  if unresolved else 'No listed particular candidate applies to this occurrence; no pairwise inequality asserted')
        count = 0
        for original in group['proposal_ids']:
            proof = {'derived': True, 'reviewed': False, 'covered_by_proposal': pid,
                     'unresolved': unresolved, 'reason': reason, 'candidate_set': group['candidates'],
                     'evidence_versions': group['versions']}
            cursor = store.db.execute("UPDATE proposals SET status=?,result_json=? WHERE id=? AND status='pending'", (state, dumps(proof), original))
            count += cursor.rowcount
        return {'deferred_unresolved' if unresolved else 'resolved_without_link': count}
    if not verdict.get('accepted'):
        return {'fallback_groups': 1}
    decision = verdict['decision']
    children = []
    if group['task'] == 'identity_tuple' and decision == 'all_same':
        # all_same explicitly assesses every supplied member together. Each
        # represented pair follows from that single assertion, not a chain of
        # independently inferred A=B, B=C links.
        for a, b in combinations(sorted(group['record_ids']), 2):
            child = store.enqueue('identity', a, b,
                                  context={'reason': 'tuple_all_same', 'parent_group_id': pid},
                                  score=200, reasons=['explicit_tuple_identity'])
            children.append((child, 'same_entity'))
    elif group['task'] == 'mention_choice' and decision.startswith('c') and decision[1:].isdigit():
        index = int(decision[1:])
        if index >= len(group['candidates']):
            raise ValueError('Unknown selected candidate')
        candidate = group['candidates'][index]
        occurrence = dict(group['context'].get('occurrence', group['context']))
        occurrence['parent_group_id'] = pid
        child = store.enqueue('mention', group['subject_record_id'], candidate['record_id'],
                              predicate=group['context'].get('predicate', ''), context=occurrence,
                              reasons=['explicit_candidate_choice'])
        children.append((child, 'refers_to'))
    else:
        # mixed, none, generic and uncertainty never imply pairwise inequality.
        return {'fallback_groups': 1}
    for child, label in children:
        proposal = store.db.execute('SELECT * FROM proposals WHERE id=?', (child,)).fetchone()
        proof = {key: value for key, value in verdict.items() if key not in {'response', 'id', 'task', 'fingerprint'}}
        proof.update(id=child, task=proposal['task'], decision=label, raw_decision=label,
                     derived=True, derived_from_group=pid, group_observation=decision,
                     inference_performed=False, fingerprint=client.signature(make_job(store, proposal)),
                     reason='Direct entailment of the supplied tuple/candidate choice; not a separate model call')
        store.store_result(child, proof)
    resolution = apply_decisions(store)
    # Preserve every original hypothesis and its parent evidence. Identity pairs
    # are covered only after the resolver has actually assigned both endpoints.
    covered = 0
    for original in group['proposal_ids']:
        proposal = store.db.execute('SELECT * FROM proposals WHERE id=?', (original,)).fetchone()
        if not proposal or proposal['status'] != 'pending':
            continue
        if group['task'] == 'identity_tuple':
            entities = list(store.db.execute('SELECT entity_id FROM records WHERE id IN (?,?)', (proposal['a'], proposal['b'])))
            if len(entities) != 2 or entities[0][0] != entities[1][0]:
                continue
        proof = {'derived': True, 'reviewed': False, 'covered_by_proposal': pid,
                 'reason': 'Same exact tuple or single-referent occurrence validated by grouped inference'}
        store.db.execute("UPDATE proposals SET status='covered_by_group',result_json=? WHERE id=? AND status='pending'", (dumps(proof), original))
        covered += 1
    return {'derived_pair_proofs': len(children), 'covered_proposals': covered, 'resolution': resolution}


def curate_groups(store, config, limit=100, scope=None, tasks=None, max_members=8, max_chars=12000):
    client = SemanticClient(config)
    contract = classifier_signature(client)
    previous = store.db.execute("SELECT value FROM meta WHERE key='semantic_contract'").fetchone()
    if previous and previous[0] != contract:
        raise ValueError('Semantic contract changed: an explicit continue-contract transition is required')
    store.db.execute("INSERT OR REPLACE INTO meta VALUES('semantic_contract',?)", (contract,))
    store.db.commit()
    plan = plan_groups(store, scope=scope, max_members=max_members, max_chars=max_chars, tasks=tasks)
    metrics = Counter(); observations = deque(maxlen=20); start = time.monotonic()
    def report(state):
        value = {**metrics, 'state': state, 'seconds': round(time.monotonic() - start, 3),
                 'plan': plan.metrics, 'observations': list(observations), 'writer_started': False}
        target = store.path.parent / 'openjev-group-progress.json'
        temporary = target.with_suffix('.json.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            stream.flush();os.fsync(stream.fileno())
        temporary.replace(target)
        return value
    report('running')
    for group in plan:
        if (store.path.parent / 'openjev.pause').exists():
            metrics['paused'] += 1; break
        if limit and metrics['groups_evaluated'] >= limit:
            break
        if not current_group(store, group):
            metrics['stale_plans'] += 1; continue
        pid = register_group(store, group)
        store.db.commit()
        row = store.db.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()
        if row['status'] in {'accepted', 'uncertain', 'manual_rejected'}:
            metrics['cached_groups'] += 1; continue
        job = group_job(store, group, pid)
        try:
            before = time.monotonic()
            verdict = client.evaluate([job])[0]
            store.store_result(pid, verdict)
            application = apply_group(store, group, pid, verdict, client)
            store.db.commit()
        except Exception as error:
            store.db.rollback()
            store.db.execute("UPDATE proposals SET status='error',error=? WHERE id=?", (str(error)[:2000], pid))
            store.db.commit()
            raise
        metrics['groups_evaluated'] += 1
        metrics['model_questions'] += int(verdict.get('inference_performed') is not False)
        metrics['represented_proposals'] += len(group['proposal_ids'])
        metrics['derived_pair_proofs'] += application.get('derived_pair_proofs', 0)
        metrics['covered_proposals'] += application.get('covered_proposals', 0)
        metrics['fallback_groups'] += application.get('fallback_groups', 0)
        metrics['deferred_unresolved'] += application.get('deferred_unresolved', 0)
        metrics['resolved_without_link'] += application.get('resolved_without_link', 0)
        observations.append({'group_id': group['id'], 'proposal_id': pid, 'task': group['task'],
                             'decision': verdict['decision'], 'represented': len(group['proposal_ids']),
                             'seconds': round(time.monotonic() - before, 3), 'application': application})
        report('running')
    return report('paused' if metrics['paused'] else 'budget_reached' if limit and metrics['groups_evaluated'] >= limit else 'plan_exhausted')
