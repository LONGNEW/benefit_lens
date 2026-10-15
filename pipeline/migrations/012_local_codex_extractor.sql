BEGIN;

ALTER TABLE benefitlens.extraction_candidate
    DROP CONSTRAINT IF EXISTS extraction_candidate_extractor_kind_check;

UPDATE benefitlens.extraction_candidate
SET extractor_kind = 'llm_api'
WHERE extractor_kind = 'llm';

ALTER TABLE benefitlens.extraction_candidate
    ALTER COLUMN extractor_kind SET DEFAULT 'local_codex';

ALTER TABLE benefitlens.extraction_candidate
    ADD CONSTRAINT extraction_candidate_extractor_kind_check
    CHECK (extractor_kind IN ('local_codex', 'llm_api'));

COMMIT;
