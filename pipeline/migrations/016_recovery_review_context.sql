BEGIN;

ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS context_json jsonb NOT NULL DEFAULT '{}'::jsonb;

UPDATE benefitlens.criteria_definition SET criteria_version='eligibility-1.6.0';
UPDATE benefitlens.criteria_definition SET allowed_operators='["eq","gte","lte","between"]'::jsonb
WHERE criterion_code='topik_level';

ALTER TABLE benefitlens.extraction_candidate
    DROP CONSTRAINT IF EXISTS extraction_candidate_source_kind_check;
ALTER TABLE benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_source_kind_check
    CHECK (source_kind IN ('api', 'html', 'pdf', 'ocr', 'hwp', 'hwpx'));

CREATE TABLE IF NOT EXISTS benefitlens.review_event (
    review_event_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    extraction_candidate_id bigint REFERENCES benefitlens.extraction_candidate(extraction_candidate_id),
    action text NOT NULL CHECK (action IN ('promote', 'reject', 'reviewed')),
    reviewer text NOT NULL,
    reason text NOT NULL,
    packet_sha256 char(64) NOT NULL,
    detail_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (packet_sha256)
);

INSERT INTO platform.schema_migration(version)
VALUES ('016_recovery_review_context') ON CONFLICT (version) DO NOTHING;

COMMIT;
