BEGIN;

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS benefitlens;

CREATE TABLE IF NOT EXISTS platform.schema_migration (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.data_source (
    source_id text PRIMARY KEY,
    provider_name text NOT NULL,
    provider_kind text NOT NULL,
    source_group text NOT NULL CHECK (
        source_group IN ('school', 'foundation', 'company', 'national')
    ),
    source_kind text NOT NULL CHECK (
        source_kind IN ('official_api', 'official_page', 'internal_web_endpoint', 'standard_master')
    ),
    canonical_url text NOT NULL,
    approval_status text NOT NULL DEFAULT 'not_required' CHECK (
        approval_status IN ('not_required', 'not_applied', 'applied', 'approved', 'rejected')
    ),
    rights_status text NOT NULL DEFAULT 'review_required' CHECK (
        rights_status IN ('review_required', 'approved', 'restricted', 'rejected')
    ),
    enabled boolean NOT NULL DEFAULT true,
    schedule_allowed boolean NOT NULL DEFAULT false,
    production_allowed boolean NOT NULL DEFAULT false,
    poll_interval_seconds integer CHECK (poll_interval_seconds > 0),
    contract_version text NOT NULL DEFAULT '1',
    last_rights_reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE platform.data_source
    ADD COLUMN IF NOT EXISTS source_group text NOT NULL DEFAULT 'national' CHECK (
        source_group IN ('school', 'foundation', 'company', 'national')
    );

ALTER TABLE platform.data_source ALTER COLUMN source_group DROP DEFAULT;

CREATE TABLE IF NOT EXISTS platform.collection_run (
    run_id uuid PRIMARY KEY,
    source_id text NOT NULL REFERENCES platform.data_source(source_id),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    run_status text NOT NULL CHECK (
        run_status IN ('running', 'succeeded', 'quarantined', 'failed', 'blocked')
    ),
    http_status integer,
    request_url_redacted text,
    final_url text,
    content_type text,
    raw_record_count integer CHECK (raw_record_count >= 0),
    normalized_record_count integer CHECK (normalized_record_count >= 0),
    raw_payload_sha256 char(64),
    parser_name text,
    parser_version text,
    error_class text,
    error_message_redacted text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS collection_run_source_started_idx
    ON platform.collection_run (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS platform.raw_document (
    raw_document_id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES platform.collection_run(run_id),
    source_id text NOT NULL REFERENCES platform.data_source(source_id),
    source_url text NOT NULL,
    final_url text NOT NULL,
    fetched_at timestamptz NOT NULL,
    http_status integer NOT NULL,
    content_type text,
    response_headers jsonb NOT NULL DEFAULT '{}'::jsonb,
    body bytea NOT NULL,
    body_size_bytes integer NOT NULL CHECK (body_size_bytes >= 0),
    body_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, body_sha256)
);

CREATE INDEX IF NOT EXISTS raw_document_source_fetched_idx
    ON platform.raw_document (source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS platform.document_version (
    document_version_id bigserial PRIMARY KEY,
    source_id text NOT NULL REFERENCES platform.data_source(source_id),
    canonical_url text NOT NULL,
    body_sha256 char(64) NOT NULL,
    raw_document_id bigint NOT NULL REFERENCES platform.raw_document(raw_document_id),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    valid_to timestamptz,
    is_current boolean NOT NULL DEFAULT true,
    UNIQUE (source_id, canonical_url, body_sha256)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_document_version_idx
    ON platform.document_version (source_id, canonical_url)
    WHERE is_current = true;

CREATE TABLE IF NOT EXISTS platform.quality_result (
    quality_result_id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES platform.collection_run(run_id),
    check_name text NOT NULL,
    check_scope text NOT NULL,
    check_status text NOT NULL CHECK (check_status IN ('pass', 'warn', 'fail')),
    message text NOT NULL,
    observed_value jsonb,
    expected_value jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, check_name, check_scope)
);

CREATE TABLE IF NOT EXISTS benefitlens.provider (
    provider_id uuid PRIMARY KEY,
    canonical_name text NOT NULL UNIQUE,
    provider_kind text NOT NULL CHECK (
        provider_kind IN (
            'central_government', 'local_government', 'public_agency', 'university',
            'company', 'corporate_foundation', 'public_foundation', 'association_nonprofit'
        )
    ),
    official_url text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.opportunity (
    opportunity_id uuid PRIMARY KEY,
    provider_id uuid NOT NULL REFERENCES benefitlens.provider(provider_id),
    canonical_key text NOT NULL UNIQUE,
    first_seen_at timestamptz NOT NULL,
    retired_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.opportunity_version (
    opportunity_version_id uuid PRIMARY KEY,
    opportunity_id uuid NOT NULL REFERENCES benefitlens.opportunity(opportunity_id),
    run_id uuid NOT NULL REFERENCES platform.collection_run(run_id),
    document_version_id bigint NOT NULL REFERENCES platform.document_version(document_version_id),
    content_fingerprint_sha256 char(64) NOT NULL,
    title text NOT NULL,
    summary text,
    official_url text NOT NULL,
    application_url text,
    opens_at timestamptz,
    closes_at timestamptz,
    lifecycle_status text NOT NULL CHECK (
        lifecycle_status IN ('upcoming', 'open', 'closed', 'rolling', 'unknown')
    ),
    publication_status text NOT NULL DEFAULT 'needs_review' CHECK (
        publication_status IN ('needs_review', 'reviewed', 'rejected', 'quarantined')
    ),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_id, content_fingerprint_sha256),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_opportunity_version_idx
    ON benefitlens.opportunity_version (opportunity_id)
    WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS benefitlens.benefit (
    benefit_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    benefit_type text NOT NULL CHECK (
        benefit_type IN ('cash', 'education_cost', 'housing_finance', 'goods', 'service', 'opportunity', 'unknown')
    ),
    description text NOT NULL,
    amount_min numeric,
    amount_max numeric,
    amount_unit text,
    recurrence text,
    repayable boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.eligibility_rule (
    eligibility_rule_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    rule_type text NOT NULL,
    operator text,
    value_json jsonb,
    rule_text text NOT NULL,
    machine_evaluable boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.evidence_span (
    evidence_span_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    raw_document_id bigint NOT NULL REFERENCES platform.raw_document(raw_document_id),
    field_name text NOT NULL,
    evidence_text text NOT NULL,
    char_start integer CHECK (char_start >= 0),
    char_end integer CHECK (char_end >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (char_start IS NULL OR char_end IS NULL OR char_end >= char_start)
);

CREATE TABLE IF NOT EXISTS benefitlens.required_document (
    required_document_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    document_name text NOT NULL,
    requirement_text text NOT NULL,
    evidence_span_id bigint REFERENCES benefitlens.evidence_span(evidence_span_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.application_prompt (
    application_prompt_id uuid PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    prompt_key text NOT NULL,
    prompt_text text NOT NULL,
    character_limit integer CHECK (character_limit > 0),
    required_elements jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_span_id bigint REFERENCES benefitlens.evidence_span(evidence_span_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_version_id, prompt_key)
);

CREATE TABLE IF NOT EXISTS benefitlens.writing_style_profile (
    writing_style_profile_id uuid PRIMARY KEY,
    subject_key text NOT NULL,
    style_label text NOT NULL,
    sample_sha256 char(64),
    derived_features jsonb NOT NULL DEFAULT '{}'::jsonb,
    consented_at timestamptz NOT NULL,
    retention_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (retention_until > consented_at)
);

CREATE TABLE IF NOT EXISTS benefitlens.experience_fact (
    experience_fact_id uuid PRIMARY KEY,
    subject_key text NOT NULL,
    fact_type text NOT NULL,
    fact_text text NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('user_input', 'uploaded_document')),
    user_confirmed boolean NOT NULL DEFAULT false,
    confirmed_at timestamptz,
    retention_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT user_confirmed OR confirmed_at IS NOT NULL),
    CHECK (retention_until > created_at)
);

CREATE TABLE IF NOT EXISTS benefitlens.draft_version (
    draft_version_id uuid PRIMARY KEY,
    application_prompt_id uuid NOT NULL REFERENCES benefitlens.application_prompt(application_prompt_id),
    writing_style_profile_id uuid REFERENCES benefitlens.writing_style_profile(writing_style_profile_id),
    generator_kind text NOT NULL CHECK (generator_kind IN ('deterministic_baseline', 'llm')),
    generator_name text NOT NULL,
    draft_text text NOT NULL,
    user_confirmed boolean NOT NULL DEFAULT false,
    retention_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (retention_until > created_at)
);

CREATE TABLE IF NOT EXISTS benefitlens.draft_claim (
    draft_claim_id bigserial PRIMARY KEY,
    draft_version_id uuid NOT NULL REFERENCES benefitlens.draft_version(draft_version_id),
    sentence_index integer NOT NULL CHECK (sentence_index >= 0),
    claim_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (draft_version_id, sentence_index)
);

CREATE TABLE IF NOT EXISTS benefitlens.claim_provenance (
    claim_provenance_id bigserial PRIMARY KEY,
    draft_claim_id bigint NOT NULL REFERENCES benefitlens.draft_claim(draft_claim_id),
    provenance_kind text NOT NULL CHECK (
        provenance_kind IN ('official_requirement', 'experience_fact', 'needs_confirmation')
    ),
    evidence_span_id bigint REFERENCES benefitlens.evidence_span(evidence_span_id),
    experience_fact_id uuid REFERENCES benefitlens.experience_fact(experience_fact_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (provenance_kind = 'official_requirement' AND evidence_span_id IS NOT NULL AND experience_fact_id IS NULL)
        OR (provenance_kind = 'experience_fact' AND evidence_span_id IS NULL AND experience_fact_id IS NOT NULL)
        OR (provenance_kind = 'needs_confirmation' AND evidence_span_id IS NULL AND experience_fact_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS benefitlens.discovery_hit (
    discovery_hit_id bigserial PRIMARY KEY,
    query_text text NOT NULL,
    result_url text NOT NULL,
    discovered_at timestamptz NOT NULL,
    official_source_id text REFERENCES platform.data_source(source_id),
    verification_status text NOT NULL CHECK (
        verification_status IN ('unverified', 'official_confirmed', 'rejected')
    ),
    UNIQUE (query_text, result_url, discovered_at)
);

CREATE OR REPLACE VIEW benefitlens.latest_candidate_opportunity AS
SELECT DISTINCT ON (o.opportunity_id)
    o.opportunity_id,
    p.canonical_name AS provider_name,
    p.provider_kind,
    v.opportunity_version_id,
    v.title,
    v.summary,
    v.official_url,
    v.application_url,
    v.opens_at,
    v.closes_at,
    v.lifecycle_status,
    v.publication_status,
    v.valid_from,
    r.source_id,
    s.source_group,
    r.finished_at AS last_collected_at
FROM benefitlens.opportunity o
JOIN benefitlens.provider p USING (provider_id)
JOIN benefitlens.opportunity_version v USING (opportunity_id)
JOIN platform.collection_run r USING (run_id)
JOIN platform.data_source s USING (source_id)
WHERE v.valid_to IS NULL
ORDER BY o.opportunity_id, v.valid_from DESC;

CREATE OR REPLACE VIEW benefitlens.latest_publishable_opportunity AS
SELECT c.*
FROM benefitlens.latest_candidate_opportunity c
JOIN platform.data_source s USING (source_id)
WHERE c.publication_status = 'reviewed'
  AND s.rights_status = 'approved'
  AND s.production_allowed = true;

INSERT INTO platform.schema_migration (version)
VALUES ('001_platform')
ON CONFLICT (version) DO NOTHING;

COMMIT;
