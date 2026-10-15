BEGIN;

CREATE TABLE IF NOT EXISTS benefitlens.criteria_definition (
    criterion_code text PRIMARY KEY,
    display_name text NOT NULL,
    profile_field text NOT NULL UNIQUE,
    value_type text NOT NULL CHECK (
        value_type IN ('string', 'integer', 'number', 'boolean', 'enum', 'date')
    ),
    canonical_unit text,
    allowed_operators jsonb NOT NULL,
    enum_values jsonb,
    criteria_version text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO benefitlens.criteria_definition (
    criterion_code, display_name, profile_field, value_type,
    canonical_unit, allowed_operators, enum_values, criteria_version
) VALUES
    ('nationality', '국적', 'nationality', 'enum', NULL, '["eq","in","neq","not_in"]', '["KR","FOREIGN"]', 'eligibility-1.0.1'),
    ('age', '만 나이', 'age', 'integer', 'year', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('academic_level', '학위 과정', 'academicLevel', 'enum', NULL, '["eq","in","neq","not_in"]', '["undergraduate","master","doctoral"]', 'eligibility-1.0.1'),
    ('highest_degree_level', '최종 취득 학위', 'highestDegreeLevel', 'enum', NULL, '["eq","in"]', '["high_school","associate","bachelor","master","doctoral"]', 'eligibility-1.0.1'),
    ('enrollment_status', '학적 상태', 'enrollmentStatus', 'enum', NULL, '["eq","in","neq","not_in"]', '["enrolled","leave","expected_graduation","graduated"]', 'eligibility-1.0.1'),
    ('academic_year', '학년', 'year', 'integer', 'academic_year', '["eq","gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('graduate_semester', '대학원 이수 학기', 'graduateSemester', 'integer', 'semester', '["eq","gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('coursework_completed', 'Coursework 수료', 'courseworkCompleted', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.0.1'),
    ('previous_semester_credits', '직전 학기 이수학점', 'previousSemesterCredits', 'number', 'credit', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('gpa', '평점', 'gpa', 'number', 'gpa_scale', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('income_band', '학자금 지원구간', 'incomeBand', 'integer', 'kosaf_band', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('residence_city', '본인 주민등록 지역', 'residenceCity', 'string', 'kr_admin_name', '["eq","in","neq","not_in"]', NULL, 'eligibility-1.0.1'),
    ('ancestor_residence_city', '직계존속 주민등록 지역', 'ancestorResidenceCity', 'string', 'kr_admin_name', '["eq","in","neq","not_in"]', NULL, 'eligibility-1.0.1'),
    ('residence_months', '연속 거주기간', 'residenceMonths', 'integer', 'month', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('regional_high_school', '비수도권 고교 졸업', 'regionalHighSchool', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.0.1'),
    ('topik_level', 'TOPIK 등급', 'topikLevel', 'integer', 'topik_level', '["gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('team_size', '지원 팀 인원', 'teamSize', 'integer', 'person', '["eq","gte","lte","between"]', NULL, 'eligibility-1.0.1'),
    ('employment_status', '취업 상태', 'employmentStatus', 'enum', NULL, '["eq","in","neq","not_in"]', '["employed","unemployed","self_employed"]', 'eligibility-1.0.1')
ON CONFLICT (criterion_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    profile_field = EXCLUDED.profile_field,
    value_type = EXCLUDED.value_type,
    canonical_unit = EXCLUDED.canonical_unit,
    allowed_operators = EXCLUDED.allowed_operators,
    enum_values = EXCLUDED.enum_values,
    criteria_version = EXCLUDED.criteria_version,
    active = true;

ALTER TABLE benefitlens.opportunity_version
    ADD COLUMN IF NOT EXISTS criteria_version text NOT NULL DEFAULT 'eligibility-1.0.1';
ALTER TABLE benefitlens.opportunity_version
    ALTER COLUMN criteria_version SET DEFAULT 'eligibility-1.0.1';

CREATE TABLE IF NOT EXISTS benefitlens.eligibility_rule_group (
    rule_group_id uuid PRIMARY KEY,
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    group_key text NOT NULL,
    parent_rule_group_id uuid REFERENCES benefitlens.eligibility_rule_group(rule_group_id),
    combinator text NOT NULL CHECK (combinator IN ('and', 'or', 'not')),
    label text NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (opportunity_version_id, group_key)
);

ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS rule_key text;
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS rule_group_id uuid REFERENCES benefitlens.eligibility_rule_group(rule_group_id);
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS criterion_code text REFERENCES benefitlens.criteria_definition(criterion_code);
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS profile_field text;
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS value_type text;
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS unit text;
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS display_label text;
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS normalization_status text NOT NULL DEFAULT 'unresolved';
ALTER TABLE benefitlens.eligibility_rule
    ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS eligibility_rule_version_key_idx
    ON benefitlens.eligibility_rule (opportunity_version_id, rule_key)
    WHERE rule_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS eligibility_rule_criterion_idx
    ON benefitlens.eligibility_rule (criterion_code)
    WHERE machine_evaluable = true;

CREATE TABLE IF NOT EXISTS benefitlens.profile_snapshot (
    profile_snapshot_id uuid PRIMARY KEY,
    subject_key text NOT NULL,
    criteria_version text NOT NULL,
    consent_version text NOT NULL,
    consented_at timestamptz NOT NULL,
    retention_until timestamptz NOT NULL,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (retention_until > consented_at)
);

CREATE TABLE IF NOT EXISTS benefitlens.profile_attribute (
    profile_attribute_id bigserial PRIMARY KEY,
    profile_snapshot_id uuid NOT NULL REFERENCES benefitlens.profile_snapshot(profile_snapshot_id),
    criterion_code text NOT NULL REFERENCES benefitlens.criteria_definition(criterion_code),
    value_json jsonb NOT NULL,
    value_type text NOT NULL,
    unit text,
    user_confirmed boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_snapshot_id, criterion_code)
);

CREATE TABLE IF NOT EXISTS benefitlens.evaluation (
    evaluation_id uuid PRIMARY KEY,
    profile_snapshot_id uuid NOT NULL REFERENCES benefitlens.profile_snapshot(profile_snapshot_id),
    opportunity_version_id uuid NOT NULL REFERENCES benefitlens.opportunity_version(opportunity_version_id),
    evaluator_version text NOT NULL,
    eligibility_status text NOT NULL CHECK (
        eligibility_status IN ('possible', 'check', 'unmet')
    ),
    completeness_status text NOT NULL CHECK (
        completeness_status IN ('complete', 'partial', 'unavailable')
    ),
    evaluated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benefitlens.evaluation_rule_result (
    evaluation_rule_result_id bigserial PRIMARY KEY,
    evaluation_id uuid NOT NULL REFERENCES benefitlens.evaluation(evaluation_id),
    eligibility_rule_id bigint NOT NULL REFERENCES benefitlens.eligibility_rule(eligibility_rule_id),
    result text NOT NULL CHECK (result IN ('pass', 'fail', 'unknown', 'not_evaluated')),
    actual_value_json jsonb,
    reason_code text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_id, eligibility_rule_id)
);

CREATE OR REPLACE VIEW benefitlens.eligibility_coverage AS
SELECT
    v.opportunity_version_id,
    v.criteria_version,
    count(r.eligibility_rule_id) FILTER (WHERE r.machine_evaluable) AS structured_rule_count,
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
GROUP BY v.opportunity_version_id, v.criteria_version;

INSERT INTO platform.schema_migration (version)
VALUES ('002_canonical_eligibility')
ON CONFLICT (version) DO NOTHING;

COMMIT;
