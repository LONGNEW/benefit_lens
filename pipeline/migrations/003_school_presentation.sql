BEGIN;
ALTER TABLE benefitlens.opportunity_version ADD COLUMN IF NOT EXISTS record_kind text NOT NULL DEFAULT 'notice' CHECK (record_kind IN ('notice', 'policy'));
ALTER TABLE benefitlens.opportunity_version ADD COLUMN IF NOT EXISTS presentation_json jsonb NOT NULL DEFAULT '{}';
INSERT INTO benefitlens.criteria_definition (criterion_code, display_name, profile_field, value_type, canonical_unit, allowed_operators, enum_values, criteria_version) VALUES
('institution', '소속 학교', 'institution', 'enum', NULL, '["eq","in"]', '["POSTECH","OTHER"]', 'eligibility-1.1.0'),
('department', '소속 학과', 'department', 'enum', NULL, '["eq","in"]', NULL, 'eligibility-1.1.0')
ON CONFLICT (criterion_code) DO UPDATE SET criteria_version=EXCLUDED.criteria_version;
UPDATE benefitlens.criteria_definition SET criteria_version='eligibility-1.1.0';
COMMIT;
