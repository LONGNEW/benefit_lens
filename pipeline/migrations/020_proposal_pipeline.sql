-- Additive proposal/demo model. Existing registries are preserved.
CREATE TABLE IF NOT EXISTS benefitlens.pipeline_work_item (
    candidate_key text PRIMARY KEY REFERENCES benefitlens.support_discovery_record(candidate_key),
    source_family text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    reason text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pipeline_work_status_idx ON benefitlens.pipeline_work_item(status, source_family);
CREATE TABLE IF NOT EXISTS benefitlens.pipeline_document (
    document_id text PRIMARY KEY,
    canonical_url text NOT NULL,
    content_sha256 text NOT NULL,
    text_sha256 text NOT NULL,
    kind text NOT NULL,
    raw_path text NOT NULL,
    text_path text NOT NULL,
    fetched_at timestamptz NOT NULL,
    metadata jsonb NOT NULL,
    UNIQUE(canonical_url, content_sha256)
);
CREATE TABLE IF NOT EXISTS benefitlens.opportunities (
    opportunity_id text PRIMARY KEY,
    program_id text NOT NULL,
    round_id text,
    title text NOT NULL,
    category text NOT NULL,
    recipient_type text NOT NULL,
    issuer jsonb NOT NULL,
    eligibility jsonb,
    additional_checks jsonb NOT NULL DEFAULT '[]',
    benefits jsonb NOT NULL DEFAULT '[]',
    application jsonb NOT NULL DEFAULT '{}',
    requirements jsonb NOT NULL DEFAULT '[]',
    source_refs jsonb NOT NULL,
    explanation text NOT NULL,
    extraction_status text NOT NULL,
    resolution_status text NOT NULL,
    extractor text NOT NULL,
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS opportunities_program_idx ON benefitlens.opportunities(program_id);
CREATE INDEX IF NOT EXISTS opportunities_scope_idx ON benefitlens.opportunities(recipient_type,category,extraction_status);
CREATE INDEX IF NOT EXISTS opportunities_payload_idx ON benefitlens.opportunities USING gin(payload jsonb_path_ops);
CREATE TABLE IF NOT EXISTS benefitlens.opportunity_revision (
    opportunity_id text NOT NULL REFERENCES benefitlens.opportunities(opportunity_id),
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(opportunity_id,version_hash)
);
CREATE TABLE IF NOT EXISTS benefitlens.program_knowledge (
    program_id text PRIMARY KEY,
    experience_cases jsonb NOT NULL,
    writing_pattern jsonb NOT NULL,
    version_hash text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS benefitlens.pipeline_search_wave (
    run_id text NOT NULL,
    wave integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(run_id,wave)
);
INSERT INTO platform.schema_migration(version) VALUES('020_proposal_pipeline') ON CONFLICT DO NOTHING;
