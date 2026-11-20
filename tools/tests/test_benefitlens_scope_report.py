"""The exported report must use actual stage counts and retain explicit unknowns."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_benefitlens_team_handoff as team


def fixture():
    snapshot={'at':'2026-11-20T02:00:00+00:00','state':'processing','monitor_end':{
        'queue_counts':[{'kind':'notice','state':'analyzed','groups':12345},
                        {'kind':'experience','state':'analyzed','groups':6789},
                        {'kind':'experience','state':'codex_running','groups':400},
                        {'kind':'experience','state':'pending','groups':181993},
                        {'kind':'experience','state':'unresolved','groups':123}]}}
    payloads={
        'compact_handoff':{'counts_by_kind':{'notice':{'llm_interpreted_groups':12345,'relevant_groups':9999,'code_applied_occurrences':45678},
                                           'experience':{'llm_interpreted_groups':6789,'relevant_groups':4567,'code_applied_occurrences':12345}}},
        'condition_schema_links':{'counts':{'verified_relevant_mapping_instances':23456,'verified_linked_unique_candidate_notices':6789,'source_verified_question_route_links':4567}},
        'writing_pattern_links':{'counts':{'source_bound_occurrence_links':12345,'paragraph_linked_observations':4567,'paragraph_case_linked_observations':3456,
                                         'generic_criterion_linked_observations':2345,'source_verified_full_paragraph_analyses_materialized':20,
                                         'actual_pattern_source_occurrence_case_links':23,'actual_semantic_criterion_assessment_links':120},
                                 'paragraph_analysis':{'successful_full_paragraph_analyses':20,'active_successful_full_paragraph_analyses':20,
                                                       'criterion_assessment_links':120,'completion_identity':'verified-20-paragraphs'},
                                 'program_round_bound_patterns':0,'program_specific_approved_patterns':0,'content_approved_patterns':0},
        'personalization':{'counters':{'notice_projection':{'with_new_source_verified_mapping':6789}},'live_questions_issued':0,'personal_eligibility_decisions_issued':0},
        'experience_quality':{'source_documents':999999,'canonical_database_writes':0,'whole_document_latent_semantics_completed':0},
        'writing_registry':{'source_rows_routed':999999,'local_writing_observations':4567},'operations':{}}
    snapshot['stages']=[{'name':name,'state':'completed'} for name in payloads]
    snapshot['team_completion']=team.completion_summary(snapshot,payloads)
    return snapshot


class ScopeReportTests(unittest.TestCase):
    def test_report_renders_two_pages_from_actual_counts_without_inventory_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);snapshot=root/'snapshot.json';team.save(snapshot,fixture())
            command=[sys.executable,str(Path(team.__file__).parent/'build_benefitlens_scope_report.py'),
                     '--snapshot',str(snapshot),'--output-root',str(root/'report')]
            result=subprocess.run(command,text=True,capture_output=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr)
            summary=json.loads(result.stdout);self.assertEqual(summary['pages'],2)
            report=(root/'report/report.txt').read_text()
            for value in ('12,345','23,456','20','Stage 0','미집계'):self.assertIn(value,report)
            self.assertNotIn('999,999',report);self.assertNotIn('\u00b7',report)
            self.assertLess(len(report),10000)


if __name__=='__main__':unittest.main()
