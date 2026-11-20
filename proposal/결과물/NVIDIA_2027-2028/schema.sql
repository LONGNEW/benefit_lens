-- BenefitLens delivery v1. Run only on a fresh, isolated PostgreSQL-compatible DB.
BEGIN;

CREATE TABLE program (
    program_id text PRIMARY KEY,
    title text NOT NULL,
    issuer text NOT NULL,
    opportunity_type text NOT NULL
        CHECK (opportunity_type IN ('scholarship', 'fellowship', 'admission_funding', 'internship', 'other'))
);

CREATE TABLE opportunity_round (
    program_id text NOT NULL REFERENCES program(program_id),
    round_id text NOT NULL,
    opportunity_id text NOT NULL UNIQUE,
    notice_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('open', 'closed', 'upcoming', 'historical', 'unknown')),
    official_url text NOT NULL CHECK (official_url ~ '^https?://'),
    application_url text CHECK (application_url ~ '^https?://'),
    opens_on date,
    closes_on date,
    announces_on date,
    checked_on date NOT NULL,
    PRIMARY KEY (program_id, round_id),
    CHECK (opens_on <= closes_on)
);

CREATE TABLE source_document (
    document_id text PRIMARY KEY,
    title text NOT NULL,
    url text NOT NULL CHECK (url ~ '^https?://'),
    kind text NOT NULL,
    capture_mode text NOT NULL CHECK (capture_mode IN ('full_document', 'public_web_excerpt')),
    fetched_at timestamptz NOT NULL,
    raw_sha256 text CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
    text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    raw_path text,
    text_path text NOT NULL,
    CHECK ((capture_mode = 'full_document' AND raw_sha256 IS NOT NULL AND raw_path IS NOT NULL)
        OR (capture_mode = 'public_web_excerpt' AND raw_sha256 IS NULL AND raw_path IS NULL)),
    UNIQUE (url, raw_sha256, text_sha256)
);

CREATE TABLE evidence (
    evidence_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES source_document(document_id),
    program_id text NOT NULL REFERENCES program(program_id),
    round_id text,
    source_role text NOT NULL CHECK (source_role IN (
        'official_notice', 'official_guideline', 'official_form', 'application_portal',
        'outcome_record', 'application_statement', 'application_index', 'interview',
        'panel', 'reflection', 'other'
    )),
    locator text NOT NULL CHECK (length(trim(locator)) > 0),
    quote text NOT NULL CHECK (length(trim(quote)) > 0),
    FOREIGN KEY (program_id, round_id) REFERENCES opportunity_round(program_id, round_id),
    UNIQUE (evidence_id, program_id),
    UNIQUE (evidence_id, program_id, round_id),
    UNIQUE (evidence_id, document_id, program_id)
);

CREATE TABLE claim (
    claim_id text PRIMARY KEY,
    program_id text NOT NULL,
    round_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN (
        'eligibility', 'benefit', 'activity', 'submission', 'form', 'advice', 'schedule', 'selection_criterion'
    )),
    field text NOT NULL,
    at text,
    value jsonb NOT NULL,
    review_state text NOT NULL CHECK (review_state IN ('reviewed', 'unknown', 'ambiguous')),
    FOREIGN KEY (program_id, round_id) REFERENCES opportunity_round(program_id, round_id),
    UNIQUE (claim_id, program_id, round_id),
    CHECK (review_state <> 'reviewed' OR value <> 'null'::jsonb)
);

CREATE TABLE claim_evidence (
    claim_id text NOT NULL,
    evidence_id text NOT NULL,
    program_id text NOT NULL,
    round_id text NOT NULL,
    PRIMARY KEY (claim_id, evidence_id),
    FOREIGN KEY (claim_id, program_id, round_id) REFERENCES claim(claim_id, program_id, round_id),
    FOREIGN KEY (evidence_id, program_id, round_id) REFERENCES evidence(evidence_id, program_id, round_id)
);

CREATE TABLE experience_case (
    case_id text PRIMARY KEY,
    program_id text NOT NULL REFERENCES program(program_id),
    experience_round_id text,
    experience_round_label text,
    outcome_status text NOT NULL CHECK (outcome_status IN ('selected', 'not_selected', 'unknown')),
    outcome_basis text NOT NULL CHECK (outcome_basis IN ('official_record', 'author_report', 'unknown')),
    outcome_evidence_id text,
    notes text NOT NULL,
    UNIQUE (case_id, program_id),
    FOREIGN KEY (program_id, experience_round_id) REFERENCES opportunity_round(program_id, round_id),
    FOREIGN KEY (outcome_evidence_id, program_id) REFERENCES evidence(evidence_id, program_id),
    CHECK ((outcome_status = 'unknown' AND outcome_basis = 'unknown')
        OR (outcome_status <> 'unknown' AND outcome_basis <> 'unknown' AND outcome_evidence_id IS NOT NULL))
);

CREATE TABLE case_document (
    case_id text NOT NULL,
    document_id text NOT NULL REFERENCES source_document(document_id),
    program_id text NOT NULL,
    document_role text NOT NULL CHECK (document_role IN (
        'application_statement', 'application_index', 'outcome_record', 'interview', 'panel', 'reflection', 'other'
    )),
    finality text NOT NULL CHECK (finality IN ('unknown', 'public_draft', 'confirmed_final', 'not_applicable')),
    binding_evidence_id text,
    PRIMARY KEY (case_id, document_id, document_role),
    UNIQUE (case_id, document_id, document_role, program_id),
    FOREIGN KEY (case_id, program_id) REFERENCES experience_case(case_id, program_id),
    FOREIGN KEY (binding_evidence_id, program_id) REFERENCES evidence(evidence_id, program_id),
    CHECK (document_role = 'application_statement' OR finality = 'not_applicable')
);

CREATE TABLE writing_pattern (
    pattern_id text PRIMARY KEY,
    program_id text NOT NULL,
    target_round_id text NOT NULL,
    target_claim_id text,
    pattern_kind text NOT NULL CHECK (pattern_kind IN ('structure', 'strengthen', 'avoid', 'question')),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    scope_status text NOT NULL CHECK (scope_status IN (
        'same_round', 'historical_transfer', 'cross_program', 'general', 'official_only'
    )),
    causal_success_claim boolean NOT NULL DEFAULT false CHECK (NOT causal_success_claim),
    UNIQUE (pattern_id, program_id, target_round_id),
    FOREIGN KEY (program_id, target_round_id) REFERENCES opportunity_round(program_id, round_id),
    FOREIGN KEY (target_claim_id, program_id, target_round_id) REFERENCES claim(claim_id, program_id, round_id)
);

CREATE TABLE pattern_evidence (
    pattern_id text NOT NULL,
    program_id text NOT NULL,
    target_round_id text NOT NULL,
    evidence_id text NOT NULL,
    document_id text NOT NULL,
    source_program_id text NOT NULL,
    source_round_id text,
    case_id text,
    case_document_role text,
    purpose text NOT NULL CHECK (purpose IN ('case_observation', 'official_constraint', 'official_guidance')),
    PRIMARY KEY (pattern_id, evidence_id),
    FOREIGN KEY (pattern_id, program_id, target_round_id)
        REFERENCES writing_pattern(pattern_id, program_id, target_round_id),
    FOREIGN KEY (evidence_id, document_id, source_program_id)
        REFERENCES evidence(evidence_id, document_id, program_id),
    FOREIGN KEY (evidence_id, source_program_id, source_round_id)
        REFERENCES evidence(evidence_id, program_id, round_id),
    FOREIGN KEY (case_id, document_id, case_document_role, source_program_id)
        REFERENCES case_document(case_id, document_id, document_role, program_id),
    CHECK (
        (purpose = 'case_observation' AND case_id IS NOT NULL AND case_document_role IS NOT NULL)
        OR
        (purpose IN ('official_constraint', 'official_guidance') AND case_id IS NULL AND case_document_role IS NULL
         AND source_program_id = program_id AND source_round_id IS NOT NULL AND source_round_id = target_round_id)
    )
);

CREATE INDEX claim_round_kind_idx ON claim(program_id, round_id, kind);
CREATE INDEX pattern_round_idx ON writing_pattern(program_id, target_round_id);
CREATE INDEX case_program_idx ON experience_case(program_id);
COMMIT;
