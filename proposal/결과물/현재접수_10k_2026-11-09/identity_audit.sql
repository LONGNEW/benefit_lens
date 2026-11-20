SELECT
 (SELECT count(*) FROM opportunity_round) AS round_rows,
 (SELECT count(DISTINCT (program_id,round_id)) FROM opportunity_round) AS distinct_round_keys,
 (SELECT count(*) FROM opportunity_round WHERE status='open') AS open_rows,
 (SELECT count(DISTINCT checked_on) FROM opportunity_round) AS distinct_check_dates,
 (SELECT count(*) FROM claim WHERE field='student_scope') AS student_scope_claims,
 (SELECT count(DISTINCT (value->>'source_id',value->>'native_id')) FROM claim
   WHERE field='student_scope' AND value->>'source_id' IS NOT NULL AND value->>'native_id' IS NOT NULL) AS distinct_provider_native,
 (SELECT count(DISTINCT value->>'source_id') FROM claim WHERE field='student_scope') AS distinct_providers,
 (SELECT count(*) FROM claim WHERE field='student_scope' AND value->>'student_scope_verified'='true') AS student_scope_verified_rows,
 (SELECT count(*) FROM claim WHERE field='student_scope' AND value->>'full_eligibility_verified'='true') AS full_eligibility_verified_rows,
 (SELECT count(*) FROM experience_case) AS experience_cases,
 (SELECT count(*) FROM writing_pattern) AS writing_patterns;
