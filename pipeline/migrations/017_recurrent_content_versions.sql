-- Equal content may recur after an intervening failed or changed extraction.
-- Preserve both validity intervals rather than leaving the failed version current.
ALTER TABLE benefitlens.opportunity_version
    DROP CONSTRAINT IF EXISTS opportunity_version_opportunity_id_content_fingerprint_sha2_key;

CREATE INDEX IF NOT EXISTS opportunity_version_content_history_idx
    ON benefitlens.opportunity_version (opportunity_id, content_fingerprint_sha256);

-- one_current_opportunity_version_idx continues to enforce one current version.
