BEGIN;
INSERT INTO benefitlens.criteria_definition (criterion_code, display_name, profile_field, value_type, canonical_unit, allowed_operators, enum_values, criteria_version)
VALUES ('enrollment_commitment', '활동 종료까지 학적 유지 가능', 'enrollmentCommitment', 'boolean', NULL, '["eq"]', NULL, 'eligibility-1.1.1')
ON CONFLICT (criterion_code) DO UPDATE SET criteria_version=EXCLUDED.criteria_version;
UPDATE benefitlens.criteria_definition SET criteria_version='eligibility-1.1.1';
COMMIT;
