BEGIN;
INSERT INTO benefitlens.criteria_definition (criterion_code, display_name, profile_field, value_type, canonical_unit, allowed_operators, enum_values, criteria_version)
VALUES ('expected_graduation_month','졸업 예정 월','expectedGraduationMonth','string','calendar_month','["eq"]',NULL,'eligibility-1.2.0')
ON CONFLICT (criterion_code) DO NOTHING;
COMMIT;
