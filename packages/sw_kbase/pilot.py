"""Bounded OpenJev pilot while CPU mining continues; import decisions by exact fingerprints."""
import json
from pathlib import Path
from .pipeline import classifier_signature, make_job, scope_selection, SCOPE_MATCH
from .semantic import SemanticClient
from .store import Store


def prepare(main, directory, limit=100, tasks=None, scope=None):
    target = Path(directory).resolve()
    if target.exists():raise FileExistsError('Pilot snapshots are immutable; choose a new directory')
    if not 1 <= limit <= 10000:raise ValueError('Pilot limit must be in 1..10000')
    prefix, params = scope_selection(scope, main)
    query = prefix + "SELECT p.* FROM proposals p WHERE p.status='pending'"
    if tasks:
        query += ' AND p.task IN (' + ','.join('?' for _ in tasks) + ')';params.extend(tasks)
    if scope:
        query += SCOPE_MATCH
    query += ' ORDER BY p.score DESC,p.a,p.id LIMIT ?';params.append(limit)
    if not main.db.in_transaction:main.db.execute('BEGIN')
    proposals = main.db.execute(query, params).fetchall()
    target.mkdir(parents=True)
    pilot = Store(target / 'knowledge.sqlite')
    try:
        ids = {row[key] for row in proposals for key in ('a', 'b') if row[key]}
        for rid in sorted(ids):
            record = main.record(rid)
            source = main.db.execute('SELECT * FROM sources WHERE id=?', (record['source_id'],)).fetchone()
            sid = pilot.source(source['dataset'], source['title'], source['origin'], json.loads(source['metadata_json']))
            profile = main.db.execute('SELECT stats_json FROM profiles WHERE version_id=?', (record['current_version'],)).fetchone()
            copied, _ = pilot.ingest(sid, record['source_key'], record['name'], record['body'], record['payload'], record['kind'],
                                     record['aliases'], record['origin'], record['universe'], record['scope'], record['locator'],
                                     json.loads(profile[0]) if profile else None)
            if copied != rid or pilot.record(copied)['current_version'] != record['current_version']:
                raise ValueError('Pilot copy changed source identity or version')
        for row in proposals:
            copied = pilot.enqueue(row['task'], row['a'], row['b'], row['predicate'], json.loads(row['context_json']), row['score'], json.loads(row['reasons_json']))
            if copied != row['id']:raise ValueError('Pilot copy changed a semantic job')
        pilot.db.commit()
        receipt = {'main_database': str(main.path.resolve()), 'pilot_database': str(pilot.path), 'records': len(ids), 'jobs': len(proposals),
                   'purpose': 'OpenJev classification only, no document synthesis or Strata'}
        (target / 'pilot.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return receipt
    finally:
        pilot.close()


def import_decisions(main, pilot_path, config):
    client = SemanticClient(config);contract = classifier_signature(client)
    pilot = Store(pilot_path, readonly=True)
    try:
        actual = pilot.db.execute("SELECT value FROM meta WHERE key='semantic_contract'").fetchone()
        previous = main.db.execute("SELECT value FROM meta WHERE key='semantic_contract'").fetchone()
        if actual is None or actual[0] != contract or previous and previous[0] != contract:
            raise ValueError('Pilot/main semantic contracts differ; no decisions imported')
        imported = skipped = 0
        for row in pilot.db.execute("SELECT * FROM proposals WHERE status IN ('accepted','uncertain') AND result_json IS NOT NULL"):
            current = main.db.execute("SELECT * FROM proposals WHERE id=? AND status!='stale'", (row['id'],)).fetchone()
            result = json.loads(row['result_json'])
            if current is None or result.get('fingerprint') != client.signature(make_job(main, current)):
                skipped += 1;continue
            if current['status'] not in {'pending', 'error'}:
                skipped += 1;continue
            main.store_result(current['id'], result);imported += 1
        main.db.execute("INSERT OR REPLACE INTO meta VALUES('semantic_contract',?)", (contract,))
        main.db.commit()
        return {'imported': imported, 'skipped_stale_or_already_decided': skipped, 'writer_started': False}
    finally:
        pilot.close()
