BEGIN;
ALTER TABLE benefitlens.opportunity_version ADD COLUMN IF NOT EXISTS contract_version text NOT NULL DEFAULT 'opportunity-legacy';
ALTER TABLE benefitlens.opportunity_version ADD COLUMN IF NOT EXISTS condition_sections_json jsonb NOT NULL DEFAULT '{}';
ALTER TABLE benefitlens.benefit ADD COLUMN IF NOT EXISTS currency text;
ALTER TABLE benefitlens.benefit ADD COLUMN IF NOT EXISTS amount_qualifier text;
ALTER TABLE benefitlens.application_prompt ADD COLUMN IF NOT EXISTS source_url text;
INSERT INTO platform.schema_migration(version) VALUES ('015_opportunity_contract') ON CONFLICT DO NOTHING;
COMMIT;
