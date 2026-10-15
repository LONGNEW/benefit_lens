BEGIN;

UPDATE benefitlens.criteria_definition
SET condition_kind = CASE WHEN criterion_code IN (
    'online_learning_available', 'hackathon_available', 'activity_attendance',
    'fulltime_training_available', 'coding_study_interest',
    'employment_preparation', 'training_card_eligible', 'enrollment_commitment'
) THEN 'requirement' ELSE 'eligibility' END,
criteria_version = 'eligibility-1.4.0';

INSERT INTO benefitlens.criteria_definition
    (criterion_code, display_name, profile_field, value_type, canonical_unit,
     allowed_operators, enum_values, criteria_version, condition_kind)
VALUES ('remaining_study_months', '남은 정규 수업연한', 'remainingStudyMonths',
        'integer', 'month', '["lte"]', NULL, 'eligibility-1.4.0', 'eligibility')
ON CONFLICT (criterion_code) DO UPDATE SET criteria_version = EXCLUDED.criteria_version;

INSERT INTO benefitlens.criteria_definition
    (criterion_code, display_name, profile_field, value_type, canonical_unit,
     allowed_operators, enum_values, criteria_version, condition_kind)
VALUES
    ('codetree_paid', '코드트리 결제 이력', 'codetreePaid', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.4.0', 'eligibility'),
    ('codetree_subscribed', '코드트리 현재 구독', 'codetreeSubscribed', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.4.0', 'eligibility'),
    ('codetree_prior_challenge', '코드트리 챌린지 참여 이력', 'codetreePriorChallenge', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.4.0', 'eligibility'),
    ('codetree_bc3_free_pass', '코드트리 이전 무료 학습권', 'codetreeBc3FreePass', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.4.0', 'eligibility')
ON CONFLICT (criterion_code) DO UPDATE SET criteria_version = EXCLUDED.criteria_version;

INSERT INTO platform.schema_migration (version)
VALUES ('010_referenced_eligibility') ON CONFLICT (version) DO NOTHING;
COMMIT;
