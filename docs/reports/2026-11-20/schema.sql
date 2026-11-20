-- Actual derived SQLite DDL exported after the executed run.

CREATE TABLE applicant_fact(
 fact_id TEXT PRIMARY KEY,subject_id TEXT NOT NULL,status TEXT CHECK(status IN ('completed','ongoing','planned')),
 provenance TEXT CHECK(provenance IN ('user_supplied','synthetic_fixture')),
 fact_text TEXT NOT NULL);

CREATE TABLE condition_dimension(
 field_id TEXT PRIMARY KEY,label TEXT NOT NULL,description TEXT NOT NULL);

CREATE TABLE condition_dimension_link(
 mapping_id TEXT PRIMARY KEY REFERENCES condition_observation(mapping_id),
 field_id TEXT REFERENCES condition_dimension(field_id),mapping_status TEXT CHECK(mapping_status='legacy_alias_only'));

CREATE TABLE condition_evidence(
 condition_id TEXT REFERENCES semantic_condition(condition_id),span_id TEXT REFERENCES source_span(span_id),
 PRIMARY KEY(condition_id,span_id));

CREATE TABLE condition_execution_gate(
 condition_id TEXT PRIMARY KEY REFERENCES semantic_condition(condition_id),
 review_status TEXT CHECK(review_status IN ('unreviewed','reviewed','rejected')),
 executable INTEGER CHECK(executable=0), parent_dimension TEXT NOT NULL,
 value_type TEXT NOT NULL, value_json TEXT CHECK(json_valid(value_json)),
 unit TEXT, reference_phase TEXT NOT NULL);

CREATE TABLE condition_observation(
 mapping_id TEXT PRIMARY KEY, notice_id TEXT REFERENCES notice(notice_id),
 namespace TEXT CHECK(namespace IN ('common','specific')), field_id TEXT,
 specific_key TEXT, role TEXT, subject TEXT, operator TEXT, value_quote TEXT,
 executable INTEGER CHECK(executable=0), evidence_json TEXT, record_json TEXT);

CREATE TABLE draft(
 draft_id TEXT PRIMARY KEY, target_document_id TEXT, subject_id TEXT,
 input_sha256 TEXT, prompt_version TEXT, draft_json TEXT, invocation TEXT);

CREATE TABLE draft_fact_binding(
 draft_id TEXT REFERENCES draft(draft_id),paragraph_index INTEGER,
 fact_id TEXT REFERENCES applicant_fact(fact_id),PRIMARY KEY(draft_id,paragraph_index,fact_id));

CREATE TABLE draft_sentence_binding(
 draft_id TEXT REFERENCES draft(draft_id),paragraph_index INTEGER,
 sentence_role TEXT CHECK(sentence_role IN ('opening','support','closing')),
 slot_name TEXT,fact_id TEXT REFERENCES applicant_fact(fact_id),slot_tense TEXT,
 PRIMARY KEY(draft_id,paragraph_index,sentence_role,slot_name,fact_id));

CREATE TABLE draft_slot_value(
 draft_id TEXT REFERENCES draft(draft_id),slot_name TEXT,value_text TEXT,
 value_kind TEXT CHECK(value_kind IN ('source_fact','plan','editorial_question','unknown')),
 slot_tense TEXT,source_ids_json TEXT CHECK(json_valid(source_ids_json)),
 PRIMARY KEY(draft_id,slot_name));

CREATE TABLE normalized_condition(
 node_id TEXT PRIMARY KEY,document_id TEXT REFERENCES source_document(document_id),
 field_id TEXT REFERENCES condition_dimension(field_id),namespace TEXT CHECK(namespace IN ('common','specific')),
 role TEXT CHECK(role IN ('eligibility','submission','evaluation','benefit','operating_constraint')),
 value_type TEXT CHECK(value_type IN ('boolean','number','string','array','object','date','null')),
 value_json TEXT CHECK(json_valid(value_json)),unit TEXT,scope_json TEXT CHECK(json_valid(scope_json)),
 evidence_span_id TEXT REFERENCES source_span(span_id),executable INTEGER CHECK(executable=0));

CREATE TABLE notice(
 notice_id TEXT PRIMARY KEY, title TEXT, source_url TEXT, source_open_flag INTEGER,
 checked_at TEXT, metadata_json TEXT);

CREATE TABLE notice_condition_coverage(
 notice_id TEXT PRIMARY KEY REFERENCES notice(notice_id),mapping_count INTEGER NOT NULL,
 reason_code TEXT NOT NULL,meaning_review_status TEXT NOT NULL);

CREATE TABLE notice_document(
 notice_id TEXT REFERENCES notice(notice_id),document_id TEXT REFERENCES source_document(document_id),
 doc_role TEXT, retrieved_at TEXT, PRIMARY KEY(notice_id,document_id));

CREATE TABLE retrieval_item(
 item_id TEXT PRIMARY KEY, kind TEXT, program_id TEXT, document_id TEXT,
 representation TEXT, representation_sha256 TEXT, record_json TEXT);

CREATE TABLE retrieval_link(
 query_id TEXT, item_id TEXT REFERENCES retrieval_item(item_id), rank INTEGER,
 cosine REAL, relation TEXT, record_json TEXT, PRIMARY KEY(query_id,item_id));

CREATE TABLE semantic_condition(
 condition_id TEXT PRIMARY KEY, document_id TEXT REFERENCES source_document(document_id),
 namespace TEXT, field_id TEXT, role TEXT, typed_value_json TEXT,
 logic_json TEXT, evidence_quote TEXT, review_status TEXT, record_json TEXT);

CREATE TABLE semantic_document(
 document_id TEXT PRIMARY KEY REFERENCES source_document(document_id),
 analysis_json TEXT, invocation TEXT);

CREATE TABLE source_document(
 document_id TEXT PRIMARY KEY, source_url TEXT, text_path TEXT, text_sha256 TEXT,
 source_role TEXT, program_id TEXT, whole_text TEXT, metadata_json TEXT);

CREATE TABLE source_span(
 span_id TEXT PRIMARY KEY,document_id TEXT REFERENCES source_document(document_id),
 text_sha256 TEXT NOT NULL,char_start INTEGER NOT NULL CHECK(char_start>=0),
 char_end INTEGER NOT NULL CHECK(char_end>char_start),exact_quote TEXT NOT NULL,
 verification TEXT NOT NULL);

CREATE TABLE strategy_template(
 template_id TEXT PRIMARY KEY, genre TEXT, strategy_json TEXT, template_json TEXT,
 evidence_json TEXT, invocation TEXT);

CREATE INDEX condition_notice ON condition_observation(notice_id);

CREATE VIEW eligibility_execution AS
 SELECT condition_id,'unknown' AS decision FROM condition_execution_gate WHERE executable=0;

CREATE VIEW notice_common_condition AS SELECT * FROM normalized_condition WHERE namespace='common';

CREATE VIEW notice_specific_condition AS SELECT * FROM normalized_condition WHERE namespace='specific';

CREATE TRIGGER bound_sentence_status BEFORE INSERT ON draft_sentence_binding
 WHEN NEW.slot_tense IN ('completed','ongoing','planned') AND NEW.slot_tense !=
      (SELECT status FROM applicant_fact WHERE fact_id=NEW.fact_id)
 BEGIN SELECT RAISE(ABORT,'fact_status_does_not_match_slot_tense'); END;

CREATE TRIGGER bound_sentence_subject BEFORE INSERT ON draft_sentence_binding
 WHEN (SELECT subject_id FROM draft WHERE draft_id=NEW.draft_id) !=
      (SELECT subject_id FROM applicant_fact WHERE fact_id=NEW.fact_id)
 BEGIN SELECT RAISE(ABORT,'fact_subject_does_not_match_draft'); END;

CREATE TRIGGER draft_subject_boundary BEFORE INSERT ON draft_fact_binding
 WHEN (SELECT subject_id FROM draft WHERE draft_id=NEW.draft_id) !=
      (SELECT subject_id FROM applicant_fact WHERE fact_id=NEW.fact_id)
 BEGIN SELECT RAISE(ABORT,'public_or_other_subject_fact_cannot_fill_applicant_slot'); END;

CREATE TRIGGER field_role_boundary_insert BEFORE INSERT ON normalized_condition
 WHEN NEW.role='eligibility' AND NEW.field_id IN ('notice.amount','notice.deadline','application.documents')
 BEGIN SELECT RAISE(ABORT,'benefit_or_submission_cannot_be_eligibility'); END;

CREATE TRIGGER field_role_boundary_update BEFORE UPDATE ON normalized_condition
 WHEN NEW.role='eligibility' AND NEW.field_id IN ('notice.amount','notice.deadline','application.documents')
 BEGIN SELECT RAISE(ABORT,'benefit_or_submission_cannot_be_eligibility'); END;
