BEGIN;

CREATE TABLE IF NOT EXISTS benefitlens.extraction_candidate (
    extraction_candidate_id bigserial PRIMARY KEY,
    opportunity_version_id uuid NOT NULL
        REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    raw_document_id bigint NOT NULL
        REFERENCES platform.raw_document(raw_document_id),
    field_kind text NOT NULL CHECK (
        field_kind IN ('benefit', 'eligibility', 'required_document', 'application_prompt')
    ),
    proposed_value jsonb NOT NULL,
    source_url text NOT NULL,
    source_kind text NOT NULL CHECK (
        source_kind IN ('api', 'html', 'pdf', 'ocr')
    ),
    source_quote text NOT NULL,
    confidence numeric(5,4) NOT NULL CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    extractor_kind text NOT NULL DEFAULT 'local_codex' CHECK (
        extractor_kind IN ('local_codex', 'llm_api')
    ),
    model_name text NOT NULL,
    prompt_version text NOT NULL,
    review_status text NOT NULL DEFAULT 'pending' CHECK (
        review_status IN ('pending', 'accepted', 'rejected')
    ),
    candidate_fingerprint_sha256 char(64) NOT NULL,
    reviewed_at timestamptz,
    reviewed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_version_id, candidate_fingerprint_sha256),
    CHECK (
        (review_status = 'pending' AND reviewed_at IS NULL)
        OR (review_status IN ('accepted', 'rejected') AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS extraction_candidate_review_idx
    ON benefitlens.extraction_candidate (review_status, created_at DESC);

COMMIT;
