"""Offline integrity audit of stored imports; does not reverify book citations."""
import json
import collections
from .store import digest


def audit(store):
    if not store.db.in_transaction:
        store.db.execute('BEGIN')
    integrity = [r[0] for r in store.db.execute('PRAGMA integrity_check')]
    foreign_keys = [tuple(r) for r in store.db.execute('PRAGMA foreign_key_check')]
    errors = []; checked = 0
    query = ('SELECT r.id,v.*,p.stats_json FROM records r JOIN record_versions v ON v.id=r.current_version '
             'LEFT JOIN profiles p ON p.version_id=v.id WHERE r.active=1 ORDER BY r.id')
    for row in store.db.execute(query):
        data = {key: row[key] for key in ('name', 'body', 'kind', 'origin', 'universe')}
        for key in ('payload', 'aliases', 'scope', 'locator'):
            data[key] = json.loads(row[key + '_json'])
        data['profile'] = json.loads(row['stats_json']) if row['stats_json'] is not None else None
        if digest(data) != row['sha256']:
            errors.append({'record_id': row['record_id'], 'error': 'version_hash_mismatch'})
        checked += 1
    redirects = {r['id']: r['redirect_to'] for r in store.db.execute('SELECT id,redirect_to FROM entities')}
    for eid, target in redirects.items():
        seen = {eid}
        while target:
            if target in seen:
                errors.append({'entity_id': eid, 'error': 'redirect_cycle'});break
            seen.add(target);target = redirects.get(target)
    mismatches = store.db.execute('SELECT count(*) FROM records r LEFT JOIN record_versions v ON v.id=r.current_version WHERE v.id IS NULL OR v.record_id!=r.id').fetchone()[0]
    if mismatches:errors.append({'error': 'current_version_wrong_owner', 'count': mismatches})
    missing_entities = store.db.execute('SELECT count(*) FROM records r LEFT JOIN entities e ON e.id=r.entity_id WHERE e.id IS NULL').fetchone()[0]
    if missing_entities:errors.append({'error': 'missing_entity', 'count': missing_entities})
    indexed = collections.Counter(r[0] for r in store.db.execute('SELECT record_id FROM records_fts'))
    bad_index = sum(indexed[r[0]] != 1 for r in store.db.execute('SELECT id FROM records WHERE active=1'))
    if bad_index:errors.append({'error': 'missing_or_duplicate_fts_record', 'count': bad_index})
    return {'ok': integrity == ['ok'] and not foreign_keys and not errors, 'integrity': integrity,
            'foreign_key_errors': foreign_keys, 'hashes_checked': checked, 'errors': errors,
            'origins': [dict(r) for r in store.db.execute('SELECT v.origin,v.universe,count(*) records FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.active=1 GROUP BY v.origin,v.universe')],
            'gaps': [dict(r) for r in store.db.execute('SELECT stage,reason,count(*) records FROM mining_gaps GROUP BY stage,reason')],
            'primary_book_verification': 'not_performed_or_required'}
