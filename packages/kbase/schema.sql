PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO schema_meta(key,value) VALUES ('schema_version','4');

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, collection TEXT NOT NULL, usage TEXT NOT NULL,
  provenance TEXT NOT NULL, unit_label TEXT NOT NULL, expected_units INTEGER,
  locator_path TEXT, transcription_sha256 TEXT, status TEXT NOT NULL,
  discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  chunk_hash TEXT NOT NULL UNIQUE, source_sha256 TEXT NOT NULL, pages_json TEXT NOT NULL, status TEXT NOT NULL,
  source_priority INTEGER NOT NULL DEFAULT 1000, first_page INTEGER NOT NULL DEFAULT 1, first_offset INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0, notes_json TEXT NOT NULL DEFAULT '[]',
  coverage_json TEXT NOT NULL DEFAULT '{}', review_required INTEGER NOT NULL DEFAULT 1,
  error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
  family TEXT NOT NULL, usage TEXT NOT NULL, name TEXT NOT NULL, summary TEXT NOT NULL,
  content TEXT NOT NULL, tags_json TEXT NOT NULL, scope TEXT NOT NULL,
  visibility TEXT NOT NULL, properties_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS node_editorial (
  node_id TEXT PRIMARY KEY REFERENCES nodes(id), summary TEXT, content TEXT, tags_json TEXT,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS node_editorial_history (
  id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id), revision INTEGER NOT NULL,
  patch_json TEXT NOT NULL, previous_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id), field TEXT NOT NULL,
  value_json TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
  UNIQUE(node_id,field,job_id)
);
CREATE TABLE IF NOT EXISTS citations (
  id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(id),
  source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL,
  quote TEXT NOT NULL, source_url TEXT NOT NULL, markdown_path TEXT NOT NULL,
  excerpt TEXT NOT NULL, unit_label TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  markdown_sha256 TEXT NOT NULL, fragment_sha256 TEXT NOT NULL DEFAULT '', offset_start INTEGER NOT NULL DEFAULT 0,
  offset_end INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS relationships (
  id TEXT PRIMARY KEY, source_node_id TEXT NOT NULL REFERENCES nodes(id),
  target_node_id TEXT NOT NULL REFERENCES nodes(id), type TEXT NOT NULL,
  notes TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
  review_status TEXT NOT NULL DEFAULT 'unreviewed', UNIQUE(source_node_id,target_node_id,type,job_id)
);
CREATE TABLE IF NOT EXISTS relationship_citations (
  relationship_id TEXT NOT NULL REFERENCES relationships(id),
  source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL,
  quote TEXT NOT NULL, source_url TEXT NOT NULL, markdown_path TEXT NOT NULL,
  excerpt TEXT NOT NULL, unit_label TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  markdown_sha256 TEXT NOT NULL, fragment_sha256 TEXT NOT NULL DEFAULT '', offset_start INTEGER NOT NULL DEFAULT 0,
  offset_end INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(relationship_id,source_id,page,quote)
);
CREATE TABLE IF NOT EXISTS logical_segments (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), job_id TEXT NOT NULL REFERENCES jobs(id),
  segment_key TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, node_keys_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', UNIQUE(job_id,segment_key)
);
CREATE TABLE IF NOT EXISTS segment_nodes (
  segment_id TEXT NOT NULL REFERENCES logical_segments(id), node_id TEXT NOT NULL REFERENCES nodes(id),
  PRIMARY KEY(segment_id,node_id)
);
CREATE TABLE IF NOT EXISTS segment_citations (
  segment_id TEXT NOT NULL REFERENCES logical_segments(id), source_id TEXT NOT NULL REFERENCES sources(id), page INTEGER NOT NULL,
  quote TEXT NOT NULL, source_url TEXT NOT NULL, markdown_path TEXT NOT NULL, excerpt TEXT NOT NULL, unit_label TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, markdown_sha256 TEXT NOT NULL, fragment_sha256 TEXT NOT NULL DEFAULT '',
  offset_start INTEGER NOT NULL DEFAULT 0, offset_end INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(segment_id,source_id,page,offset_start,quote)
);
CREATE INDEX IF NOT EXISTS nodes_family_usage ON nodes(family,usage);
CREATE INDEX IF NOT EXISTS claims_node ON claims(node_id);
CREATE INDEX IF NOT EXISTS citations_source_page ON citations(source_id,page);
