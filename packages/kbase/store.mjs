import { DatabaseSync } from 'node:sqlite';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, readdirSync, mkdirSync, existsSync, realpathSync, statSync, unlinkSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCHEMA = readFileSync(path.join(HERE, 'schema.sql'), 'utf8');
export const KBASE_SCHEMA_VERSION = 4;
export const ORIGINAL_SOURCE_IDS = Object.freeze([
  'src-1678b1eb8c4bd691','src-a3a28719eed85a70','src-e76020e4b32da626','src-c86af6abdc3184c5'
]);
export const PILOT_SOURCE_IDS = Object.freeze(ORIGINAL_SOURCE_IDS.slice(0,2));
export const NODE_TYPES = new Set([
  'Character','Faction','Organization','Location','Planet','System','Ship','Vehicle','Species','Item','Artifact','HistoricalEvent',
  'Rule','Mechanic','Skill','Ability','Equipment','StatBlock','RandomTable','HouseRule','Difficulty',
  'Arc','Plot','Thread','Episode','Adventure','Scene','Event','Objective','Secret','Clue','Revelation','Consequence',
  'NPCSeed','LocationSeed','EncounterSeed','Complication','Rumor','NameList','DescriptionFragment','DialogueFragment','SceneSeed','Reward','Media'
]);
export const FAMILIES = new Set(['WORLD','RULES','NARRATIVE','INSPIRATION','CAMPAIGN','RELATIONSHIPS','STATE']);
export const USAGES = new Set(['darkstryder_scenario','reference_lore','rules','inspiration','campaign_customization']);
export const NODE_FAMILY_TYPES = Object.freeze({
  WORLD:new Set(['Character','Faction','Organization','Location','Planet','System','Ship','Vehicle','Species','Item','Artifact','HistoricalEvent']),
  RULES:new Set(['Rule','Mechanic','Skill','Ability','Equipment','StatBlock','RandomTable','HouseRule','Difficulty']),
  NARRATIVE:new Set(['Arc','Plot','Thread','Episode','Adventure','Scene','Event','Objective','Secret','Clue','Revelation','Consequence']),
  INSPIRATION:new Set(['NPCSeed','LocationSeed','EncounterSeed','Complication','Rumor','NameList','DescriptionFragment','DialogueFragment','SceneSeed','Reward','Media'])
});
export const SCOPES = new Set(['source','campaign','cross_source','future_campaign_state']);
export const VISIBILITIES = new Set(['private','gm_only','player_visible','public']);
const EXCLUDED = new Set(['src-13b156cf43fd4f8a','src-9924922776fe2cd6','src-1af1e1b994ff3683']);
const sha = value => createHash('sha256').update(value).digest('hex');
const json = value => JSON.stringify(value);
const normalize = value => String(value).normalize('NFC').replace(/\s+/gu, ' ').trim();
const identityName = value => normalize(value).replace(/[’‘`´]/gu,"'").replace(/[‐‑‒–—]/gu,'-').toLocaleLowerCase('fr-FR');
const safeJson = (value, fallback) => { try { return JSON.parse(value); } catch { return fallback; } };
const inside = (base, target) => target === base || target.startsWith(base + path.sep);
export class KBaseError extends Error {
  constructor(message,status=400) { super(message); this.name='KBaseError'; this.status=status; }
}
const requireString = (value, label) => {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} must be a non-empty string`);
  return value.trim();
};

function readJson(file) { return JSON.parse(readFileSync(file, 'utf8')); }

export function openKBase(projectRoot, optionalRuntimeRoot) {
  const root = realpathSync(projectRoot);
  const config = readJson(path.join(root, 'config.local.json'));
  const runtimeRoot = path.resolve(optionalRuntimeRoot || config.runtimeRoot || path.join(root, 'runtime'));
  mkdirSync(path.join(runtimeRoot, 'kbase'), { recursive: true });
  const dbPath = path.join(runtimeRoot, 'kbase', 'knowledge.sqlite');
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA synchronous=FULL;');
  db.exec(SCHEMA);
  const schemaVersion = db.prepare("SELECT value FROM schema_meta WHERE key='schema_version'").get()?.value;
  if (Number(schemaVersion) !== KBASE_SCHEMA_VERSION) throw new Error(`Unsupported KBase schema version ${schemaVersion}`);

  const indexPath = path.join(root, 'content', 'transcriptions', 'index.json');
  const transRoot = realpathSync(path.join(root, 'content', 'transcriptions'));
  const index = readJson(indexPath);
  const sourcesById = new Map((index.sources || []).map(source => [source.id, source]));
  const scopeFile = path.join(root, 'config', 'corpus-scope.json');
  const scope = existsSync(scopeFile) ? readJson(scopeFile) : { skipSources: [] };
  const skipped = new Set([...(scope.skipSources || []).map(entry => entry.id), ...EXCLUDED]);
  const sourceMeta = new Map();
  const stmts = {
    sourceUpsert: db.prepare(`INSERT INTO sources(id,name,collection,usage,provenance,unit_label,expected_units,locator_path,transcription_sha256,status)
      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,collection=excluded.collection,usage=excluded.usage,
      provenance=excluded.provenance,unit_label=excluded.unit_label,expected_units=excluded.expected_units,locator_path=excluded.locator_path,
      transcription_sha256=excluded.transcription_sha256,status=excluded.status`),
    getJobs: db.prepare('SELECT * FROM jobs ORDER BY created_at,id'),
    getJob: db.prepare('SELECT * FROM jobs WHERE id=?'),
    updateJob: db.prepare("UPDATE jobs SET status=?,attempt=attempt+1,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('pending','failed')"),
    insertJob: db.prepare("INSERT OR IGNORE INTO jobs(id,source_id,chunk_hash,source_sha256,pages_json,source_priority,first_page,first_offset,status) VALUES(?,?,?,?,?,?,?,?,'pending')"),
    updateFailed: db.prepare("UPDATE jobs SET status='pending',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE status='failed'"),
  };

  function getSourceContext(sourceId) {
    const source = sourcesById.get(sourceId);
    if (!source) throw new Error(`Unknown source ${sourceId}`);
    if (skipped.has(sourceId) || source.status === 'skipped') throw new Error(`Source ${sourceId} is excluded`);
    const sourceDir = path.join(transRoot, sourceId);
    const realDir = realpathSync(sourceDir);
    if (!inside(transRoot, realDir)) throw new Error('Source path escapes transcription root');
    const transcriptionPath = path.join(realDir, 'transcription.json');
    const transcription = existsSync(transcriptionPath) ? readJson(transcriptionPath) : {};
    const collection = source.collection || transcription.collections?.[0] || 'sourcebooks';
    const usage = collection === 'darkstryder' ? 'darkstryder_scenario' : collection === 'addons' ? 'campaign_customization' : collection === 'guides-rules' ? 'rules' : 'reference_lore';
    const provenance = transcription.evidenceProvenance || scope.include?.find(entry => locatorPathFor(source, transcription).startsWith(entry.path))?.provenance || 'provenance-unclassified';
    const sourceHash = transcription.sha256 || source.sha256 || '';
    const transcriptionHash = existsSync(transcriptionPath) ? sha(readFileSync(transcriptionPath)) : '';
    return { source, sourceDir: realDir, transcription, sourceHash, transcriptionHash, collection, usage, provenance };
  }

  function locatorPathFor(source, transcription) { return source.locator?.path || transcription.locator?.path || ''; }

  function syncSources() {
    const found = [];
    const transaction = db.prepare('SELECT 1').get(); // Fail early if the connection is unhealthy.
    void transaction;
    db.exec('BEGIN IMMEDIATE');
    try {
      for (const source of index.sources || []) {
        if (skipped.has(source.id) || source.status !== 'complete' || !source.markdown) continue;
        const ctx = getSourceContext(source.id);
        const pagesDir = path.join(ctx.sourceDir, 'pages');
        if (!existsSync(pagesDir)) continue;
        const pageFiles = readdirSync(pagesDir).filter(name => /^\d+\.md$/u.test(name)).sort((a,b) => Number(a.slice(0,-3))-Number(b.slice(0,-3)));
        if (!pageFiles.length) continue;
        const locator = locatorPathFor(source, ctx.transcription) || null;
        stmts.sourceUpsert.run(source.id, source.name, ctx.collection, ctx.usage, ctx.provenance,
          source.unitLabel || ctx.transcription.unitLabel || 'page PDF', Number(source.units || ctx.transcription.units) || null,
          locator, ctx.sourceHash || null, 'complete');
        sourceMeta.set(source.id, ctx);
        found.push({ id: source.id, pages: pageFiles.length });
      }
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
    return { sources: found.length, pages: found.reduce((n, x) => n + x.pages, 0), skipped: skipped.size };
  }

  function getCtx(sourceId) {
    if (!sourceMeta.has(sourceId)) sourceMeta.set(sourceId, getSourceContext(sourceId));
    const ctx = sourceMeta.get(sourceId);
    const transcriptionPath = path.join(ctx.sourceDir, 'transcription.json');
    const liveTranscriptionHash = existsSync(transcriptionPath) ? sha(readFileSync(transcriptionPath)) : '';
    if (liveTranscriptionHash !== ctx.transcriptionHash) throw new Error(`Source transcription metadata changed after planning: ${sourceId}`);
    return ctx;
  }

  function collectPages(sourceId) {
    const ctx = getCtx(sourceId);
    const pagesDir = path.join(ctx.sourceDir, 'pages');
    const realPages = realpathSync(pagesDir);
    if (!inside(transRoot, realPages)) throw new Error('Pages path escapes transcription root');
    return readdirSync(realPages).filter(name => /^\d+\.md$/u.test(name)).sort((a,b) => Number(a.slice(0,-3))-Number(b.slice(0,-3))).map(name => {
      const page = Number(name.slice(0,-3));
      const file = path.join(realPages, name);
      const realFile = realpathSync(file);
      if (!inside(realPages, realFile) || !statSync(realFile).isFile()) throw new Error('Unsafe page file path');
      const markdown = readFileSync(realFile, 'utf8');
      return { page, fileName:name, markdown, path: realFile, sha256: sha(markdown), relativePath: `content/transcriptions/${sourceId}/pages/${name}` };
    });
  }

  function planJobs({ maxChars = 18000, maxPages = 6, sourceIds = PILOT_SOURCE_IDS } = {}) {
    if (!Number.isInteger(maxChars) || maxChars < 1 || !Number.isInteger(maxPages) || maxPages < 1) throw new Error('maxChars and maxPages must be positive integers');
    if (!Array.isArray(sourceIds) || sourceIds.some(id=>typeof id!=='string')) throw new Error('sourceIds must be an ordered array');
    syncSources();
    const jobs = [];
    const available=new Map(db.prepare("SELECT id,name,collection,usage FROM sources WHERE status='complete'").all().map(s=>[s.id,s]));
    for (const sourceId of sourceIds) {
      const source=available.get(sourceId); if (!source) continue;
      const allPages = collectPages(source.id).flatMap(page => {
        if (page.markdown.length <= maxChars) return [{ ...page, offsetStart:0, offsetEnd:page.markdown.length, fragmentSha256:sha(page.markdown), pageSha256:page.sha256 }];
        const segments=[];
        for (let start=0;start<page.markdown.length;start+=maxChars) {
          const end=Math.min(page.markdown.length,start+maxChars); const markdown=page.markdown.slice(start,end);
          segments.push({ ...page, markdown, offsetStart:start, offsetEnd:end, fragmentSha256:sha(markdown), pageSha256:page.sha256 });
        }
        return segments;
      });
      let chunk = [];
      const flush = () => {
        if (!chunk.length) return;
        const sourceHash=getCtx(source.id).sourceHash;
        const descriptor = chunk.map(({ page, fileName, pageSha256, fragmentSha256, offsetStart, offsetEnd }) => ({ page, fileName, pageSha256, fragmentSha256, offsetStart, offsetEnd }));
        const chunkHash = sha(json({ sourceId: source.id, sourceHash, pages: descriptor }));
        const id = `job-${chunkHash.slice(0, 24)}`;
        const priority=ORIGINAL_SOURCE_IDS.indexOf(source.id); stmts.insertJob.run(id, source.id, chunkHash, sourceHash, json(descriptor), priority>=0?priority:1000, descriptor[0].page, descriptor[0].offsetStart);
        jobs.push({ id, sourceId: source.id, sourceName: source.name, collection: source.collection, usage: source.usage,
          sourceHash: getCtx(source.id).sourceHash, pages: chunk });
        chunk = [];
      };
      for (const page of allPages) {
        if (chunk.length && (chunk.length >= maxPages || chunk.reduce((n, p) => n + p.markdown.length, 0) + page.markdown.length > maxChars)) flush();
        chunk.push(page);
        if (chunk.reduce((n,p)=>n+p.markdown.length,0)>=maxChars) flush();
      }
      flush();
    }
    return jobs;
  }

  function jobView(row) {
    if (!row) return null;
    const source = db.prepare('SELECT * FROM sources WHERE id=?').get(row.source_id);
    const ctx = getCtx(row.source_id);
    if (ctx.sourceHash !== row.source_sha256) throw new Error(`Source hash changed since job planning: ${row.source_id}`);
    const descriptors = safeJson(row.pages_json, []);
    const seen=new Map();
    const pages = descriptors.map(d => {
      let whole=seen.get(d.page);
      if (!whole) {
        if (typeof d.fileName!=='string'||!/^\d+\.md$/u.test(d.fileName)) throw new Error('Unsafe page file path');
        const file=path.join(ctx.sourceDir,'pages',d.fileName);
        const realFile=realpathSync(file); const pagesDir=realpathSync(path.join(ctx.sourceDir,'pages'));
        if (!inside(pagesDir,realFile)||!statSync(realFile).isFile()) throw new Error('Unsafe page file path');
        const markdown=readFileSync(realFile,'utf8'); whole={markdown,sha256:sha(markdown),path:realFile,
          relativePath:`content/transcriptions/${row.source_id}/pages/${d.fileName}`};
        seen.set(d.page,whole);
      }
      if (whole.sha256!==d.pageSha256) throw new Error(`Source page changed since job planning: ${row.source_id} page ${d.page}`);
      const markdown=whole.markdown.slice(d.offsetStart,d.offsetEnd);
      if (markdown.length!==d.offsetEnd-d.offsetStart || sha(markdown)!==d.fragmentSha256) throw new Error(`Source fragment changed since job planning: ${row.source_id} page ${d.page}`);
      return { page:d.page, markdown, path:whole.path, sha256:whole.sha256, pageSha256:whole.sha256,
        fragmentSha256:d.fragmentSha256, offsetStart:d.offsetStart, offsetEnd:d.offsetEnd, relativePath:whole.relativePath };
    });
    const existingNodes=ctx.collection==='darkstryder' ? db.prepare(`SELECT n.id,n.type,n.family,n.name,COALESCE(e.summary,n.summary) summary,n.properties_json
      FROM nodes n LEFT JOIN node_editorial e ON e.node_id=n.id WHERE n.source_key LIKE 'darkstryder-original:%' ORDER BY n.name,n.id LIMIT 300`).all().map(n=>({
      id:n.id,type:n.type,family:n.family,name:n.name,summary:String(n.summary).slice(0,400),aliases:(Array.isArray(safeJson(n.properties_json,{}).aliases)?safeJson(n.properties_json,{}).aliases:[]).slice(0,20)
      })) : [];
    return { id: row.id, sourceId: row.source_id, sourceName: source.name, collection: source.collection, usage: source.usage,
      sourceUsage:source.usage,provenance:ctx.provenance,unitLabel:source.unit_label||ctx.transcription.unitLabel||'page PDF',
      status: row.status, attempt: row.attempt, sourceHash: ctx.sourceHash, pages, existingNodes,
      coverage:safeJson(row.coverage_json,{}), reviewRequired:Boolean(row.review_required), notes:safeJson(row.notes_json,[]), error:row.error||null };
  }

  function claimNextJob({ sourceIds = PILOT_SOURCE_IDS } = {}) {
    if (!Array.isArray(sourceIds) || sourceIds.some(id=>typeof id!=='string')) throw new Error('sourceIds must be an ordered array');
    if (sourceIds.length===0) return null;
    db.exec('BEGIN IMMEDIATE');
    try {
      if (db.prepare("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").get()) { db.exec('COMMIT'); return null; }
      const placeholders=sourceIds.map(()=>'?').join(',');
      const row = db.prepare(`SELECT * FROM jobs WHERE status='pending' AND source_id IN (${placeholders}) ORDER BY source_priority,first_page,first_offset,created_at,id LIMIT 1`).get(...sourceIds);
      if (!row) { db.exec('COMMIT'); return null; }
      const changed = stmts.updateJob.run('running', row.id).changes;
      if (changed !== 1) { db.exec('COMMIT'); return null; }
      const claimed = jobView(db.prepare('SELECT * FROM jobs WHERE id=?').get(row.id));
      db.exec('COMMIT');
      return claimed;
    } catch (error) { db.exec('ROLLBACK'); throw error; }
  }

  function getJob(id) { return jobView(stmts.getJob.get(id)); }
  function decodeJobStatus(row) { return row ? { id:row.id,sourceId:row.source_id,sourceName:row.source_name,status:row.status,attempt:row.attempt,
    error:row.error||null,reviewRequired:Boolean(row.review_required),coverage:safeJson(row.coverage_json,{}),notes:safeJson(row.notes_json,[]),
    firstPage:row.first_page,firstOffset:row.first_offset } : null; }
  function getJobStatuses(ids) {
    if (!Array.isArray(ids)) throw new Error('ids must be an array');
    if (!ids.length) return [];
    const rows=db.prepare(`SELECT j.*,s.name source_name FROM jobs j JOIN sources s ON s.id=j.source_id WHERE j.id IN (${ids.map(()=>'?').join(',')})`).all(...ids);
    const byId=new Map(rows.map(row=>[row.id,decodeJobStatus(row)]));
    return ids.map(id=>byId.get(id)).filter(Boolean);
  }
  function getJobAudit({sourceIds}={}) {
    const clauses=[]; const params=[];
    if(sourceIds!==undefined){if(!Array.isArray(sourceIds))throw new Error('sourceIds must be an array');if(!sourceIds.length)return {jobs:{},reviewRequired:0,bySource:{}};clauses.push(`source_id IN (${sourceIds.map(()=>'?').join(',')})`);params.push(...sourceIds);}
    const where=clauses.length?`WHERE ${clauses.join(' AND ')}`:'';
    const statuses=Object.fromEntries(db.prepare(`SELECT status,COUNT(*) n FROM jobs ${where} GROUP BY status`).all(...params).map(row=>[row.status,row.n]));
    const bySource=Object.fromEntries(db.prepare(`SELECT source_id,COUNT(*) n FROM jobs ${where} GROUP BY source_id`).all(...params).map(row=>[row.source_id,row.n]));
    const reviewRequired=db.prepare(`SELECT COUNT(*) n FROM jobs ${where?where+' AND':'WHERE'} review_required=1 AND status='complete'`).get(...params).n;
    return {jobs:statuses,reviewRequired,bySource};
  }

  function backupBeforeImport() {
    const dir = path.join(runtimeRoot, 'backups', 'kbase');
    mkdirSync(dir, { recursive: true });
    const filename = `knowledge-${new Date().toISOString().replace(/[:.]/gu, '-')}-${randomUUID().slice(0,8)}.sqlite`;
    const target = path.join(dir, filename).replaceAll("'", "''");
    db.exec(`VACUUM INTO '${target}'`);
    const files = readdirSync(dir).filter(name => /^knowledge-.*\.sqlite$/u.test(name)).sort();
    for (const old of files.slice(0, Math.max(0, files.length - 5))) unlinkSync(path.join(dir, old));
  }

  function validateCitation(citation, job, pageByNumber, fieldsRequired) {
    if (!citation || typeof citation !== 'object') throw new Error('Every insertion requires citation evidence');
    if (citation.sourceId !== job.sourceId) throw new Error('Evidence source is outside the job');
    if (!Number.isInteger(citation.page) || !pageByNumber.has(citation.page)) throw new Error('Evidence page is outside the job');
    const quote = requireString(citation.quote, 'evidence quote');
    if (quote.length > 1200) throw new Error('Evidence quote exceeds 1200 characters');
    const page = pageByNumber.get(citation.page);
    if (!normalize(page.markdown).includes(normalize(quote))) throw new Error(`Evidence quote does not match source page ${citation.page}`);
    if (!Array.isArray(citation.fields) || !citation.fields.length) throw new Error('Evidence fields must identify supported fields');
    for (const f of citation.fields) if (!fieldsRequired.has(f)) throw new Error(`Evidence references an unsupported field: ${f}`);
    return { page, quote, fields: citation.fields };
  }

  function importExtraction(jobId, payload) {
    const jobRow = stmts.getJob.get(jobId);
    if (!jobRow) throw new Error(`Unknown job ${jobId}`);
    if (jobRow.status === 'complete') return { idempotent: true, nodes: 0, relationships: 0, citations: 0, coverage: safeJson(jobRow.coverage_json, {}) };
    if (jobRow.status !== 'running') throw new Error(`Job ${jobId} is not running`);
    if (!payload || payload.jobId !== jobId || !Array.isArray(payload.nodes) || !Array.isArray(payload.relationships) || !Array.isArray(payload.notes) ||
        (payload.segments !== undefined && !Array.isArray(payload.segments))) throw new Error('Extraction payload does not match the required contract');
    const job = jobView(jobRow);
    const pages = new Map(job.pages.map(page => [page.page, page]));
    const keys = new Map();
    const validatedNodes = [];
    const validatedRelationships = [];
    let citationCount = 0;
    for (const node of payload.nodes) {
      if (!node || typeof node !== 'object') throw new Error('Invalid node');
      const key = requireString(node.key, 'node key');
      if (keys.has(key)) throw new Error(`Duplicate node key ${key}`);
      if (!NODE_TYPES.has(node.type)) throw new Error(`Invalid node type ${node.type}`);
      if (!FAMILIES.has(node.family) || node.family === 'STATE') throw new Error(`Invalid or reserved node family ${node.family}`);
      if (NODE_FAMILY_TYPES[node.family] && !NODE_FAMILY_TYPES[node.family].has(node.type)) throw new Error(`Node type ${node.type} is incompatible with family ${node.family}`);
      if (!USAGES.has(node.usage)) throw new Error(`Invalid usage ${node.usage}`);
      for (const field of ['name','summary','content']) requireString(node[field], `node ${field}`);
      if (!SCOPES.has(node.scope)) throw new Error(`Invalid scope ${node.scope}`);
      if (!VISIBILITIES.has(node.visibility)) throw new Error(`Invalid visibility ${node.visibility}`);
      const tags = node.tags ?? [];
      const properties = node.properties ?? {};
      if (!Array.isArray(tags) || tags.some(x => typeof x !== 'string') || !properties || Array.isArray(properties) || typeof properties !== 'object') throw new Error('Invalid node tags or properties');
      const required = new Set(['name','summary','content', ...tags.map(t => `tag:${t}`), ...Object.keys(properties).map(k => `property:${k}`)]);
      if (!Array.isArray(node.evidence) || !node.evidence.length) throw new Error(`Node ${key} requires citations`);
      const evidence = node.evidence.map(c => validateCitation(c, job, pages, required));
      const covered = new Set(evidence.flatMap(c => c.fields));
      for (const f of required) if (!covered.has(f)) throw new Error(`Node ${key} lacks evidence for ${f}`);
      if (node.existingId!==undefined) {
        const namespace=sourceMeta.get(job.sourceId)?.collection==='darkstryder'?'darkstryder-original':job.sourceId;
        const existing=db.prepare('SELECT id,type,source_key FROM nodes WHERE id=?').get(node.existingId);
        if (!existing||existing.type!==node.type||!existing.source_key.startsWith(`${namespace}:`)) throw new Error(`Node ${key} references an unavailable or incompatible existingId`);
      }
      keys.set(key, { node, evidence });
      validatedNodes.push({ key, node, evidence });
      citationCount += evidence.length * required.size;
    }
    for (const relation of payload.relationships) {
      if (!relation || !keys.has(relation.sourceKey) || !keys.has(relation.targetKey)) throw new Error('Relationship has a dangling node key');
      const type = requireString(relation.type, 'relationship type');
      if (!/^[a-z][a-z0-9_]{0,47}$/u.test(type)) throw new Error(`Invalid relationship type ${type}`);
      const notes = relation.notes || '';
      if (typeof notes !== 'string') throw new Error('Relationship notes must be text');
      const required = new Set(relation.notes ? ['relation','notes'] : ['relation']);
      if (!Array.isArray(relation.evidence) || !relation.evidence.length) throw new Error('Every relationship requires citations');
      const evidence = relation.evidence.map(c => validateCitation(c, job, pages, required));
      for (const field of required) if (!evidence.some(c=>c.fields.includes(field))) throw new Error(`Relationship evidence must support ${field}`);
      validatedRelationships.push({ relation, type, notes, evidence });
      citationCount += evidence.length;
    }
    const validatedSegments=[];
    for (const segment of payload.segments||[]) {
      if (!segment||typeof segment!=='object') throw new Error('Invalid logical segment');
      const key=requireString(segment.key,'segment key'); const title=requireString(segment.title,'segment title');
      const kind=requireString(segment.kind,'segment kind');
      if (!Array.isArray(segment.nodeKeys)||segment.nodeKeys.some(k=>!keys.has(k))) throw new Error(`Logical segment ${key} has unknown node keys`);
      const fields=new Set(['segment']);
      if (!Array.isArray(segment.evidence)||!segment.evidence.length) throw new Error(`Logical segment ${key} requires citations`);
      const evidence=segment.evidence.map(c=>validateCitation(c,job,pages,fields));
      if (!evidence.some(c=>c.fields.includes('segment'))) throw new Error(`Logical segment ${key} lacks segment evidence`);
      validatedSegments.push({key,title,kind,nodeKeys:segment.nodeKeys,evidence}); citationCount+=evidence.length;
    }
    const usableCount = validatedNodes.filter(({ node }) => node.name.trim() && node.summary.trim() && node.content.trim()).length;
    const coverage = { nodeCount: validatedNodes.length, relationshipCount: validatedRelationships.length,
      populatedNodeCount: usableCount, hasEditorialContent: usableCount > 0, reviewRequired: true,
      notes: payload.notes.map(note => String(note)).slice(0, 100), logicalSegmentCount:validatedSegments.length };
    backupBeforeImport();
    db.exec('BEGIN IMMEDIATE');
    try {
      if (db.prepare("SELECT status FROM jobs WHERE id=?").get(jobId)?.status !== 'running') throw new Error('Job state changed during import');
      const nodeIds = new Map();
      for (const { key, node, evidence } of validatedNodes) {
        const namespace=sourceMeta.get(job.sourceId)?.collection==='darkstryder'?'darkstryder-original':job.sourceId;
        const disambiguator=node.properties?.identityDisambiguator ? `:${identityName(node.properties.identityDisambiguator)}` : '';
        const explicitExisting=node.existingId?db.prepare('SELECT source_key FROM nodes WHERE id=?').get(node.existingId):null;
        const stableSourceKey = explicitExisting?.source_key || `${namespace}:${node.type}:${identityName(node.name)}${disambiguator}`;
        const nodeId = `kb-${sha(stableSourceKey).slice(0,24)}`;
        nodeIds.set(key, nodeId);
        db.prepare(`INSERT INTO nodes(id,source_key,type,family,usage,name,summary,content,tags_json,scope,visibility,properties_json,review_status)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'unreviewed') ON CONFLICT(source_key) DO NOTHING`).run(nodeId,stableSourceKey,node.type,node.family,node.usage,node.name,node.summary,node.content,json(node.tags||[]),node.scope,node.visibility,json(node.properties||{}));
        const actualNode = db.prepare('SELECT id FROM nodes WHERE source_key=?').get(stableSourceKey).id;
        nodeIds.set(key, actualNode);
        const fieldValues = { name: node.name, summary: node.summary, content: node.content, ...Object.fromEntries((node.tags||[]).map(t => [`tag:${t}`,t])), ...Object.fromEntries(Object.entries(node.properties||{}).map(([k,v]) => [`property:${k}`,v])) };
        for (const [field,value] of Object.entries(fieldValues)) {
          const claimId = `cl-${sha(`${stableSourceKey}:${field}:${jobId}`).slice(0,24)}`;
          db.prepare('INSERT OR IGNORE INTO claims(id,node_id,field,value_json,job_id) VALUES(?,?,?,?,?)').run(claimId,actualNode,field,json(value),jobId);
          const claim = db.prepare('SELECT id FROM claims WHERE node_id=? AND field=? AND job_id=?').get(actualNode,field,jobId);
          const fieldEvidence = evidence.filter(c => c.fields.includes(field));
          for (const citation of fieldEvidence) {
            const page = citation.page;
            db.prepare(`INSERT OR IGNORE INTO citations(id,claim_id,source_id,page,quote,source_url,markdown_path,excerpt,unit_label,source_sha256,markdown_sha256,fragment_sha256,offset_start,offset_end)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(`ci-${sha(`${claim.id}:${page.page}:${citation.quote}:${page.offsetStart}`).slice(0,24)}`,claim.id,job.sourceId,page.page,citation.quote,
              `/original/${job.sourceId}#page=${page.page}`,page.relativePath,citation.quote,sourceMeta.get(job.sourceId)?.source.unitLabel || 'page PDF',job.sourceHash,page.pageSha256,page.fragmentSha256,page.offsetStart,page.offsetEnd);
          }
        }
      }
      for (const { relation, type, notes, evidence } of validatedRelationships) {
        const sourceNodeId = nodeIds.get(relation.sourceKey); const targetNodeId = nodeIds.get(relation.targetKey);
        const id = `rel-${sha(`${jobId}:${sourceNodeId}:${targetNodeId}:${type}`).slice(0,24)}`;
        db.prepare(`INSERT OR IGNORE INTO relationships(id,source_node_id,target_node_id,type,notes,job_id,review_status)
          VALUES(?,?,?,?,?,?,'unreviewed')`).run(id,sourceNodeId,targetNodeId,type,notes,jobId);
        const actual = db.prepare('SELECT id FROM relationships WHERE source_node_id=? AND target_node_id=? AND type=? AND job_id=?').get(sourceNodeId,targetNodeId,type,jobId).id;
        for (const citation of evidence) {
          const page = citation.page;
          db.prepare(`INSERT OR IGNORE INTO relationship_citations(relationship_id,source_id,page,quote,source_url,markdown_path,excerpt,unit_label,source_sha256,markdown_sha256,fragment_sha256,offset_start,offset_end)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(actual,job.sourceId,page.page,citation.quote,`/original/${job.sourceId}#page=${page.page}`,page.relativePath,citation.quote,
            sourceMeta.get(job.sourceId)?.source.unitLabel || 'page PDF',job.sourceHash,page.pageSha256,page.fragmentSha256,page.offsetStart,page.offsetEnd);
        }
      }
      for (const segment of validatedSegments) {
        const id=`seg-${sha(`${jobId}:${segment.key}`).slice(0,24)}`;
        db.prepare(`INSERT OR IGNORE INTO logical_segments(id,source_id,job_id,segment_key,title,kind,node_keys_json)
          VALUES(?,?,?,?,?,?,?)`).run(id,job.sourceId,jobId,segment.key,segment.title,segment.kind,json(segment.nodeKeys));
        const actual=db.prepare('SELECT id FROM logical_segments WHERE job_id=? AND segment_key=?').get(jobId,segment.key).id;
        for(const nodeKey of segment.nodeKeys) db.prepare('INSERT OR IGNORE INTO segment_nodes(segment_id,node_id) VALUES(?,?)').run(actual,nodeIds.get(nodeKey));
        for(const citation of segment.evidence) {
          const page=citation.page;
          db.prepare(`INSERT OR IGNORE INTO segment_citations(segment_id,source_id,page,quote,source_url,markdown_path,excerpt,unit_label,source_sha256,markdown_sha256,fragment_sha256,offset_start,offset_end)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(actual,job.sourceId,page.page,citation.quote,`/original/${job.sourceId}#page=${page.page}`,page.relativePath,citation.quote,
            sourceMeta.get(job.sourceId)?.source.unitLabel||'page PDF',job.sourceHash,page.pageSha256,page.fragmentSha256,page.offsetStart,page.offsetEnd);
        }
      }
      db.prepare("UPDATE jobs SET status='complete',coverage_json=?,review_required=1,notes_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?").run(json(coverage),json(coverage.notes),jobId);
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
    return { idempotent: false, nodes: validatedNodes.length, relationships: validatedRelationships.length, segments:validatedSegments.length, citations: citationCount, coverage };
  }

  function failJob(id, message) {
    const text = requireString(String(message), 'failure message').slice(0, 2000);
    return db.prepare("UPDATE jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'").run(text,id).changes === 1;
  }
  function retryFailedJobs({sourceIds=PILOT_SOURCE_IDS}={}) {
    if(!Array.isArray(sourceIds)||sourceIds.some(id=>typeof id!=='string')) throw new Error('sourceIds must be an array');
    if(!sourceIds.length) return 0;
    return db.prepare(`UPDATE jobs SET status='pending',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE status='failed' AND source_id IN (${sourceIds.map(()=>'?').join(',')})`).run(...sourceIds).changes;
  }

  function getSummary() {
    const citationCount=db.prepare('SELECT (SELECT COUNT(*) FROM citations)+(SELECT COUNT(*) FROM relationship_citations) n').get().n;
    return { schemaVersion: KBASE_SCHEMA_VERSION, sources: db.prepare('SELECT COUNT(*) n FROM sources').get().n,
      jobs: Object.fromEntries(db.prepare('SELECT status,COUNT(*) n FROM jobs GROUP BY status').all().map(row => [row.status,row.n])),
      nodes: db.prepare('SELECT COUNT(*) n FROM nodes').get().n, relationships: db.prepare('SELECT COUNT(*) n FROM relationships').get().n,
      citations:citationCount, evidence:citationCount,
      stateNodes: db.prepare("SELECT COUNT(*) n FROM nodes WHERE family='STATE'").get().n,
      canonicalSourceClaims: db.prepare("SELECT COUNT(*) n FROM claims c JOIN nodes n ON n.id=c.node_id JOIN citations x ON x.claim_id=c.id JOIN sources s ON s.id=x.source_id WHERE s.provenance='published-source'").get().n,
      inspirationClaims: db.prepare("SELECT COUNT(*) n FROM claims c JOIN nodes n ON n.id=c.node_id WHERE n.family='INSPIRATION'").get().n,
      proposedClaims: db.prepare("SELECT COUNT(*) n FROM claims c JOIN nodes n ON n.id=c.node_id WHERE n.review_status='unreviewed'").get().n,
      byFamily:Object.fromEntries(db.prepare('SELECT family,COUNT(*) n FROM nodes GROUP BY family').all().map(row=>[row.family,row.n])),
      byUsage:Object.fromEntries(db.prepare('SELECT usage,COUNT(*) n FROM nodes GROUP BY usage').all().map(row=>[row.usage,row.n])) };
  }

  function listNodes({ q, family, usage, limit = 50, offset = 0 } = {}) {
    const take = Math.max(1, Math.min(250, Number(limit) || 50)); const skip = Math.max(0, Number(offset) || 0);
    const clauses = []; const params = [];
    if (q) { clauses.push(`(n.name LIKE ? ESCAPE '\\' OR COALESCE(e.summary,n.summary) LIKE ? ESCAPE '\\' OR COALESCE(e.content,n.content) LIKE ? ESCAPE '\\'
      OR EXISTS(SELECT 1 FROM claims c WHERE c.node_id=n.id AND c.value_json LIKE ? ESCAPE '\\'))`); const escaped = `%${String(q).replace(/[\\%_]/gu, '\\$&')}%`; params.push(escaped,escaped,escaped,escaped); }
    if (family) { clauses.push('n.family=?'); params.push(family); }
    if (usage) { clauses.push('n.usage=?'); params.push(usage); }
    const where = clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
    const total = db.prepare(`SELECT COUNT(*) n FROM nodes n LEFT JOIN node_editorial e ON e.node_id=n.id ${where}`).get(...params).n;
    const rows = db.prepare(`SELECT n.*,e.summary editorial_summary,e.content editorial_content,e.tags_json editorial_tags,e.review_status editorial_review_status,e.revision
      FROM nodes n LEFT JOIN node_editorial e ON e.node_id=n.id ${where} ORDER BY n.name,n.id LIMIT ? OFFSET ?`).all(...params,take,skip);
    return { nodes: rows.map(decodeNode), total };
  }
  function decodeNode(row) { return { ...row, summary:row.editorial_summary ?? row.summary, content:row.editorial_content ?? row.content,
    tags:safeJson(row.editorial_tags ?? row.tags_json,[]), properties:safeJson(row.properties_json,{}), reviewStatus:row.editorial_review_status ?? row.review_status,
    revision:row.revision ?? 0, tags_json:undefined, properties_json:undefined, editorial_summary:undefined, editorial_content:undefined,
    editorial_tags:undefined, editorial_review_status:undefined };
  }
  function getNode(id) {
    const row = db.prepare(`SELECT n.*,e.summary editorial_summary,e.content editorial_content,e.tags_json editorial_tags,e.review_status editorial_review_status,e.revision
      FROM nodes n LEFT JOIN node_editorial e ON e.node_id=n.id WHERE n.id=?`).get(id); if (!row) return null;
    const node = decodeNode(row);
    node.claims = db.prepare('SELECT * FROM claims WHERE node_id=? ORDER BY field,job_id').all(id).map(claim => ({ ...claim,
      value:safeJson(claim.value_json,null), value_json:undefined,
      citations:db.prepare(`SELECT x.*,s.name source_name FROM citations x JOIN sources s ON s.id=x.source_id WHERE claim_id=? ORDER BY source_id,page`).all(claim.id).map(citation => ({
        sourceId:citation.source_id,sourceName:citation.source_name,page:citation.page,quote:citation.quote,fields:[claim.field],
        sourceUrl:citation.source_url,markdownPath:citation.markdown_path,excerpt:citation.excerpt,unitLabel:citation.unit_label,
        sourceSha256:citation.source_sha256,markdownSha256:citation.markdown_sha256,fragmentSha256:citation.fragment_sha256,
        offsetStart:citation.offset_start,offsetEnd:citation.offset_end
      })) }));
    node.evidence = node.claims.flatMap(claim => claim.citations);
    const evidenceOrder=(a,b)=>{
      const ar=ORIGINAL_SOURCE_IDS.indexOf(a.sourceId), br=ORIGINAL_SOURCE_IDS.indexOf(b.sourceId);
      return (ar<0?1000:ar)-(br<0?1000:br) || a.sourceId.localeCompare(b.sourceId) || a.page-b.page || a.offsetStart-b.offsetStart;
    };
    const contentClaims=node.claims.filter(claim=>claim.field==='content').map(claim=>({claimId:claim.id,value:claim.value,evidence:[...claim.citations].sort(evidenceOrder)}))
      .sort((a,b)=>evidenceOrder(a.evidence[0]||{sourceId:'',page:0,offsetStart:0},b.evidence[0]||{sourceId:'',page:0,offsetStart:0}));
    const originalContentClaim=node.claims.find(claim=>claim.field==='content'&&claim.value===row.content);
    node.sourceContent=contentClaims;
    node.contentUpdates=contentClaims.filter(claim=>claim.claimId!==originalContentClaim?.id);
    const summaryClaims=node.claims.filter(claim=>claim.field==='summary').map(claim=>({claimId:claim.id,value:claim.value,evidence:[...claim.citations].sort(evidenceOrder)}))
      .sort((a,b)=>evidenceOrder(a.evidence[0]||{sourceId:'',page:0,offsetStart:0},b.evidence[0]||{sourceId:'',page:0,offsetStart:0}));
    const originalSummaryClaim=node.claims.find(claim=>claim.field==='summary'&&claim.value===row.summary);
    node.sourceSummaries=summaryClaims;
    node.summaryUpdates=summaryClaims.filter(claim=>claim.claimId!==originalSummaryClaim?.id);
    const propertyGroups=new Map();
    for(const claim of node.claims.filter(item=>item.field.startsWith('property:'))){
      const key=claim.field.slice('property:'.length); if(!propertyGroups.has(key)) propertyGroups.set(key,[]);
      const group=propertyGroups.get(key); const valueKey=json(claim.value); let option=group.find(item=>item.valueKey===valueKey);
      if(!option){option={valueKey,value:claim.value,evidence:[]};group.push(option);}
      option.evidence.push(...claim.citations);
    }
    const mergedProperties={...node.properties}; const propertyAlternatives=[];
    for(const [key,options] of propertyGroups){
      if(options.length===1) mergedProperties[key]=options[0].value;
      else { delete mergedProperties[key]; propertyAlternatives.push({field:key,options:options.map(({value,evidence})=>({value,evidence:evidence.sort(evidenceOrder)}))}); }
    }
    node.properties=mergedProperties; node.propertyAlternatives=propertyAlternatives;
    node.relationships = db.prepare(`SELECT r.id,r.source_node_id sourceId,sn.name sourceName,r.target_node_id targetId,tn.name targetName,
      r.type,r.notes,r.review_status reviewStatus,CASE WHEN r.source_node_id=? THEN 'outgoing' ELSE 'incoming' END direction
      FROM relationships r JOIN nodes sn ON sn.id=r.source_node_id JOIN nodes tn ON tn.id=r.target_node_id WHERE r.source_node_id=? OR r.target_node_id=?`).all(id,id,id).map(relation=>({
        ...relation,evidence:db.prepare(`SELECT x.*,s.name source_name FROM relationship_citations x JOIN sources s ON s.id=x.source_id WHERE relationship_id=? ORDER BY source_id,page`).all(relation.id).map(citation=>({
          sourceId:citation.source_id,sourceName:citation.source_name,page:citation.page,quote:citation.quote,fields:['relation'],sourceUrl:citation.source_url,
          markdownPath:citation.markdown_path,excerpt:citation.excerpt,unitLabel:citation.unit_label,sourceSha256:citation.source_sha256,
          markdownSha256:citation.markdown_sha256,fragmentSha256:citation.fragment_sha256,offsetStart:citation.offset_start,offsetEnd:citation.offset_end
        }))
      }));
    node.segments=db.prepare(`SELECT s.id,s.segment_key key,s.title,s.kind,s.review_status reviewStatus FROM segment_nodes m
      JOIN logical_segments s ON s.id=m.segment_id WHERE m.node_id=? ORDER BY s.title,s.id`).all(id).map(segment=>({
        ...segment,evidence:db.prepare(`SELECT x.*,s.name source_name FROM segment_citations x JOIN sources s ON s.id=x.source_id WHERE segment_id=? ORDER BY source_id,page`).all(segment.id).map(citation=>({
          sourceId:citation.source_id,sourceName:citation.source_name,page:citation.page,quote:citation.quote,fields:['segment'],sourceUrl:citation.source_url,
          markdownPath:citation.markdown_path,excerpt:citation.excerpt,unitLabel:citation.unit_label,sourceSha256:citation.source_sha256,
          markdownSha256:citation.markdown_sha256,fragmentSha256:citation.fragment_sha256,offsetStart:citation.offset_start,offsetEnd:citation.offset_end
        }))
      }));
    return node;
  }

  function getSources() {
    return db.prepare('SELECT id,name,collection,usage,provenance,unit_label unitLabel,expected_units expectedUnits,locator_path locatorPath,transcription_sha256 transcriptionSha256,status FROM sources ORDER BY collection,name').all();
  }

  function updateNode(id, patch, expectedRevision) {
    if (!patch || typeof patch !== 'object' || Array.isArray(patch)) throw new KBaseError('Editorial patch must be an object');
    const allowed = new Set(['summary','content','tags','reviewStatus']);
    for (const key of Object.keys(patch)) if (!allowed.has(key)) throw new KBaseError(`Unsupported editorial field ${key}`);
    if (!Object.keys(patch).length) throw new KBaseError('Editorial patch is empty');
    if (!Number.isInteger(expectedRevision) || expectedRevision < 0) throw new KBaseError('expectedRevision must be a non-negative integer');
    if (patch.summary !== undefined && typeof patch.summary !== 'string') throw new KBaseError('summary must be text');
    if (patch.content !== undefined && typeof patch.content !== 'string') throw new KBaseError('content must be text');
    if (patch.tags !== undefined && (!Array.isArray(patch.tags) || patch.tags.some(tag => typeof tag !== 'string'))) throw new KBaseError('tags must be an array of text');
    if (patch.reviewStatus !== undefined && !['unreviewed','reviewed'].includes(patch.reviewStatus)) throw new KBaseError('reviewStatus must be unreviewed or reviewed');
    const current = getNode(id); if (!current) throw new KBaseError(`Unknown node ${id}`,404);
    if (current.revision !== expectedRevision) throw new KBaseError(`Revision conflict: expected ${expectedRevision}, current ${current.revision}`,409);
    backupBeforeImport();
    db.exec('BEGIN IMMEDIATE');
    try {
      const live = db.prepare('SELECT * FROM node_editorial WHERE node_id=?').get(id);
      const revision = live?.revision ?? 0;
      if (revision !== expectedRevision) throw new KBaseError(`Revision conflict: expected ${expectedRevision}, current ${revision}`,409);
      const previous = { summary:live?.summary ?? current.summary, content:live?.content ?? current.content,
        tags:safeJson(live?.tags_json,current.tags), reviewStatus:live?.review_status ?? current.reviewStatus };
      const next = { summary:patch.summary ?? previous.summary, content:patch.content ?? previous.content,
        tags:patch.tags ?? previous.tags, reviewStatus:patch.reviewStatus ?? previous.reviewStatus };
      const nextRevision = revision + 1;
      db.prepare(`INSERT INTO node_editorial(node_id,summary,content,tags_json,review_status,revision) VALUES(?,?,?,?,?,?)
        ON CONFLICT(node_id) DO UPDATE SET summary=excluded.summary,content=excluded.content,tags_json=excluded.tags_json,
        review_status=excluded.review_status,revision=excluded.revision,updated_at=CURRENT_TIMESTAMP`).run(id,next.summary,next.content,json(next.tags),next.reviewStatus,nextRevision);
      db.prepare('INSERT INTO node_editorial_history(id,node_id,revision,patch_json,previous_json) VALUES(?,?,?,?,?)').run(randomUUID(),id,nextRevision,json(patch),json(previous));
      db.exec('COMMIT');
      return getNode(id);
    } catch (error) { db.exec('ROLLBACK'); throw error; }
  }
  function exportGraph() {
    const nodes = db.prepare(`SELECT n.*,e.summary editorial_summary,e.content editorial_content,e.tags_json editorial_tags,e.review_status editorial_review_status,e.revision
      FROM nodes n LEFT JOIN node_editorial e ON e.node_id=n.id ORDER BY n.id`).all().map(row => {
      const node = decodeNode(row);
      node.claims = db.prepare(`SELECT c.field,c.value_json,s.id sourceId,s.name sourceName,s.collection,s.usage sourceUsage,s.provenance,x.page,x.quote,x.source_url sourceUrl,
        x.markdown_path markdownPath,x.excerpt,x.unit_label unitLabel,x.source_sha256 sourceSha256,x.markdown_sha256 markdownSha256,
        x.fragment_sha256 fragmentSha256,x.offset_start offsetStart,x.offset_end offsetEnd
        FROM claims c JOIN citations x ON x.claim_id=c.id JOIN sources s ON s.id=x.source_id WHERE c.node_id=? ORDER BY c.field,s.id,page`).all(row.id).map(c => ({
          field:c.field,value:safeJson(c.value_json,null),evidence:{sourceId:c.sourceId,sourceName:c.sourceName,collection:c.collection,usage:c.sourceUsage,
            provenance:c.provenance,page:c.page,quote:c.quote,sourceUrl:c.sourceUrl,markdownPath:c.markdownPath,excerpt:c.excerpt,unitLabel:c.unitLabel,
            sourceSha256:c.sourceSha256,markdownSha256:c.markdownSha256,fragmentSha256:c.fragmentSha256,offsetStart:c.offsetStart,offsetEnd:c.offsetEnd}
        }));
      return node;
    });
    const relationships = db.prepare(`SELECT r.id,r.source_node_id sourceId,r.target_node_id targetId,r.type,r.notes,r.review_status reviewStatus,
      x.source_id evidenceSourceId,s.name sourceName,s.collection,s.usage sourceUsage,s.provenance,x.page,x.quote,x.source_url sourceUrl,x.markdown_path markdownPath,
      x.excerpt,x.unit_label unitLabel,x.source_sha256 sourceSha256,x.markdown_sha256 markdownSha256
      FROM relationships r JOIN relationship_citations x ON x.relationship_id=r.id JOIN sources s ON s.id=x.source_id ORDER BY r.id,x.page`).all().map(r => ({
      id:r.id,sourceId:r.sourceId,targetId:r.targetId,type:r.type,notes:r.notes,reviewStatus:r.reviewStatus,
      evidence:{sourceId:r.evidenceSourceId,sourceName:r.sourceName,collection:r.collection,usage:r.sourceUsage,provenance:r.provenance,page:r.page,quote:r.quote,
        sourceUrl:r.sourceUrl,markdownPath:r.markdownPath,excerpt:r.excerpt,unitLabel:r.unitLabel,sourceSha256:r.sourceSha256,markdownSha256:r.markdownSha256}
    }));
    const segments=db.prepare(`SELECT s.id,s.source_id sourceId,s.job_id jobId,s.segment_key key,s.title,s.kind,s.node_keys_json nodeKeysJson,s.review_status reviewStatus,
      x.page,x.quote,x.source_url sourceUrl,x.markdown_path markdownPath,x.excerpt,x.unit_label unitLabel,x.source_sha256 sourceSha256,x.markdown_sha256 markdownSha256,
      x.fragment_sha256 fragmentSha256,x.offset_start offsetStart,x.offset_end offsetEnd
      FROM logical_segments s LEFT JOIN segment_citations x ON x.segment_id=s.id ORDER BY s.source_id,x.page,s.title`).all().reduce((all,row)=>{
      let segment=all.find(item=>item.id===row.id); if(!segment){segment={id:row.id,sourceId:row.sourceId,jobId:row.jobId,key:row.key,title:row.title,kind:row.kind,
        nodeKeys:safeJson(row.nodeKeysJson,[]),reviewStatus:row.reviewStatus,evidence:[]};all.push(segment);}
      if(row.page!==null) segment.evidence.push({sourceId:row.sourceId,page:row.page,quote:row.quote,sourceUrl:row.sourceUrl,markdownPath:row.markdownPath,
        excerpt:row.excerpt,unitLabel:row.unitLabel,sourceSha256:row.sourceSha256,markdownSha256:row.markdownSha256,fragmentSha256:row.fragmentSha256,
        offsetStart:row.offsetStart,offsetEnd:row.offsetEnd});
      return all;
    },[]);
    return { format:'darkstryder-kbase', version:1, schemaVersion:KBASE_SCHEMA_VERSION, summary:getSummary(), nodes, relationships, segments };
  }

  // Authored, bounded batches do not consume or falsely complete the old model queue.
  // They go through exactly the same hash, evidence and transactional validation.
  function importAuthoredBatch({sourceId,batchId,nodes,relationships=[],segments=[],notes=[]}) {
    requireString(batchId,'authored batch ID');
    if(!Array.isArray(nodes))throw new Error('Authored nodes must be an array');
    const numbers=new Set([...nodes,...relationships,...segments].flatMap(item=>(item.evidence||[]).map(e=>e.page)));
    if(!numbers.size||numbers.size>6)throw new Error('Authored batches require 1–6 cited pages');
    const ctx=getCtx(sourceId);
    const selected=collectPages(sourceId).filter(p=>numbers.has(p.page));
    if(selected.length!==numbers.size)throw new Error('Unknown authored source page');
    const descriptor=selected.map(p=>({page:p.page,fileName:p.fileName,pageSha256:p.sha256,fragmentSha256:p.sha256,offsetStart:0,offsetEnd:p.markdown.length}));
    const chunkHash=sha(json({authoring:'manual-v1',sourceId,batchId,sourceHash:ctx.sourceHash,pages:descriptor,nodes,relationships,segments,notes}));
    const id=`manual-${chunkHash.slice(0,24)}`;
    stmts.insertJob.run(id,sourceId,chunkHash,ctx.sourceHash,json(descriptor),10000,descriptor[0].page,0);
    const row=stmts.getJob.get(id);
    if(row.status!=='complete')stmts.updateJob.run('running',id);
    try{return {jobId:id,...importExtraction(id,{jobId:id,nodes,relationships,segments,notes:['Manually authored selection; not whole-page or whole-book coverage.',...notes]})};}
    catch(error){failJob(id,error.message);throw error;}
  }

  syncSources();
  return { syncSources, planJobs, claimNextJob, getJob, getJobStatuses, getJobAudit, importExtraction, importAuthoredBatch, failJob, retryFailedJobs, getSummary, getSources,
    listNodes, getNode, updateNode, exportGraph, close:() => db.close() };
}
