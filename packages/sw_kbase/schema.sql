PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY,dataset TEXT NOT NULL,title TEXT NOT NULL,origin TEXT NOT NULL,
 metadata_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS records(
 id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),source_key TEXT NOT NULL,
 current_version TEXT NOT NULL,entity_id TEXT,active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(source_id,source_key));
CREATE TABLE IF NOT EXISTS record_versions(
 id TEXT PRIMARY KEY,record_id TEXT NOT NULL REFERENCES records(id),sha256 TEXT NOT NULL,
 name TEXT NOT NULL,kind TEXT NOT NULL,universe TEXT NOT NULL,origin TEXT NOT NULL,
 aliases_json TEXT NOT NULL,scope_json TEXT NOT NULL,locator_json TEXT NOT NULL,
 payload_json TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(record_id,sha256));
CREATE TABLE IF NOT EXISTS entities(
 id TEXT PRIMARY KEY,anchor_record_id TEXT NOT NULL REFERENCES records(id),name TEXT NOT NULL,
 kind TEXT NOT NULL,redirect_to TEXT REFERENCES entities(id),revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS names(
 form TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES records(id),original TEXT NOT NULL,
 PRIMARY KEY(form,record_id));
CREATE INDEX IF NOT EXISTS names_record ON names(record_id);
CREATE TABLE IF NOT EXISTS name_grams(
 gram TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES records(id),PRIMARY KEY(gram,record_id));
CREATE INDEX IF NOT EXISTS grams_record ON name_grams(record_id);
CREATE TABLE IF NOT EXISTS passages(
 id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES record_versions(id),sequence INTEGER NOT NULL,
 text TEXT NOT NULL,offset_start INTEGER NOT NULL,offset_end INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(record_id UNINDEXED,name,aliases,body,tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(passage_id UNINDEXED,record_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS profiles(
 id TEXT PRIMARY KEY,record_id TEXT NOT NULL REFERENCES records(id),version_id TEXT NOT NULL REFERENCES record_versions(id),
 scope_json TEXT NOT NULL,stats_json TEXT NOT NULL,UNIQUE(version_id));
CREATE TABLE IF NOT EXISTS profile_values(
 profile_id TEXT NOT NULL REFERENCES profiles(id),field TEXT NOT NULL,value_json TEXT NOT NULL,
 source_path TEXT NOT NULL,unit TEXT NOT NULL,PRIMARY KEY(profile_id,field,source_path));
CREATE INDEX IF NOT EXISTS profile_values_field ON profile_values(field);
CREATE TABLE IF NOT EXISTS proposals(
 id TEXT PRIMARY KEY,task TEXT NOT NULL,a TEXT NOT NULL REFERENCES records(id),b TEXT REFERENCES records(id),
 predicate TEXT NOT NULL DEFAULT '',context_json TEXT NOT NULL,signature TEXT NOT NULL,
 score REAL NOT NULL,reasons_json TEXT NOT NULL,decision TEXT,status TEXT NOT NULL DEFAULT 'pending',
 result_json TEXT,error TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS proposal_status ON proposals(status,task);
CREATE INDEX IF NOT EXISTS proposal_pair ON proposals(a,b,task);
CREATE TABLE IF NOT EXISTS proposal_inputs(
 proposal_id TEXT NOT NULL REFERENCES proposals(id),record_id TEXT NOT NULL REFERENCES records(id),
 version_id TEXT NOT NULL REFERENCES record_versions(id),PRIMARY KEY(proposal_id,record_id));
CREATE INDEX IF NOT EXISTS proposal_inputs_record ON proposal_inputs(record_id,proposal_id);
CREATE INDEX IF NOT EXISTS group_dependency ON proposals(json_extract(context_json,'$.parent_group_id'))
 WHERE task IN ('identity','mention');
CREATE TABLE IF NOT EXISTS resolutions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,proposal_id TEXT REFERENCES proposals(id),action TEXT NOT NULL,
 before_json TEXT NOT NULL,after_json TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cannot_links(
 a TEXT NOT NULL REFERENCES records(id),b TEXT NOT NULL REFERENCES records(id),reason TEXT NOT NULL,
 PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS links(
 id TEXT PRIMARY KEY,subject_record_id TEXT NOT NULL REFERENCES records(id),predicate TEXT NOT NULL,
 object_record_id TEXT NOT NULL REFERENCES records(id),proposal_id TEXT NOT NULL REFERENCES proposals(id),
 status TEXT NOT NULL,context_json TEXT NOT NULL,UNIQUE(proposal_id));
CREATE INDEX IF NOT EXISTS links_subject ON links(subject_record_id,predicate);
CREATE INDEX IF NOT EXISTS links_object ON links(object_record_id,predicate);
CREATE TABLE IF NOT EXISTS annotations(
 record_id TEXT NOT NULL REFERENCES records(id),task TEXT NOT NULL,proposal_id TEXT NOT NULL REFERENCES proposals(id),
 value_json TEXT NOT NULL,PRIMARY KEY(record_id,task));
CREATE TABLE IF NOT EXISTS editorial(
 entity_id TEXT PRIMARY KEY REFERENCES entities(id),revision INTEGER NOT NULL DEFAULT 1,
 content_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS syntheses(
 id TEXT PRIMARY KEY,entity_id TEXT NOT NULL REFERENCES entities(id),signature TEXT NOT NULL,
 result_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(entity_id,signature));
CREATE TABLE IF NOT EXISTS imports(
 id INTEGER PRIMARY KEY AUTOINCREMENT,dataset TEXT NOT NULL,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT,records_seen INTEGER NOT NULL DEFAULT 0,changed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mining_gaps(
 record_id TEXT NOT NULL REFERENCES records(id),stage TEXT NOT NULL,reason TEXT NOT NULL,
 details_json TEXT NOT NULL,PRIMARY KEY(record_id,stage,reason));
CREATE INDEX IF NOT EXISTS versions_record ON record_versions(record_id);
CREATE INDEX IF NOT EXISTS records_entity ON records(entity_id,active);
CREATE INDEX IF NOT EXISTS passages_version ON passages(version_id,sequence);
CREATE INDEX IF NOT EXISTS profiles_record ON profiles(record_id,version_id);
CREATE INDEX IF NOT EXISTS proposals_b ON proposals(b,task);
CREATE INDEX IF NOT EXISTS relation_grounding ON proposals(json_extract(context_json,'$.mention_proposal_id')) WHERE task='relation';
CREATE TABLE IF NOT EXISTS document_links(
 subject_record_id TEXT NOT NULL REFERENCES records(id),object_record_id TEXT NOT NULL REFERENCES records(id),
 kind TEXT NOT NULL,subject_version TEXT NOT NULL,object_version TEXT NOT NULL,context_json TEXT NOT NULL,
 PRIMARY KEY(subject_record_id,object_record_id,kind));
CREATE TABLE IF NOT EXISTS decision_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,proposal_id TEXT NOT NULL REFERENCES proposals(id),
 result_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS document_links_subject ON document_links(subject_record_id);
CREATE INDEX IF NOT EXISTS document_links_object ON document_links(object_record_id);
CREATE TABLE IF NOT EXISTS fts_record_rows(
 record_id TEXT PRIMARY KEY REFERENCES records(id),row_id INTEGER NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS fts_passage_rows(
 passage_id TEXT PRIMARY KEY REFERENCES passages(id),record_id TEXT NOT NULL REFERENCES records(id),
 row_id INTEGER NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS fts_passages_record ON fts_passage_rows(record_id);
CREATE TABLE IF NOT EXISTS applied_decisions(
 proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),result_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS grams_record_cover ON name_grams(record_id,gram);
CREATE TABLE IF NOT EXISTS application_queue(
 proposal_id TEXT PRIMARY KEY REFERENCES proposals(id));
CREATE INDEX IF NOT EXISTS proposals_work ON proposals(
 status,CASE task WHEN 'identity' THEN 0 WHEN 'mention' THEN 1 WHEN 'relation' THEN 2 WHEN 'merge_utility' THEN 3 ELSE 4 END,
 score DESC,a,id);
CREATE INDEX IF NOT EXISTS proposals_task_work ON proposals(status,task,score DESC,a,id);
CREATE INDEX IF NOT EXISTS covered_dependency ON proposals(json_extract(result_json,'$.covered_by_proposal'))
 WHERE status IN ('covered_by_resolved_mention','covered_by_resolved_relation');
CREATE TABLE IF NOT EXISTS cleaning_runs(
 id TEXT PRIMARY KEY,version TEXT NOT NULL,created_at TEXT NOT NULL,report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aggregates(
 id TEXT PRIMARY KEY,key TEXT NOT NULL UNIQUE,name TEXT NOT NULL,kind TEXT NOT NULL,
 universe TEXT NOT NULL,anchor_record_id TEXT NOT NULL REFERENCES records(id),
 warnings_json TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES cleaning_runs(id));
CREATE TABLE IF NOT EXISTS aggregate_members(
 record_id TEXT PRIMARY KEY REFERENCES records(id),aggregate_id TEXT NOT NULL REFERENCES aggregates(id),
 version_id TEXT NOT NULL REFERENCES record_versions(id),match_key TEXT NOT NULL,rule TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS aggregate_members_aggregate ON aggregate_members(aggregate_id,record_id);
CREATE TABLE IF NOT EXISTS aggregate_aliases(
 form TEXT NOT NULL,aggregate_id TEXT NOT NULL REFERENCES aggregates(id),label TEXT NOT NULL,
 PRIMARY KEY(form,aggregate_id));
CREATE INDEX IF NOT EXISTS aggregate_aliases_aggregate ON aggregate_aliases(aggregate_id);
CREATE INDEX IF NOT EXISTS aggregates_kind ON aggregates(kind,name);
CREATE TABLE IF NOT EXISTS aggregate_links(
 subject_id TEXT NOT NULL REFERENCES aggregates(id),object_id TEXT NOT NULL REFERENCES aggregates(id),
 kind TEXT NOT NULL,document_count INTEGER NOT NULL,occurrences INTEGER NOT NULL,
 flags_json TEXT NOT NULL,PRIMARY KEY(subject_id,object_id,kind));
CREATE INDEX IF NOT EXISTS aggregate_links_object ON aggregate_links(object_id,kind,subject_id);
CREATE TABLE IF NOT EXISTS aggregate_link_evidence(
 id TEXT PRIMARY KEY,subject_id TEXT NOT NULL REFERENCES aggregates(id),object_id TEXT NOT NULL REFERENCES aggregates(id),
 kind TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES records(id),version_id TEXT NOT NULL REFERENCES record_versions(id),
 offset_start INTEGER,excerpt TEXT NOT NULL,rule TEXT NOT NULL,context_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS aggregate_evidence_pair ON aggregate_link_evidence(subject_id,object_id,kind);
CREATE INDEX IF NOT EXISTS aggregate_evidence_record ON aggregate_link_evidence(record_id,object_id,kind);
CREATE TABLE IF NOT EXISTS cleaning_decisions(
 run_id TEXT NOT NULL REFERENCES cleaning_runs(id),proposal_id TEXT NOT NULL REFERENCES proposals(id),
 previous_status TEXT NOT NULL,new_status TEXT NOT NULL,reason TEXT NOT NULL,
 PRIMARY KEY(run_id,proposal_id));
CREATE TABLE IF NOT EXISTS record_sections(
 record_id TEXT PRIMARY KEY REFERENCES records(id),section TEXT NOT NULL,rule TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS record_sections_section ON record_sections(section,record_id);
CREATE TABLE IF NOT EXISTS aggregate_sections(
 aggregate_id TEXT NOT NULL REFERENCES aggregates(id),section TEXT NOT NULL,documents INTEGER NOT NULL,
 PRIMARY KEY(aggregate_id,section));
CREATE INDEX IF NOT EXISTS aggregate_sections_section ON aggregate_sections(section,aggregate_id);
CREATE TABLE IF NOT EXISTS aggregate_text_blocks(
 aggregate_id TEXT NOT NULL REFERENCES aggregates(id),hash TEXT NOT NULL,sequence INTEGER NOT NULL,text TEXT NOT NULL,
 PRIMARY KEY(aggregate_id,hash));
CREATE INDEX IF NOT EXISTS aggregate_text_order ON aggregate_text_blocks(aggregate_id,sequence);
CREATE TABLE IF NOT EXISTS aggregate_text_sources(
 aggregate_id TEXT NOT NULL,hash TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES records(id),
 version_id TEXT NOT NULL REFERENCES record_versions(id),offset_start INTEGER NOT NULL,offset_end INTEGER NOT NULL,
 PRIMARY KEY(aggregate_id,hash,record_id,offset_start),
 FOREIGN KEY(aggregate_id,hash) REFERENCES aggregate_text_blocks(aggregate_id,hash));
