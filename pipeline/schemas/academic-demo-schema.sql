-- Isolated fixture DB. No student data or connection to the existing 70-item D1 DB.
PRAGMA foreign_keys = ON;

CREATE TABLE fixture_inputs (
  input_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
  byte_count INTEGER NOT NULL CHECK(byte_count >= 0)
);

CREATE TABLE demo_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);

-- source_key identifies one metadata observation. sha is only the original file SHA.
-- A metadata-only web source has sha NULL; no invented original-document hash.
CREATE TABLE source_versions (
  source_key TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  sha TEXT CHECK(sha IS NULL OR length(sha) = 64),
  version TEXT,
  url TEXT NOT NULL,
  checked TEXT,
  published TEXT,
  full_json TEXT NOT NULL,
  UNIQUE(source_key, source_id, sha)
);

CREATE TABLE rule_versions (
  rule_version TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_sha TEXT NOT NULL CHECK(length(source_sha) = 64),
  scope TEXT NOT NULL CHECK(scope = 'academic_language_only'),
  input_id TEXT NOT NULL REFERENCES fixture_inputs(input_id),
  rule_json TEXT NOT NULL,
  FOREIGN KEY(source_key, source_id, source_sha)
    REFERENCES source_versions(source_key, source_id, sha)
);

CREATE TABLE notices (
  notice_id TEXT PRIMARY KEY,
  input_order INTEGER NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  admission_status TEXT NOT NULL,
  rule_version TEXT NOT NULL UNIQUE REFERENCES rule_versions(rule_version),
  full_json TEXT NOT NULL
);

-- Paths are alternatives selected by education/context, not a combined AND.
-- Grade and English are AND; alternatives within each required group are OR.
CREATE TABLE rule_paths (
  rule_version TEXT NOT NULL REFERENCES rule_versions(rule_version),
  path_id TEXT NOT NULL,
  path_order INTEGER NOT NULL,
  education_path TEXT NOT NULL,
  grade_applicability TEXT NOT NULL CHECK(grade_applicability IN ('required','not_applicable','unknown')),
  english_applicability TEXT NOT NULL CHECK(english_applicability IN ('required','not_applicable','unknown')),
  alternatives_operator TEXT NOT NULL DEFAULT 'OR' CHECK(alternatives_operator = 'OR'),
  groups_operator TEXT NOT NULL DEFAULT 'AND' CHECK(groups_operator = 'AND'),
  path_json TEXT NOT NULL,
  PRIMARY KEY(rule_version, path_id)
);

CREATE TABLE grade_rules (
  rule_version TEXT NOT NULL,
  path_id TEXT NOT NULL,
  alternative_id TEXT NOT NULL,
  alternative_order INTEGER NOT NULL,
  metric TEXT NOT NULL CHECK(metric IN ('GPA','percentile')),
  minimum_score REAL NOT NULL CHECK(minimum_score >= 0),
  scale REAL NOT NULL CHECK(scale > 0 AND minimum_score <= scale),
  period TEXT NOT NULL,
  context TEXT NOT NULL,
  evidence_ids_json TEXT NOT NULL,
  rule_json TEXT NOT NULL,
  PRIMARY KEY(rule_version, path_id, alternative_id),
  FOREIGN KEY(rule_version, path_id) REFERENCES rule_paths(rule_version, path_id),
  CHECK(metric <> 'percentile' OR scale = 100)
);

CREATE TABLE english_rules (
  rule_version TEXT NOT NULL,
  path_id TEXT NOT NULL,
  alternative_id TEXT NOT NULL,
  alternative_order INTEGER NOT NULL,
  test_type TEXT NOT NULL,
  score_kind TEXT NOT NULL CHECK(score_kind IN ('numeric','ordered_enum')),
  minimum_numeric REAL,
  minimum_enum TEXT,
  scheme_id TEXT NOT NULL, -- exact input score_scale; no score conversion
  score_domain_json TEXT,
  window_from TEXT,
  window_to TEXT,
  window_json TEXT,
  supported INTEGER NOT NULL CHECK(supported IN (0,1)),
  evidence_ids_json TEXT NOT NULL,
  domain_evidence_ids_json TEXT NOT NULL,
  rule_json TEXT NOT NULL,
  PRIMARY KEY(rule_version, path_id, alternative_id),
  FOREIGN KEY(rule_version, path_id) REFERENCES rule_paths(rule_version, path_id),
  CHECK((score_kind = 'numeric' AND minimum_numeric IS NOT NULL AND minimum_enum IS NULL)
     OR (score_kind = 'ordered_enum' AND minimum_numeric IS NULL AND minimum_enum IS NOT NULL)),
  CHECK(minimum_numeric IS NULL OR minimum_numeric >= 0),
  CHECK((window_from IS NULL AND window_to IS NULL) OR
        (window_from IS NOT NULL AND window_to IS NOT NULL AND window_from <= window_to)),
  CHECK(supported = 0 OR (score_kind = 'numeric' AND score_domain_json IS NOT NULL))
);

-- quote may be NULL for a content_ref. meta_json retains URL, catalog version,
-- summary/quote status, independent_of_notice and reviewed range without coercion.
CREATE TABLE source_evidence (
  source_key TEXT NOT NULL REFERENCES source_versions(source_key),
  evidence_id TEXT NOT NULL,
  locator TEXT,
  quote TEXT,
  url TEXT NOT NULL,
  meta_json TEXT NOT NULL,
  PRIMARY KEY(source_key, evidence_id)
);

-- Validated evidence links include group-, alternative-, domain- and window-level roles.
CREATE TABLE rule_evidence_refs (
  rule_version TEXT NOT NULL,
  path_id TEXT NOT NULL,
  rule_kind TEXT NOT NULL CHECK(rule_kind IN ('grade','english')),
  alternative_id TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK(role IN ('group','threshold','domain','window')),
  source_key TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  PRIMARY KEY(rule_version,path_id,rule_kind,alternative_id,role,evidence_id),
  FOREIGN KEY(rule_version,path_id) REFERENCES rule_paths(rule_version,path_id),
  FOREIGN KEY(source_key,evidence_id) REFERENCES source_evidence(source_key,evidence_id)
);

CREATE TABLE writing_resources (
  record_id TEXT PRIMARY KEY,
  input_order INTEGER NOT NULL UNIQUE,
  notice_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  academic_path TEXT NOT NULL,
  ai_writing_policy TEXT NOT NULL,
  automatic_eligibility_use INTEGER NOT NULL CHECK(automatic_eligibility_use = 0),
  input_id TEXT NOT NULL REFERENCES fixture_inputs(input_id),
  record_json TEXT NOT NULL
);

CREATE TABLE writing_resource_sources (
  record_id TEXT NOT NULL REFERENCES writing_resources(record_id),
  source_key TEXT NOT NULL REFERENCES source_versions(source_key),
  source_role TEXT NOT NULL CHECK(source_role IN ('official','unofficial')),
  PRIMARY KEY(record_id,source_key)
);

CREATE TABLE writing_resource_evidence (
  record_id TEXT NOT NULL REFERENCES writing_resources(record_id),
  evidence_id TEXT NOT NULL,
  source_key TEXT NOT NULL,
  PRIMARY KEY(record_id,evidence_id),
  FOREIGN KEY(source_key,evidence_id) REFERENCES source_evidence(source_key,evidence_id),
  FOREIGN KEY(record_id,source_key) REFERENCES writing_resource_sources(record_id,source_key)
);

CREATE TABLE writing_questions (
  record_id TEXT NOT NULL REFERENCES writing_resources(record_id),
  question_id TEXT NOT NULL,
  question_order INTEGER NOT NULL,
  title TEXT NOT NULL,
  wording_status TEXT NOT NULL,
  question_json TEXT NOT NULL,
  PRIMARY KEY(record_id,question_id)
);

CREATE TABLE writing_question_evidence (
  record_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('question','length_limit')),
  evidence_id TEXT NOT NULL,
  PRIMARY KEY(record_id,question_id,role,evidence_id),
  FOREIGN KEY(record_id,question_id) REFERENCES writing_questions(record_id,question_id),
  FOREIGN KEY(record_id,evidence_id) REFERENCES writing_resource_evidence(record_id,evidence_id)
);

CREATE INDEX idx_notice_status ON notices(admission_status);
CREATE INDEX idx_grade_metric ON grade_rules(metric,scale,period,context);
CREATE INDEX idx_english_scheme ON english_rules(test_type,scheme_id,supported);
