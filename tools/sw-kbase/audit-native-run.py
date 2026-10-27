"""Read-only audit of a native document run; never performs inference."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))
from sw_kbase.curation_store import active_run, snapshot
from sw_kbase.disambiguation import fingerprint
from sw_kbase.store import Store


def percentile(values, fraction):
    return sorted(values)[int((len(values) - 1) * fraction)] if values else None


def audit(store, run_id, folder, verify=False):
    run = store.db.execute('SELECT * FROM jev_v1_runs WHERE id=?', (run_id,)).fetchone()
    if not run:
        raise ValueError('Unknown run')
    counts = Counter(); selections = Counter(); observations = Counter(); proofs = Counter()
    models = cache_hits = source_count = anchors = 0
    issues = []; errors = []; references = set(); excluded = set(); retained = set(); held = set()
    for job in store.db.execute('SELECT * FROM jev_v1_jobs WHERE run_id=? ORDER BY id', (run_id,)):
        counts[job['status']] += 1
        if job['error']:
            errors.append({'job_id': job['id'], 'error': job['error']})
        if verify and fingerprint(snapshot(store, job['kind'], job['subject_id'], job['object_id'])) != job['input_hash']:
            issues.append(job['id'])
        if job['status'] != 'complete':
            continue
        result = json.loads(job['result_json'])
        models += result.get('model_calls', 0); cache_hits += result.get('cache_hits', 0)
        if result.get('reference_id'):
            references.add(result['reference_id']); anchors += 1
        for selection in result.get('source_selection', []):
            source_count += 1
            verdict = selection['merge_selection']; reason = selection['reason']
            selections[verdict + ':' + reason] += 1
            {'retain': retained, 'exclude_redundant': excluded, 'hold': held}[verdict].add(selection['source_id'])
            for proof in selection.get('unit_proofs', []):
                proofs[proof.get('selection', 'unspecified')] += 1
                if proof.get('selection') == 'semantic_coverage' and proof.get('scores_pct', {}).get('no', 0) <= 80:
                    issues.append(job['id'] + ':semantic-proof-not-strictly-above-80')
        for observation in result.get('observations', []):
            if observation.get('inference_performed') is False:
                observations[observation.get('reason', 'unspecified')] += 1
    request_states = Counter()
    deferred = Counter(); tasks = Counter(); unique_requests = 0
    for request in store.db.execute('SELECT DISTINCT q.id,q.status,q.request_json,q.response_json '
            'FROM jev_v1_job_requests x JOIN jev_v1_jobs j ON j.id=x.job_id '
            'JOIN jev_v1_requests q ON q.id=x.request_id WHERE j.run_id=?', (run_id,)):
        unique_requests += 1; request_states[request['status']] += 1
        payload = json.loads(request['request_json'])
        for task in payload.get('questions', {}):
            tasks[task] += 1
        response = json.loads(request['response_json']) if request['response_json'] else {}
        if response.get('deferred_reason'):
            deferred[response['deferred_reason']] += 1
    rows = []
    log = folder / 'requests.jsonl'
    if log.exists():
        for line in log.read_text(encoding='utf-8').splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if verify:
                    raise
    latencies = [row['latency_ms'] for row in rows]
    started = json.loads((folder / 'started.json').read_text(encoding='utf-8'))
    progress = json.loads((folder / 'progress.json').read_text(encoding='utf-8'))
    duration = (datetime.fromisoformat(progress['utc']) - datetime.fromisoformat(started['utc'])).total_seconds()
    complete = counts.get('complete', 0) == sum(counts.values()) and run['status'] == 'complete' and progress['stage'] == 'complete'
    if active_run(store) != run['cleaning_run']:
        issues.append('cleaning-run-changed')
    if references & excluded or retained & excluded or held & excluded:
        issues.append('selection-sets-overlap')
    singletons = store.db.execute('SELECT count(*) FROM (SELECT aggregate_id FROM aggregate_members '
                                  'GROUP BY aggregate_id HAVING count(*)=1)').fetchone()[0]
    return {
        'utc': datetime.now(timezone.utc).isoformat(), 'run_id': run_id,
        'complete': complete, 'snapshot_verified': verify, 'issues': issues, 'job_errors': errors,
        'run_status': run['status'], 'job_statuses': dict(counts),
        'records': store.db.execute('SELECT count(*) FROM records').fetchone()[0],
        'aggregates': store.db.execute('SELECT count(*) FROM aggregates').fetchone()[0],
        'anchors': anchors, 'candidate_documents': source_count,
        'documents_retained_including_anchors': len(retained | references),
        'retained_count_scope': 'completed multi-document jobs only',
        'single_document_aggregates_outside_run': singletons,
        'all_documents_selected_for_merge': len(retained | references) + singletons if complete else None,
        'documents_excluded_from_merge': len(excluded), 'documents_held': len(held),
        'selections': dict(selections), 'unit_proofs': dict(proofs),
        'deferred_observations': dict(observations), 'unique_request_states': dict(request_states),
        'unique_deferred_requests': dict(deferred), 'unique_requests': unique_requests,
        'request_tasks': dict(tasks), 'completed_jobs_model_calls': models,
        'completed_jobs_cache_hits': cache_hits, 'actual_native_calls_this_execution': len(rows),
        'input_tokens_this_execution': sum(sum(r['question_tokens'].values()) for r in rows),
        'elapsed_seconds': duration, 'inference_seconds': sum(latencies) / 1000,
        'latency_ms': {'mean': statistics.mean(latencies) if latencies else None,
                       'p50': percentile(latencies, .5), 'p95': percentile(latencies, .95),
                       'min': min(latencies) if latencies else None, 'max': max(latencies) if latencies else None},
        'pause_file_present': (store.path.parent / 'openjev.pause').exists(),
        'paragraph_pass_executed_by_this_script': False, 'database_modified_by_this_script': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--run-output', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--verify-snapshots', action='store_true')
    args = parser.parse_args()
    store = Store(args.database, readonly=True)
    try:
        report = audit(store, args.run_id, args.run_output, args.verify_snapshots)
    finally:
        store.close()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.verify_snapshots and (not report['complete'] or report['issues'] or report['job_errors']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
