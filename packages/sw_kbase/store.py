"""Versioned import, search and reversible entity assignment; stdlib only."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path

from . import VERSION

NAMESPACE = uuid.UUID('a1221a4d-ef8c-5091-9cdc-45eb5b8c1917')
RESOLUTION_POLICY = 'direct-anchor/2-neutral-abstentions'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def stable(prefix, *parts):
    return prefix + '-' + str(uuid.uuid5(NAMESPACE, dumps(parts)))


def normalize(value):
    text = str(value).casefold()
    if not text.isascii():
        text = unicodedata.normalize('NFKD', text)
        text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text, re.UNICODE))


def grams(value):
    text = ' ' + normalize(value) + ' '
    return {text[i:i + 3] for i in range(max(0, len(text) - 2))}


@contextlib.contextmanager
def process_lock(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'writer.lock').open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if __import__('sys').platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another SW knowledge writer is running') from error
        yield


def confined(base, relative):
    path = (Path(base) / relative).resolve()
    path.relative_to(Path(base).resolve())
    return path


class Store:
    def __init__(self, path, readonly=False):
        self.path = Path(path)
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA cache_size=' + ('-32768' if readonly else '-131072'))
        if not readonly:
            self.db.execute('PRAGMA synchronous=NORMAL')
            self.db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            existing = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if existing and existing[0] != VERSION:
                raise ValueError('Unsupported SW knowledge schema; use an explicit migration')
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version',?)", (VERSION,))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('data_revision','0')")
            if not self.db.execute("SELECT 1 FROM meta WHERE key='fts_row_mapping'").fetchone():
                self.db.execute('INSERT OR REPLACE INTO fts_record_rows SELECT record_id,rowid FROM records_fts')
                self.db.execute('INSERT OR REPLACE INTO fts_passage_rows SELECT passage_id,record_id,rowid FROM passages_fts')
                self.db.execute("INSERT INTO meta VALUES('fts_row_mapping','1')")
            if not self.db.execute("SELECT 1 FROM meta WHERE key='application_queue_version'").fetchone():
                self.db.execute("INSERT OR IGNORE INTO application_queue SELECT p.id FROM proposals p LEFT JOIN applied_decisions ad ON ad.proposal_id=p.id WHERE p.status='accepted' AND ad.proposal_id IS NULL")
                self.db.execute("INSERT INTO meta VALUES('application_queue_version','1')")
            policy = self.db.execute("SELECT value FROM meta WHERE key='identity_resolution_policy'").fetchone()
            if not policy or policy[0] != RESOLUTION_POLICY:
                retry = ("SELECT p.id FROM proposals p JOIN records a ON a.id=p.a JOIN records b ON b.id=p.b "
                         "WHERE p.task='identity' AND p.status='accepted' AND p.decision='same_entity' AND a.entity_id!=b.entity_id")
                self.db.execute('DELETE FROM applied_decisions WHERE proposal_id IN (' + retry + ')')
                self.db.execute('INSERT OR IGNORE INTO application_queue ' + retry)
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('identity_resolution_policy',?)", (RESOLUTION_POLICY,))
            self.db.commit()

    def close(self):
        self.db.close()

    def source(self, dataset, title, origin='fan_compilation', metadata=None):
        sid = stable('src-web', dataset)
        self.db.execute('INSERT INTO sources(id,dataset,title,origin,metadata_json) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(id) DO UPDATE SET title=excluded.title,origin=excluded.origin,metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP',
                        (sid, dataset, title, origin, dumps(metadata or {})))
        return sid

    def ingest(self, source_id, key, name, body='', payload=None, kind='unknown', aliases=None,
               origin='fan_compilation', universe='star_wars', scope=None, locator=None, profile=None):
        key, name = str(key), str(name).strip()
        if not key or not name:
            raise ValueError('Records require a stable source key and a name')
        rid = stable('rec', source_id, key)
        aliases = sorted({str(a).strip() for a in aliases or [] if isinstance(a, (str, int)) and str(a).strip()})
        data = dict(name=name, body=body, payload=payload or {}, kind=kind, aliases=aliases, origin=origin,
                    universe=universe, scope=scope or {}, locator=locator or {}, profile=profile)
        sha = digest(data); vid = stable('ver', rid, sha)
        previous = self.db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
        if previous and previous['current_version'] == vid and previous['active']:
            return rid, False
        self.db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='data_revision'")
        entity_id = previous['entity_id'] if previous else stable('ent', rid)
        self.db.execute('INSERT INTO records(id,source_id,source_key,current_version,entity_id) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,active=1',
                        (rid, source_id, key, vid, entity_id))
        self.db.execute('INSERT OR IGNORE INTO record_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                        (vid, rid, sha, name, kind, universe, origin, dumps(aliases), dumps(scope or {}), dumps(locator or {}), dumps(payload or {}), body))
        self.db.execute('INSERT OR IGNORE INTO entities(id,anchor_record_id,name,kind) VALUES(?,?,?,?)',
                        (entity_id, rid, name, kind))
        self.db.execute('UPDATE entities SET name=?,kind=? WHERE id=? AND anchor_record_id=?', (name, kind, entity_id, rid))
        # A changed source invalidates memberships validated against the previous bytes.
        if previous and previous['current_version'] != vid:
            self.invalidate_group_inputs(rid)
            self.detach(rid, 'source_version_changed')
            self.db.execute("UPDATE proposals SET status='stale' WHERE a=? OR b=?", (rid, rid))
            self.db.execute("UPDATE links SET status='stale' WHERE subject_record_id=? OR object_record_id=?", (rid, rid))
            self.db.execute('DELETE FROM annotations WHERE record_id=?', (rid,))
        self.db.execute('DELETE FROM names WHERE record_id=?', (rid,))
        self.db.execute('DELETE FROM name_grams WHERE record_id=?', (rid,))
        for form in [name, *aliases]:
            if not normalize(form):
                continue
            self.db.execute('INSERT OR IGNORE INTO names VALUES(?,?,?)', (normalize(form), rid, form))
            self.db.executemany('INSERT OR IGNORE INTO name_grams VALUES(?,?)', ((g, rid) for g in grams(form)))
        if previous:
            self.db.execute('DELETE FROM records_fts WHERE rowid=(SELECT row_id FROM fts_record_rows WHERE record_id=?)', (rid,))
        fts_id = self.db.execute('INSERT INTO records_fts VALUES(?,?,?,?)', (rid, name, ' '.join(aliases), body)).lastrowid
        self.db.execute('INSERT OR REPLACE INTO fts_record_rows VALUES(?,?)', (rid, fts_id))
        if previous:
            self.db.execute('DELETE FROM passages_fts WHERE rowid IN (SELECT row_id FROM fts_passage_rows WHERE record_id=?)', (rid,))
            self.db.execute('DELETE FROM fts_passage_rows WHERE record_id=?', (rid,))
        sequence = 0
        for start in range(0, len(body), 3000):
            text = body[start:start + 3000]
            pid = stable('pass', vid, sequence)
            self.db.execute('INSERT OR IGNORE INTO passages VALUES(?,?,?,?,?,?)', (pid, vid, sequence, text, start, start + len(text)))
            fts_id = self.db.execute('INSERT INTO passages_fts VALUES(?,?,?)', (pid, rid, text)).lastrowid
            self.db.execute('INSERT OR REPLACE INTO fts_passage_rows VALUES(?,?,?)', (pid, rid, fts_id))
            sequence += 1
        if profile is not None:
            from .normalize import fields
            profile_id = stable('profile', vid)
            self.db.execute('INSERT OR IGNORE INTO profiles VALUES(?,?,?,?,?)',
                            (profile_id, rid, vid, dumps(scope or {}), dumps(profile)))
            self.db.executemany('INSERT OR IGNORE INTO profile_values VALUES(?,?,?,?,?)',
                                ((profile_id, field['field'], dumps(field['value']), field['source_path'], field['unit']) for field in fields(profile)))
        return rid, True

    def record(self, rid):
        row = self.db.execute('SELECT r.*,v.name,v.kind,v.universe,v.origin,v.aliases_json,v.scope_json,v.locator_json,v.payload_json,v.body,v.sha256 '
                              'FROM records r JOIN record_versions v ON v.id=r.current_version WHERE r.id=?', (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        result = dict(row)
        for key in ['aliases', 'scope', 'locator', 'payload']:
            result[key] = json.loads(result.pop(key + '_json'))
        return result

    def retire(self, rid):
        """Remove a missing snapshot record from live retrieval without deleting its history."""
        if not self.record(rid)['active']:
            return
        self.invalidate_group_inputs(rid)
        self.detach(rid, 'source_record_removed')
        self.db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='data_revision'")
        self.db.execute('UPDATE records SET active=0 WHERE id=?', (rid,))
        self.db.execute("UPDATE proposals SET status='stale' WHERE a=? OR b=?", (rid, rid))
        self.db.execute("UPDATE links SET status='stale' WHERE subject_record_id=? OR object_record_id=?", (rid, rid))
        self.db.execute('DELETE FROM annotations WHERE record_id=?', (rid,))

    def enqueue(self, task, a, b=None, predicate='', context=None, score=0, reasons=None):
        # Include both current versions: a changed input gets a new job, old decisions remain auditable.
        av = self.db.execute('SELECT current_version FROM records WHERE id=? AND active=1', (a,)).fetchone()
        bv = self.db.execute('SELECT current_version FROM records WHERE id=? AND active=1', (b,)).fetchone() if b else None
        if av is None or b and bv is None:
            raise ValueError('Semantic jobs require active records')
        av, bv = av[0], bv[0] if bv else None
        context = context or {}
        pid = stable('job', task, a, b, predicate, digest(context), av, bv)
        signature = digest([task, av, bv, context])
        self.db.execute("INSERT INTO proposals(id,task,a,b,predicate,context_json,signature,score,reasons_json) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status='pending',decision=NULL,result_json=NULL,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE proposals.status='stale'",
                        (pid, task, a, b, predicate, dumps(context), signature, score, dumps(reasons or [])))
        return pid

    def store_result(self, pid, result):
        proposal = self.db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
        if proposal is None:
            raise KeyError(pid)
        if proposal['task'] in {'identity_tuple', 'mention_choice'} and proposal['result_json'] is not None and proposal['result_json'] != dumps(result):
            self.revoke_group(pid)
        self.db.execute('DELETE FROM applied_decisions WHERE proposal_id=?', (pid,))
        self.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (pid,))
        accepted = bool(result.get('accepted')) and result.get('decision') != 'uncertain'
        if (proposal['task'] == 'identity' and proposal['result_json'] is not None
                and (proposal['decision'] != result.get('decision') or (proposal['status'] == 'accepted') != accepted)):
            # Retry blocked assignments only when a relevant proof changes, not every worker round.
            members = [row[0] for row in self.db.execute(
                'SELECT id FROM records WHERE active=1 AND entity_id IN (SELECT entity_id FROM records WHERE id IN (?,?))',
                (proposal['a'], proposal['b']))]
            marks = ','.join('?' for _ in members)
            if members:
                query = f"SELECT id FROM proposals WHERE task='identity' AND status='accepted' AND decision='same_entity' AND (a IN ({marks}) OR b IN ({marks}))"
                self.db.execute('DELETE FROM applied_decisions WHERE proposal_id IN (' + query + ')', (*members, *members))
                self.db.execute('INSERT OR IGNORE INTO application_queue ' + query, (*members, *members))
        if proposal['status'] == 'accepted' and (not accepted or result.get('decision') != proposal['decision']):
            self.uncover(pid)
        if proposal['task'] == 'merge_utility' and (not accepted or result.get('decision') not in {'complementary', 'conflicting'}):
            eid = self.record(proposal['a'])['entity_id']
            self.db.execute("UPDATE syntheses SET status='stale' WHERE entity_id=?", (eid,))
        if proposal['task'] == 'identity' and (not accepted or result.get('decision') != 'same_entity'):
            a, b = self.record(proposal['a']), self.record(proposal['b'])
            if a['entity_id'] == b['entity_id']:
                explicit_conflict = accepted and result.get('decision') in {'different_entity', 'mention_only'}
                anchor = self.db.execute('SELECT anchor_record_id FROM entities WHERE id=?', (a['entity_id'],)).fetchone()[0]
                revoked_anchor_proof = (proposal['status'] == 'accepted' and proposal['decision'] == 'same_entity'
                                        and anchor in {a['id'], b['id']})
                if revoked_anchor_proof:
                    alternative = self.db.execute(
                        "SELECT 1 FROM proposals WHERE task='identity' AND status='accepted' AND decision='same_entity' AND id!=? "
                        "AND ((a=? AND b=?) OR (a=? AND b=?)) LIMIT 1",
                        (pid, a['id'], b['id'], b['id'], a['id'])).fetchone()
                    revoked_anchor_proof = alternative is None
                if explicit_conflict or revoked_anchor_proof:
                    self.detach(a['id'], 'semantic_conflict')
        if proposal['result_json'] != dumps(result):
            self.db.execute('INSERT INTO decision_history(proposal_id,result_json) VALUES(?,?)', (pid, dumps(result)))
        self.db.execute("UPDATE links SET status='stale' WHERE proposal_id=?", (pid,))
        self.db.execute('DELETE FROM annotations WHERE proposal_id=?', (pid,))
        if proposal['task'] == 'mention' and (not accepted or result.get('decision') != 'refers_to'):
            derived = [r[0] for r in self.db.execute("SELECT id FROM proposals WHERE task='relation' AND json_extract(context_json,'$.mention_proposal_id')=?", (pid,))]
            for child in derived:
                self.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (child,))
                self.db.execute("UPDATE links SET status='stale' WHERE proposal_id=?", (child,))
                self.uncover(child)
        self.db.execute('UPDATE proposals SET decision=?,status=?,result_json=?,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                        (result.get('decision', 'uncertain'), 'accepted' if accepted else 'uncertain', dumps(result), pid))
        if accepted:
            self.db.execute('INSERT OR IGNORE INTO application_queue VALUES(?)', (pid,))

    def uncover(self, pid):
        self.db.execute("UPDATE proposals SET status='pending',decision=NULL,result_json=NULL WHERE status IN ('covered_by_resolved_mention','covered_by_resolved_relation','covered_by_group','resolved_by_group','deferred_by_group') AND json_extract(result_json,'$.covered_by_proposal')=?", (pid,))

    def revoke_group(self, pid):
        children = self.db.execute("SELECT id,result_json FROM proposals WHERE task IN ('identity','mention') AND json_extract(context_json,'$.parent_group_id')=?", (pid,)).fetchall()
        for child in children:
            if child['result_json']:
                result = json.loads(child['result_json'])
                result.update(accepted=False, decision='uncertain', status='source_group_revoked')
                self.store_result(child['id'], result)
            self.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (child['id'],))
            self.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (child['id'],))
        self.uncover(pid)
        self.db.execute('DELETE FROM applied_decisions WHERE proposal_id=?', (pid,))
        self.db.execute('DELETE FROM application_queue WHERE proposal_id=?', (pid,))

    def invalidate_group_inputs(self, rid):
        parents = self.db.execute("SELECT p.id FROM proposal_inputs i JOIN proposals p ON p.id=i.proposal_id WHERE i.record_id=? AND p.status!='stale'", (rid,)).fetchall()
        for parent in parents:
            self.revoke_group(parent['id'])
            self.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (parent['id'],))

    def invalidate_mention_composition(self, members):
        if not members:return
        marks = ','.join('?' for _ in members)
        parents = self.db.execute(f"SELECT DISTINCT p.id FROM proposal_inputs i JOIN proposals p ON p.id=i.proposal_id WHERE i.record_id IN ({marks}) AND p.task='mention_choice' AND p.status!='stale'", tuple(members)).fetchall()
        for parent in parents:
            self.revoke_group(parent['id'])
            self.db.execute("UPDATE proposals SET status='stale' WHERE id=?", (parent['id'],))

    def cover_equivalent_targets(self, proposal):
        """One grounded positive occurrence covers candidates already proven to be one identity."""
        eid = self.db.execute('SELECT entity_id FROM records WHERE id=?', (proposal['b'],)).fetchone()[0]
        task = proposal['task']
        context = json.loads(proposal['context_json']);context.pop('mention_proposal_id', None)
        proof = dumps({'derived': True, 'covered_by_proposal': proposal['id'], 'target_entity_id': eid,
                       'reason': 'Same occurrence and target identity already resolved by OpenJev', 'reviewed': False})
        self.db.execute("UPDATE proposals INDEXED BY proposal_pair SET status=?,result_json=? WHERE task=? AND a=? AND predicate=? AND status='pending' AND json_remove(context_json,'$.mention_proposal_id')=? AND b IN (SELECT id FROM records WHERE entity_id=? AND active=1)",
                        ('covered_by_resolved_' + task, proof, task, proposal['a'], proposal['predicate'], dumps(context), eid))

    def detach(self, rid, reason='manual_split'):
        record = self.record(rid)
        members = [self.record(row[0]) for row in self.db.execute('SELECT id FROM records WHERE entity_id=?', (record['entity_id'],))]
        ids = [member['id'] for member in members]
        marks = ','.join('?' for _ in ids)
        self.db.execute(f'DELETE FROM applied_decisions WHERE proposal_id IN (SELECT id FROM proposals WHERE a IN ({marks}) OR b IN ({marks}))', (*ids, *ids))
        self.db.execute(f"INSERT OR IGNORE INTO application_queue SELECT id FROM proposals WHERE status='accepted' AND (a IN ({marks}) OR b IN ({marks}))", (*ids, *ids))
        self.db.execute(f"UPDATE proposals SET status='pending',decision=NULL,result_json=NULL WHERE status IN ('covered_by_anchor','covered_by_resolved_mention','covered_by_resolved_relation','covered_by_group','resolved_by_group','deferred_by_group') AND (a IN ({marks}) OR b IN ({marks}))", (*ids, *ids))
        before = {'records': {}, 'redirects': {}, 'cannot_links': {}}
        after = {'records': {}, 'redirects': {}, 'cannot_links': {}}
        self.db.execute("UPDATE syntheses SET status='stale' WHERE entity_id=?", (record['entity_id'],))
        # Conservative split: dissolve this group so no member depends on an absent/changed anchor.
        for member in members:
            own_id = stable('ent', member['id'])
            self.db.execute('INSERT OR IGNORE INTO entities(id,anchor_record_id,name,kind) VALUES(?,?,?,?)',
                            (own_id, member['id'], member['name'], member['kind']))
            before['records'][member['id']] = member['entity_id']
            before['redirects'][own_id] = self.db.execute('SELECT redirect_to FROM entities WHERE id=?', (own_id,)).fetchone()[0]
            after['records'][member['id']] = own_id; after['redirects'][own_id] = None
            self.db.execute('UPDATE entities SET redirect_to=NULL,name=?,kind=?,revision=revision+1 WHERE id=?', (member['name'], member['kind'], own_id))
            self.db.execute('UPDATE records SET entity_id=? WHERE id=?', (own_id, member['id']))
            self.db.execute("UPDATE syntheses SET status='stale' WHERE entity_id=?", (own_id,))
            if reason == 'manual_split' and member['id'] != rid:
                a, b = sorted([rid, member['id']]); key = dumps([a, b])
                previous = self.db.execute('SELECT reason FROM cannot_links WHERE a=? AND b=?', (a, b)).fetchone()
                before['cannot_links'][key] = previous[0] if previous else None
                after['cannot_links'][key] = 'manual_split'
                self.db.execute('INSERT OR REPLACE INTO cannot_links VALUES(?,?,?)', (a, b, 'manual_split'))
        before['versions'] = after['versions'] = {member['id']: member['current_version'] for member in members}
        self.db.execute('INSERT INTO resolutions(action,before_json,after_json) VALUES(?,?,?)', (reason, dumps(before), dumps(after)))
        self.invalidate_mention_composition(ids)
        return stable('ent', rid)

    def resolve_identity(self, proposal_id, max_members=64):
        proposal = self.db.execute("SELECT * FROM proposals WHERE id=? AND task='identity' AND status='accepted'", (proposal_id,)).fetchone()
        if not proposal or proposal['decision'] != 'same_entity':
            return 'not_accepted'
        a, b = self.record(proposal['a']), self.record(proposal['b'])
        members_a = [r[0] for r in self.db.execute('SELECT id FROM records WHERE entity_id=? AND active=1', (a['entity_id'],))]
        members_b = [r[0] for r in self.db.execute('SELECT id FROM records WHERE entity_id=? AND active=1', (b['entity_id'],))]
        if len(members_b) > len(members_a) or (len(members_a) == len(members_b) and a['entity_id'] > b['entity_id']):
            a, b, members_a, members_b = b, a, members_b, members_a
        if len(set(members_a + members_b)) > max_members:
            self.gap(a['id'], 'resolve', 'group_limit', {'proposal': proposal_id})
            return 'group_limit'
        anchor = self.db.execute('SELECT anchor_record_id FROM entities WHERE id=?', (a['entity_id'],)).fetchone()[0]
        all_members = members_a + members_b
        all_members = list(dict.fromkeys(all_members))
        marks = ','.join('?' for _ in all_members)
        known = self.db.execute(f"SELECT a,b,decision,status FROM proposals WHERE task='identity' AND a IN ({marks}) AND b IN ({marks}) AND status!='stale'",
                                (*all_members, *all_members)).fetchall()
        for pair in known:
            if pair['status'] == 'accepted' and pair['decision'] in {'different_entity', 'mention_only'}:
                return 'known_conflict'
        for blocked in self.db.execute(f'SELECT a,b FROM cannot_links WHERE a IN ({marks}) AND b IN ({marks})', (*all_members, *all_members)):
            return 'manual_conflict'
        if a['entity_id'] == b['entity_id']:
            return 'already_assigned'
        for member in members_b:
            direct = any({pair['a'], pair['b']} == {anchor, member} and pair['decision'] == 'same_entity' and pair['status'] == 'accepted' for pair in known)
            if not direct:
                question = self.enqueue('identity', anchor, member, context={'reason': 'direct_anchor_required'}, score=200, reasons=['anchor_validation'])
                status = self.db.execute('SELECT status FROM proposals WHERE id=?', (question,)).fetchone()[0]
                if status in {'uncertain', 'manual_rejected'}:
                    return 'anchor_abstained' if status == 'uncertain' else 'anchor_manually_rejected'
                return 'awaiting_anchor'
        before = {'records': {rid: b['entity_id'] for rid in members_b}, 'redirects': {b['entity_id']: None}}
        after = {'records': {rid: a['entity_id'] for rid in members_b}, 'redirects': {b['entity_id']: a['entity_id']}}
        before['versions'] = after['versions'] = {rid: self.record(rid)['current_version'] for rid in all_members}
        self.db.executemany('UPDATE records SET entity_id=? WHERE id=?', ((a['entity_id'], rid) for rid in members_b))
        self.db.execute('UPDATE entities SET redirect_to=?,revision=revision+1 WHERE id=?', (a['entity_id'], b['entity_id']))
        self.db.execute('UPDATE entities SET revision=revision+1 WHERE id=?', (a['entity_id'],))
        self.invalidate_mention_composition(all_members)
        self.db.execute("UPDATE syntheses SET status='stale' WHERE entity_id IN (?,?)", (a['entity_id'], b['entity_id']))
        event = self.db.execute('INSERT INTO resolutions(proposal_id,action,before_json,after_json) VALUES(?,?,?,?)',
                                (proposal_id, 'identity_assignment', dumps(before), dumps(after))).lastrowid
        proof = dumps({'derived': True, 'anchor_record_id': anchor, 'resolution_event_id': event,
                       'reason': 'Both members have accepted direct identity proofs to this anchor', 'reviewed': False})
        self.db.execute(f"UPDATE proposals INDEXED BY proposal_pair SET status='covered_by_anchor',result_json=? WHERE task='identity' AND status='pending' AND a IN ({marks}) AND b IN ({marks})", (proof, *all_members, *all_members))
        return 'assigned'

    def undo(self, event_id):
        event = self.db.execute('SELECT * FROM resolutions WHERE id=? AND undone=0', (event_id,)).fetchone()
        if event is None:
            raise ValueError('Unknown or already undone resolution')
        if event['action'] in {'source_version_changed', 'source_record_removed', 'semantic_configuration_changed', 'semantic_conflict'}:
            raise ValueError('Source invalidation cannot restore stale identities; reclassify current records')
        before, after = json.loads(event['before_json']), json.loads(event['after_json'])
        for rid, expected in after.get('versions', {}).items():
            if self.record(rid)['current_version'] != expected:
                raise ValueError('Source version changed since this resolution')
        for rid, expected in after['records'].items():
            if self.record(rid)['entity_id'] != expected:
                raise ValueError('A later assignment conflicts with this undo')
        # Do not roll back around subsequent events involving the same records.
        touched = set(after['records'])
        for later in self.db.execute('SELECT after_json FROM resolutions WHERE id>? AND undone=0', (event_id,)):
            if touched.intersection(json.loads(later[0]).get('records', {})):
                raise ValueError('Undo later dependent resolutions first')
        for rid, eid in before['records'].items():
            self.db.execute('UPDATE records SET entity_id=? WHERE id=?', (eid, rid))
        for eid, target in before['redirects'].items():
            self.db.execute('UPDATE entities SET redirect_to=?,revision=revision+1 WHERE id=?', (target, eid))
            self.db.execute("UPDATE syntheses SET status='stale' WHERE entity_id=?", (eid,))
        for key, reason in before.get('cannot_links', {}).items():
            a, b = json.loads(key)
            if reason is None:
                self.db.execute('DELETE FROM cannot_links WHERE a=? AND b=?', (a, b))
            else:
                self.db.execute('INSERT OR REPLACE INTO cannot_links VALUES(?,?,?)', (a, b, reason))
        if event['action'] == 'identity_assignment':
            # Undo is a manual rejection of this assignment, not permission to replay it next resolve.
            ids = list(after.get('versions', after['records']))
            marks = ','.join('?' for _ in ids)
            self.db.execute(f"UPDATE proposals SET status='manual_rejected' WHERE task='identity' AND a IN ({marks}) AND b IN ({marks}) AND status IN ('accepted','covered_by_anchor') AND (SELECT entity_id FROM records WHERE id=proposals.a)!=(SELECT entity_id FROM records WHERE id=proposals.b)", (*ids, *ids))
            self.db.execute(f"UPDATE proposals SET status='pending',result_json=NULL WHERE status IN ('covered_by_resolved_mention','covered_by_resolved_relation') AND (a IN ({marks}) OR b IN ({marks}))", (*ids, *ids))
        self.db.execute('UPDATE resolutions SET undone=1 WHERE id=?', (event_id,))

    def gap(self, rid, stage, reason, details):
        self.db.execute('INSERT OR REPLACE INTO mining_gaps VALUES(?,?,?,?)', (rid, stage, reason, dumps(details)))

    def search(self, query, limit=20, kind=None, universe='star_wars', origin=None, importance=None):
        terms = normalize(query).split()
        if not terms:
            return []
        expression = ' OR '.join('"' + term + '"' for term in terms[:12])
        filters, params = ['r.active=1'], [expression]
        for expression_key, value in [("COALESCE(json_extract(ty.value_json,'$'),v.kind)", kind), ("COALESCE(json_extract(uv.value_json,'$'),v.universe)", universe), ('v.origin', origin), ("json_extract(im.value_json,'$')", importance)]:
            if value and value != 'all':
                filters.append(expression_key + '=?'); params.append(value)
        params.append(limit)
        return [dict(row) for row in self.db.execute(
            "SELECT r.id record_id,r.entity_id,v.name,v.kind,COALESCE(json_extract(ty.value_json,'$'),v.kind) resolved_kind,CASE WHEN ty.record_id IS NULL THEN 'adapter_hint' ELSE 'model_classified' END kind_status,json_extract(im.value_json,'$') importance,v.universe,COALESCE(json_extract(uv.value_json,'$'),v.universe) resolved_universe,v.origin,s.dataset,bm25(records_fts,0,8,4,1) score "
            'FROM records_fts JOIN records r ON r.id=records_fts.record_id JOIN record_versions v ON v.id=r.current_version '
            "JOIN sources s ON s.id=r.source_id LEFT JOIN annotations ty ON ty.record_id=r.id AND ty.task='type' LEFT JOIN annotations im ON im.record_id=r.id AND im.task='importance' LEFT JOIN annotations uv ON uv.record_id=r.id AND uv.task='universe' WHERE records_fts MATCH ? AND " + ' AND '.join(filters) + ' ORDER BY score LIMIT ?', params)]

    def entity(self, eid):
        visited = set()
        while True:
            if eid in visited:
                raise ValueError('Entity redirect cycle')
            visited.add(eid)
            entity = self.db.execute('SELECT * FROM entities WHERE id=?', (eid,)).fetchone()
            if entity is None:
                raise KeyError(eid)
            if not entity['redirect_to']:
                break
            eid = entity['redirect_to']
        members = [self.record(r[0]) for r in self.db.execute('SELECT id FROM records WHERE entity_id=? AND active=1 ORDER BY id', (eid,))]
        links = [dict(row) for row in self.db.execute(
            'SELECT l.*,a.entity_id subject_entity_id,b.entity_id object_entity_id FROM links l '
            'JOIN records a ON a.id=l.subject_record_id JOIN records b ON b.id=l.object_record_id '
            "WHERE (a.entity_id=? OR b.entity_id=?) AND l.status='accepted'", (eid, eid))]
        editorial = self.db.execute('SELECT * FROM editorial WHERE entity_id=?', (eid,)).fetchone()
        overlays = []
        # Keep the editorial layers of redirected identities accessible, including conflicts.
        for row in self.db.execute('SELECT * FROM editorial'):
            candidate = row['entity_id']; seen = set()
            while candidate and candidate not in seen:
                seen.add(candidate)
                if candidate == eid:
                    overlays.append(dict(row));break
                target = self.db.execute('SELECT redirect_to FROM entities WHERE id=?', (candidate,)).fetchone()
                candidate = target[0] if target else None
        return {'entity': dict(entity), 'resolution_status': 'model_resolved' if len(members) > 1 else 'provisional', 'records': members, 'relations': links,
                'document_links': [dict(row) for row in self.db.execute('SELECT l.* FROM document_links l JOIN records a ON a.id=l.subject_record_id JOIN records b ON b.id=l.object_record_id WHERE (a.entity_id=? OR b.entity_id=?) AND a.active=1 AND b.active=1 AND a.current_version=l.subject_version AND b.current_version=l.object_version', (eid, eid))],
                'profiles': [dict(row) for row in self.db.execute('SELECT p.* FROM profiles p JOIN records r ON r.id=p.record_id WHERE r.entity_id=? AND p.version_id=r.current_version', (eid,))],
                'editorial': dict(editorial) if editorial else None,
                'editorial_overlays': overlays,
                'annotations': [dict(row) for row in self.db.execute('SELECT an.* FROM annotations an JOIN records r ON r.id=an.record_id WHERE r.entity_id=?', (eid,))]}

    def status(self):
        tables = ['sources', 'records', 'record_versions', 'entities', 'profiles', 'passages', 'links', 'document_links', 'syntheses', 'mining_gaps']
        result = {table: self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}
        result['active_entities'] = self.db.execute('SELECT count(DISTINCT entity_id) FROM records WHERE active=1').fetchone()[0]
        result['tasks'] = [dict(row) for row in self.db.execute('SELECT task,status,count(*) count FROM proposals GROUP BY task,status')]
        result['datasets'] = [dict(row) for row in self.db.execute('SELECT s.dataset,count(*) records FROM records r JOIN sources s ON s.id=r.source_id WHERE r.active=1 GROUP BY s.dataset')]
        return result
