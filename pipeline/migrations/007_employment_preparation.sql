BEGIN;
INSERT INTO benefitlens.criteria_definition (criterion_code, display_name, profile_field, value_type, canonical_unit, allowed_operators, enum_values, criteria_version)
VALUES ('employment_preparation','취업 준비 목적 참여','employmentPreparation','boolean',NULL,'["eq"]',NULL,'eligibility-1.2.0')
ON CONFLICT (criterion_code) DO NOTHING;
COMMIT;
