-- Discovery is independent of issuer approval and individual eligibility.
CREATE TABLE IF NOT EXISTS benefitlens.support_discovery_record (
    candidate_key text PRIMARY KEY,
    source_url text NOT NULL,
    title text NOT NULL,
    title_basis text NOT NULL,
    source_family text NOT NULL,
    source_record_id text,
    record_type text NOT NULL,
    capture_status text NOT NULL,
    issuer_name text,
    issuer_kind text NOT NULL DEFAULT 'unknown',
    source_country text,
    funding_hints jsonb NOT NULL DEFAULT '[]',
    detail_text text,
    payload jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    rights_status text NOT NULL DEFAULT 'review_required',
    eligibility_status text NOT NULL DEFAULT 'not_evaluated'
);
CREATE INDEX IF NOT EXISTS support_discovery_family_idx
    ON benefitlens.support_discovery_record(source_family);
CREATE INDEX IF NOT EXISTS support_discovery_capture_idx
    ON benefitlens.support_discovery_record(capture_status, record_type);
CREATE TABLE IF NOT EXISTS benefitlens.support_discovery_observation (
    observation_key text PRIMARY KEY,
    candidate_key text NOT NULL REFERENCES benefitlens.support_discovery_record(candidate_key),
    source_family text NOT NULL,
    listing_url text NOT NULL,
    evidence_sha256 text,
    payload jsonb NOT NULL,
    observed_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS benefitlens.support_discovery_batch (
    batch_id text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    manifest jsonb NOT NULL
);
INSERT INTO platform.schema_migration(version)
VALUES ('019_broad_support_collection') ON CONFLICT DO NOTHING;
