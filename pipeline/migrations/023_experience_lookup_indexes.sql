CREATE INDEX IF NOT EXISTS pipeline_document_text_identity
  ON benefitlens.pipeline_document(text_sha256);
CREATE INDEX IF NOT EXISTS experience_source_url_identity
  ON benefitlens.experience_discovery_case(source_url);
CREATE INDEX IF NOT EXISTS experience_document_membership
  ON benefitlens.experience_discovery_case USING gin ((payload->'document_ids'));
INSERT INTO platform.schema_migration(version) VALUES('023_experience_lookup_indexes') ON CONFLICT DO NOTHING;
