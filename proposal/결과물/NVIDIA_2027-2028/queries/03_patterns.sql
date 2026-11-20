SELECT jsonb_agg(jsonb_build_object('id',wp.pattern_id,'kind',wp.pattern_kind,'scope',wp.scope_status,
    'round',wp.target_round_id,'claim',wp.target_claim_id,'guidance',wp.body,
    'causal_success_claim',wp.causal_success_claim,
    'evidence',(SELECT jsonb_agg(jsonb_build_object('evidence',e.evidence_id,
      'case',pe.case_id,'purpose',pe.purpose,'document',e.document_id,'locator',e.locator,'url',d.url)
      ORDER BY pe.purpose,pe.case_id,e.evidence_id)
      FROM pattern_evidence pe JOIN evidence e USING(evidence_id) JOIN source_document d ON d.document_id=e.document_id
      WHERE pe.pattern_id=wp.pattern_id))
  ORDER BY wp.pattern_id)
FROM writing_pattern wp WHERE wp.target_round_id='2027-2028';
