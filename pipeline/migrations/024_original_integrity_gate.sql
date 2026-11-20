BEGIN;
CREATE TABLE IF NOT EXISTS benefitlens.source_integrity_check (
    document_id text PRIMARY KEY REFERENCES benefitlens.pipeline_document(document_id),
    raw_sha256 text NOT NULL,
    text_sha256 text NOT NULL,
    status text NOT NULL CHECK (status IN ('verified', 'unavailable')),
    reason text NOT NULL,
    text_characters integer NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE VIEW benefitlens.notice_original_ready AS
SELECT r.* FROM benefitlens.support_discovery_record r
JOIN benefitlens.pipeline_document d ON d.document_id = r.payload->>'document_id'
JOIN benefitlens.source_integrity_check v ON v.document_id = d.document_id
WHERE r.record_type = 'candidate' AND r.capture_status = 'original_available'
  AND coalesce((r.payload->>'active')::boolean, true)
  AND v.status = 'verified' AND v.raw_sha256 = d.content_sha256
  AND v.text_sha256 = d.text_sha256;
CREATE OR REPLACE VIEW benefitlens.experience_original_ready AS
SELECT c.* FROM benefitlens.experience_discovery_case c
WHERE c.evidence_status = 'public_original_collected'
  AND coalesce((c.payload->>'active')::boolean, true)
  AND NOT (c.payload ? 'legacy_case_id')
  AND c.case_key NOT LIKE 'writing-corpus-legacy-%'
  AND coalesce(c.payload->>'example_kind', '') <> 'synthetic'
  AND coalesce(c.payload->>'sample_scope', '') <> 'demo'
  AND jsonb_array_length(coalesce(c.payload->'document_ids','[]')) > 0
  AND NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(c.payload->'document_ids') x(id)
    LEFT JOIN benefitlens.pipeline_document d ON d.document_id=x.id
    LEFT JOIN benefitlens.source_integrity_check v ON v.document_id=x.id
    WHERE d.document_id IS NULL OR v.document_id IS NULL OR v.status <> 'verified'
      OR v.raw_sha256 <> d.content_sha256 OR v.text_sha256 <> d.text_sha256
  );
COMMIT;
