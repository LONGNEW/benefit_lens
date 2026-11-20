SELECT jsonb_agg(jsonb_build_object('case', ec.case_id, 'round_label', ec.experience_round_label,
    'outcome', ec.outcome_status, 'basis', ec.outcome_basis, 'notes', ec.notes,
    'outcome_evidence', (SELECT jsonb_build_object('document',e.document_id,'locator',e.locator,'url',d.url)
      FROM evidence e JOIN source_document d USING(document_id) WHERE e.evidence_id=ec.outcome_evidence_id),
    'documents', (SELECT jsonb_agg(jsonb_build_object('document', d.document_id, 'url',d.url,
      'role',cd.document_role,'finality',cd.finality,'capture',d.capture_mode) ORDER BY cd.document_role,d.document_id)
      FROM case_document cd JOIN source_document d USING(document_id) WHERE cd.case_id=ec.case_id))
  ORDER BY ec.case_id)
FROM experience_case ec WHERE ec.program_id='nvidia-graduate-fellowship';
