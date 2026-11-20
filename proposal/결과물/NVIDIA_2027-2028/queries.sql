-- Q1. One round, typed claims and each exact supporting span.
SELECT r.opportunity_id, r.notice_version, r.status, r.checked_on,
       r.opens_on, r.closes_on, r.announces_on, r.official_url, r.application_url,
       c.claim_id, c.kind, c.field, c.at, c.value, c.review_state,
       COALESCE(s.sources, '[]'::jsonb) AS sources
FROM opportunity_round r
JOIN claim c USING (program_id, round_id)
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'evidence_id', e.evidence_id, 'source_role', e.source_role,
        'document_id', d.document_id, 'url', d.url, 'fetched_at', d.fetched_at,
        'capture_mode', d.capture_mode, 'text_sha256', d.text_sha256,
        'locator', e.locator, 'quote', e.quote
    ) ORDER BY e.evidence_id) AS sources
    FROM claim_evidence ce
    JOIN evidence e USING (evidence_id)
    JOIN source_document d USING (document_id)
    WHERE ce.claim_id = c.claim_id
) s ON true
WHERE r.opportunity_id = 'nvidia-2027-2028'
ORDER BY c.kind, c.field, c.claim_id;

-- Q2. Historical cases, outcomes, and document roles, without claiming round identity.
SELECT x.case_id, x.experience_round_id, x.experience_round_label,
       x.outcome_status, x.outcome_basis, x.notes,
       o.evidence_id AS outcome_evidence_id, o.round_id AS outcome_source_round,
       o.quote AS outcome_quote, od.url AS outcome_url, od.capture_mode AS outcome_capture_mode,
       COALESCE(ds.documents, '[]'::jsonb) AS case_documents
FROM experience_case x
JOIN opportunity_round r USING (program_id)
LEFT JOIN evidence o ON o.evidence_id = x.outcome_evidence_id
LEFT JOIN source_document od ON od.document_id = o.document_id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'document_id', d.document_id, 'role', cd.document_role, 'finality', cd.finality,
        'url', d.url, 'capture_mode', d.capture_mode, 'binding_evidence_id', cd.binding_evidence_id
    ) ORDER BY d.document_id, cd.document_role) AS documents
    FROM case_document cd
    JOIN source_document d USING (document_id)
    WHERE cd.case_id = x.case_id
) ds ON true
WHERE r.opportunity_id = 'nvidia-2027-2028'
ORDER BY x.case_id;

-- Q3. Pattern → target prompt → case/official evidence. Scope travels with every row.
SELECT p.pattern_id, p.target_claim_id, c.field AS target_field, p.pattern_kind,
       p.scope_status, p.causal_success_claim, p.body, pe.purpose,
       pe.case_id, pe.case_document_role, x.experience_round_id, x.experience_round_label,
       x.outcome_status, x.outcome_basis, e.evidence_id, e.program_id AS evidence_program_id,
       e.round_id AS evidence_round_id, d.document_id, d.capture_mode, d.url,
       d.text_sha256, e.source_role, e.locator, e.quote
FROM writing_pattern p
JOIN opportunity_round r ON (r.program_id, r.round_id) = (p.program_id, p.target_round_id)
LEFT JOIN claim c ON c.claim_id = p.target_claim_id
JOIN pattern_evidence pe USING (pattern_id)
JOIN evidence e USING (evidence_id)
JOIN source_document d ON d.document_id = e.document_id
LEFT JOIN experience_case x ON x.case_id = pe.case_id
WHERE r.opportunity_id = 'nvidia-2027-2028'
ORDER BY p.pattern_id, pe.purpose, e.evidence_id;

-- Q4. Delivery checks beyond the foreign keys. Expected output: zero rows.
SELECT 'reviewed_claim_missing_evidence' AS violation, c.claim_id AS item_id
FROM claim c WHERE c.review_state = 'reviewed'
AND NOT EXISTS (SELECT 1 FROM claim_evidence ce WHERE ce.claim_id = c.claim_id)
UNION ALL
SELECT 'reviewed_claim_not_official', ce.claim_id
FROM claim_evidence ce JOIN claim c USING (claim_id) JOIN evidence e USING (evidence_id)
WHERE c.review_state = 'reviewed'
AND e.source_role NOT IN ('official_notice', 'official_guideline', 'official_form', 'application_portal')
UNION ALL
SELECT 'pattern_missing_grounding', p.pattern_id
FROM writing_pattern p WHERE NOT EXISTS (
    SELECT 1 FROM pattern_evidence pe WHERE pe.pattern_id = p.pattern_id
)
UNION ALL
SELECT 'pattern_target_not_submission', p.pattern_id
FROM writing_pattern p LEFT JOIN claim c ON c.claim_id = p.target_claim_id
WHERE c.kind IS DISTINCT FROM 'submission'
UNION ALL
SELECT 'pattern_source_round_not_preserved', pe.pattern_id
FROM pattern_evidence pe JOIN evidence e USING (evidence_id)
WHERE pe.source_round_id IS DISTINCT FROM e.round_id
UNION ALL
SELECT 'pattern_official_grounding_not_official', pe.pattern_id
FROM pattern_evidence pe JOIN evidence e USING (evidence_id)
WHERE pe.purpose IN ('official_constraint', 'official_guidance')
AND e.source_role NOT IN ('official_notice', 'official_guideline', 'official_form', 'application_portal')
UNION ALL
SELECT 'case_scope_mislabeled', pe.pattern_id
FROM pattern_evidence pe
JOIN writing_pattern p USING (pattern_id)
JOIN experience_case x USING (case_id)
WHERE (p.scope_status = 'same_round' AND
       (x.program_id IS DISTINCT FROM p.program_id OR x.experience_round_id IS DISTINCT FROM p.target_round_id))
   OR (p.scope_status = 'historical_transfer' AND
       (x.program_id IS DISTINCT FROM p.program_id OR x.experience_round_id = p.target_round_id))
   OR p.scope_status = 'official_only'
UNION ALL
SELECT 'case_grounding_role_mismatch', pe.pattern_id
FROM pattern_evidence pe JOIN evidence e USING (evidence_id)
WHERE pe.purpose = 'case_observation' AND pe.case_document_role <> e.source_role
ORDER BY 1, 2;
