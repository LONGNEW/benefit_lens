-- Keep original URLs intact; index a fixed-length URL identity instead.
-- Writers compare the full URL on a key conflict rather than merging silently.
ALTER TABLE benefitlens.exploration_url ADD COLUMN IF NOT EXISTS url_hash text
 GENERATED ALWAYS AS (md5(url)) STORED;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='benefitlens.exploration_url'::regclass
            AND conname='exploration_url_pkey' AND pg_get_constraintdef(oid)='PRIMARY KEY (run_id, url)') THEN
   ALTER TABLE benefitlens.exploration_url DROP CONSTRAINT exploration_url_pkey;
   ALTER TABLE benefitlens.exploration_url ADD PRIMARY KEY(run_id,url_hash);
 END IF;
END $$;
ALTER TABLE benefitlens.pipeline_document ADD COLUMN IF NOT EXISTS canonical_url_hash text
 GENERATED ALWAYS AS (md5(canonical_url)) STORED;
ALTER TABLE benefitlens.pipeline_document DROP CONSTRAINT IF EXISTS pipeline_document_canonical_url_content_sha256_key;
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_document_urlhash_content_key
 ON benefitlens.pipeline_document(canonical_url_hash,content_sha256);
INSERT INTO platform.schema_migration(version) VALUES('022_long_url_identity') ON CONFLICT DO NOTHING;
