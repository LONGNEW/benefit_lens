SELECT jsonb_build_object(
  'program', to_jsonb(p), 'round', to_jsonb(r),
  'claims', (
    SELECT jsonb_agg(jsonb_build_object('id',c.claim_id,'kind',c.kind,'field',c.field,'at',c.at,
      'value',c.value,'state',c.review_state,
      'evidence', (SELECT jsonb_agg(jsonb_build_object('id',e.evidence_id,'document',e.document_id,
        'locator',e.locator,'role',e.source_role) ORDER BY e.evidence_id)
        FROM claim_evidence ce JOIN evidence e USING(evidence_id) WHERE ce.claim_id=c.claim_id))
        ORDER BY c.kind,c.field)
    FROM claim c WHERE (c.program_id,c.round_id)=(r.program_id,r.round_id)))
FROM program p JOIN opportunity_round r USING(program_id)
WHERE r.opportunity_id='nvidia-2027-2028';
