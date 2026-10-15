-- Search discoveries and relay documents are evidence, not issuer originals.
ALTER TABLE platform.data_source DROP CONSTRAINT IF EXISTS data_source_source_kind_check;
ALTER TABLE platform.data_source ADD CONSTRAINT data_source_source_kind_check
CHECK (source_kind IN ('official_api', 'official_page', 'internal_web_endpoint', 'standard_master', 'public_reference'));
INSERT INTO platform.schema_migration(version) VALUES ('017_discovery_public_reference') ON CONFLICT DO NOTHING;
