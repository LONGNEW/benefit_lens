BEGIN;

ALTER TABLE benefitlens.criteria_definition
    ADD COLUMN IF NOT EXISTS condition_kind text NOT NULL DEFAULT 'eligibility'
    CHECK (condition_kind IN ('eligibility', 'requirement'));

UPDATE benefitlens.criteria_definition
SET condition_kind = CASE
    WHEN criterion_code IN (
        'online_learning_available', 'hackathon_available', 'activity_attendance',
        'fulltime_training_available', 'coding_study_interest'
    ) THEN 'requirement'
    ELSE 'eligibility'
END,
criteria_version = 'eligibility-1.3.0';

-- Only a derived reporting view is recreated; stored rules remain unchanged.
DROP VIEW IF EXISTS benefitlens.eligibility_coverage;
CREATE VIEW benefitlens.eligibility_coverage AS
SELECT
    v.opportunity_version_id,
    v.criteria_version,
    count(r.eligibility_rule_id) FILTER (
        WHERE r.machine_evaluable AND d.condition_kind = 'eligibility'
    ) AS structured_rule_count,
    count(r.eligibility_rule_id) FILTER (
        WHERE NOT r.machine_evaluable AND r.normalization_status = 'unresolved'
    ) AS unresolved_rule_count,
    CASE
        WHEN count(r.eligibility_rule_id) FILTER (WHERE r.machine_evaluable) = 0 THEN 'review_required'
        WHEN count(r.eligibility_rule_id) FILTER (
            WHERE NOT r.machine_evaluable AND r.normalization_status = 'unresolved'
        ) > 0 THEN 'partial'
        ELSE 'structured'
    END AS match_mode
FROM benefitlens.opportunity_version v
LEFT JOIN benefitlens.eligibility_rule r USING (opportunity_version_id)
LEFT JOIN benefitlens.criteria_definition d USING (criterion_code)
GROUP BY v.opportunity_version_id, v.criteria_version;

CREATE OR REPLACE VIEW benefitlens.requirement_coverage AS
SELECT v.opportunity_version_id,
       count(r.eligibility_rule_id) FILTER (
           WHERE r.machine_evaluable AND d.condition_kind = 'requirement'
       ) AS requirement_count
FROM benefitlens.opportunity_version v
LEFT JOIN benefitlens.eligibility_rule r USING (opportunity_version_id)
LEFT JOIN benefitlens.criteria_definition d USING (criterion_code)
GROUP BY v.opportunity_version_id;

INSERT INTO platform.schema_migration (version)
VALUES ('009_participation_requirements') ON CONFLICT (version) DO NOTHING;

COMMIT;
