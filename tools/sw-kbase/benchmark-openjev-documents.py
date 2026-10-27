"""Read-only benchmark of bounded, real pending KBase documents; never applies decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sw_kbase.pipeline import make_job
from packages.sw_kbase.semantic import SemanticClient, encoded
from packages.sw_kbase.store import Store


def prepare(database):
    store = Store(database, readonly=True)
    try:
        selected, seen = [], set()
        for task, count in [('identity', 6), ('type', 2)]:
            candidates = []
            for row in store.db.execute(
                "SELECT * FROM proposals WHERE task=? AND status='pending' "
                "AND (?!='identity' OR json_extract(context_json,'$.kind')='semantic_alias') "
                "ORDER BY id LIMIT 1500", (task, task)):
                job = make_job(store, row)
                size = len(encoded(job['context']))
                if not 600 <= size <= 12000:
                    continue
                records = [job['context'][key] for key in ('TARGET', 'CANDIDATE', 'RECORD')
                           if key in job['context']]
                candidates.append((size, job, records))
            candidates.sort(key=lambda item: item[0])
            if not candidates:
                raise RuntimeError(f'No complete bounded pending {task} jobs found')
            # Spread across short and longer complete records instead of taking only tiny cases.
            for slot in range(count):
                target = int((len(candidates) - 1) * (slot + .5) / count)
                for offset in range(len(candidates)):
                    _, job, records = candidates[(target + offset) % len(candidates)]
                    if job['id'] in seen or any(record['id'] in seen for record in records):
                        continue
                    selected.append(job)
                    seen.add(job['id'])
                    seen.update(record['id'] for record in records)
                    break
        if len(selected) != 8:
            raise RuntimeError(f'Expected 8 complete real jobs, found {len(selected)}')
        return {'database': str(database), 'readonly': True, 'jobs': selected,
                'sample': [{'id': job['id'], 'task': job['task'],
                            'context_chars': len(encoded(job['context'])),
                            'names': [job['context'][key]['name'] for key in
                                      ('TARGET', 'CANDIDATE', 'RECORD') if key in job['context']]}
                           for job in selected]}
    finally:
        store.db.close()


def run(args):
    sample = json.loads(args.jobs.read_text(encoding='utf-8'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(args.url + '/models', timeout=15) as response:
        discovery = json.load(response)
    report = {**sample, 'discovery': discovery, 'revision': args.revision, 'rounds': []}
    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for label, batch_size, jobs in [
        ('first_inference', 1, sample['jobs'][:1]),
        ('sequential', 1, sample['jobs']),
        ('grouped_first', 4, sample['jobs']),
        ('grouped_repeat', 4, sample['jobs']),
        ('sequential_repeat', 1, sample['jobs']),
    ]:
        client = SemanticClient({'semantic_url': args.url, 'semantic_revision': args.revision,
                                 'semantic_batch_size': batch_size, 'timeout': 600, 'attempts': 1})
        requests = []
        original_post = client.http.post
        def measured_post(route, payload):
            started = time.perf_counter()
            item = {'questions': len(payload['questions']), 'payload_chars': len(encoded(payload))}
            try:
                result = original_post(route, payload)
                item['response'] = result
                return result
            except Exception as exc:
                item['error'] = repr(exc)
                raise
            finally:
                item['seconds'] = time.perf_counter() - started
                requests.append(item)
                save()
        client.http.post = measured_post
        started = time.perf_counter()
        item = {'label': label, 'batch_size': batch_size, 'questions': len(jobs), 'requests': requests}
        report['active_round'] = item
        try:
            results = client.evaluate(jobs)
            item['results'] = results
            item['inferred'] = sum(result.get('inference_performed', True) for result in results)
            item['accepted'] = sum(result['accepted'] for result in results)
        except Exception as exc:
            item['error'] = repr(exc)
        item['seconds'] = time.perf_counter() - started
        item['http_requests'] = len(requests)
        item['questions_per_second'] = item.get('inferred', 0) / item['seconds']
        if requests:
            item['http_median_seconds'] = statistics.median(r['seconds'] for r in requests)
        report['rounds'].append(item)
        del report['active_round']
        save()
        print(json.dumps({k: v for k, v in item.items() if k not in {'requests', 'results'}}, ensure_ascii=False), flush=True)
        if 'error' in item:
            return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--jobs', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--url', default='http://127.0.0.1:8000/v1')
    parser.add_argument('--revision')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        if not args.database:
            parser.error('--database is required to prepare the sample')
        report = prepare(args.database)
        args.jobs.parent.mkdir(parents=True, exist_ok=True)
        args.jobs.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report['sample'], ensure_ascii=False, indent=2))
        return 0
    if not args.revision or not args.output:
        parser.error('--revision and --output are required to run inference')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
