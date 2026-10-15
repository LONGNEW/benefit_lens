BEGIN;

INSERT INTO benefitlens.criteria_definition
    (criterion_code, display_name, profile_field, value_type, canonical_unit,
     allowed_operators, enum_values, criteria_version, condition_kind)
VALUES
    ('industry_employee', '산업체 재직 여부', 'industryEmployee', 'boolean', NULL,
     '["eq"]', NULL, 'eligibility-1.5.0', 'eligibility')
ON CONFLICT (criterion_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    profile_field = EXCLUDED.profile_field,
    value_type = EXCLUDED.value_type,
    allowed_operators = EXCLUDED.allowed_operators,
    criteria_version = EXCLUDED.criteria_version,
    condition_kind = EXCLUDED.condition_kind;

UPDATE benefitlens.criteria_definition
SET criteria_version = 'eligibility-1.5.0';

UPDATE benefitlens.opportunity_version old_version
SET valid_to = now(), publication_status = 'rejected'
FROM benefitlens.opportunity old_opportunity
WHERE old_version.opportunity_id = old_opportunity.opportunity_id
  AND old_version.valid_to IS NULL
  AND old_version.official_url = 'https://gift.postech.ac.kr/sub4_2.php'
  AND EXISTS (
      SELECT 1
      FROM benefitlens.opportunity_version current_version
      WHERE current_version.valid_to IS NULL
        AND current_version.official_url = 'https://gift.postech.ac.kr/sub4_2_a.php'
  );

UPDATE benefitlens.opportunity old_opportunity
SET retired_at = now()
WHERE EXISTS (
    SELECT 1
    FROM benefitlens.opportunity_version old_version
    WHERE old_version.opportunity_id = old_opportunity.opportunity_id
      AND old_version.official_url = 'https://gift.postech.ac.kr/sub4_2.php'
)
AND EXISTS (
    SELECT 1
    FROM benefitlens.opportunity_version current_version
    WHERE current_version.valid_to IS NULL
      AND current_version.official_url = 'https://gift.postech.ac.kr/sub4_2_a.php'
);

UPDATE platform.data_source old_source
SET enabled = false, updated_at = now()
WHERE old_source.canonical_url = 'https://gift.postech.ac.kr/sub4_2.php'
  AND EXISTS (
      SELECT 1
      FROM platform.data_source current_source
      WHERE current_source.canonical_url = 'https://gift.postech.ac.kr/sub4_2_a.php'
  );

INSERT INTO platform.schema_migration (version)
VALUES ('013_school_policy_identity')
ON CONFLICT (version) DO NOTHING;

COMMIT;
