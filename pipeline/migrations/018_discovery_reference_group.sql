-- Supplementary public references need a group distinct from funding issuers.
ALTER TABLE platform.data_source DROP CONSTRAINT IF EXISTS data_source_source_group_check;
ALTER TABLE platform.data_source ADD CONSTRAINT data_source_source_group_check
CHECK (source_group IN ('school', 'foundation', 'company', 'national', 'other'));
INSERT INTO platform.schema_migration(version) VALUES ('018_discovery_reference_group') ON CONFLICT DO NOTHING;
