BEGIN;
CREATE OR REPLACE VIEW benefitlens.latest_candidate_opportunity AS
SELECT DISTINCT ON (o.opportunity_id)
    o.opportunity_id, p.canonical_name AS provider_name, p.provider_kind,
    v.opportunity_version_id, v.title, v.summary, v.official_url, v.application_url,
    v.opens_at, v.closes_at, v.lifecycle_status, v.publication_status, v.valid_from,
    r.source_id, s.source_group, r.finished_at AS last_collected_at
FROM benefitlens.opportunity o
JOIN benefitlens.provider p USING (provider_id)
JOIN benefitlens.opportunity_version v USING (opportunity_id)
JOIN platform.collection_run r USING (run_id)
JOIN platform.data_source s USING (source_id)
WHERE v.valid_to IS NULL AND o.retired_at IS NULL
ORDER BY o.opportunity_id, v.valid_from DESC;
COMMIT;
