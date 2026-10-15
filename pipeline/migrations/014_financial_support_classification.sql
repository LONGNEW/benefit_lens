CREATE TABLE IF NOT EXISTS benefitlens.funding_classification (
    opportunity_version_id uuid PRIMARY KEY REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    classifier_version text NOT NULL,
    tags_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(tags_json) = 'array'),
    evidence_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence_json) = 'array'),
    classification_status text NOT NULL CHECK (classification_status IN ('targeted', 'unconfirmed')),
    reason text NOT NULL,
    classified_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_funding_classification_tags
    ON benefitlens.funding_classification USING gin (tags_json);
