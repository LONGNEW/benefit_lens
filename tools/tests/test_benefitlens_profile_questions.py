from copy import deepcopy
import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_profile_questions import load_contract,initial_form,question_plan
from run_benefitlens_requirement_types import validate_group


def record(mid='one',notice='n1',**changes):
    m={'field_id':'education.gpa','subject':'applicant','statement_type':'eligibility_requirement','modality':'required','operator':'gte',
        'value_quote':'3.0','unit':'gpa_point','reference_phase':'application','ambiguities':[],'logic_cues':[]}
    m.update(changes)
    return {'mapping_id':mid,'candidate_key':notice,'mapping':m,'semantic_review':'approved','bindings':[{'exact_quote':'GPA 3.0'}]}


class ProfileQuestionsTests(unittest.TestCase):
    def test_initial_is_six_values_in_two_chunks_and_no_sensitive_mandatory_fields(self):
        c=load_contract();form=initial_form(c)
        self.assertEqual(len(form),2);self.assertEqual(sum(len(g['fields']) for g in form),6)
        fields=[f for g in form for f in g['fields']]
        self.assertTrue(all(not f['mandatory'] for f in fields))
        self.assertFalse({'person.age','person.nationalities','education.gpa','work.authorization'}&{f['field_id'] for f in fields})
        self.assertEqual(sum(f['kind']=='search_preference' for f in fields),2)
        self.assertLessEqual(len(c['condition_types']),100)
        self.assertEqual(len(c['user_fact_field_ids']),14)
        self.assertEqual(len(c['input_field_types']),35)

    def test_contract_rejects_more_than_a_hundred_condition_types(self):
        c=load_contract();c['condition_types']={str(i):{} for i in range(101)}
        with tempfile.TemporaryDirectory() as f:
            p=Path(f)/'bad.json';p.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'cap'):load_contract(p)

    def test_contract_rejects_unbounded_input_fields(self):
        c=load_contract();c['input_field_types']=[str(i) for i in range(101)]
        with tempfile.TemporaryDirectory() as f:
            p=Path(f)/'bad.json';p.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'cap'):load_contract(p)

    def test_same_profile_fact_is_asked_once_across_different_thresholds_without_rewriting(self):
        rows=[record(),record('two','n2',value_quote='3.5')];original=deepcopy(rows)
        p=question_plan(rows,selected_notices=['n1','n2'])
        self.assertEqual(p['unique_followup_questions'],1)
        q=p['selected_notice_followups'][0]['questions'][0]
        self.assertEqual(len(q['source_conditions']),2)
        self.assertEqual([r['mapping']['value_quote'] for r in q['source_conditions']],['3.0','3.5'])
        self.assertEqual(rows,original);self.assertFalse(p['condition_logic_collapsed'])

    def test_time_units_and_subject_are_preserved(self):
        rows=[record(),record('two','n2',unit='percent'),record('three','n3',reference_phase='benefit_start'),record('four','n4',reference_phase='benefit_start')]
        p=question_plan(rows,selected_notices=['n1','n2','n3','n4'])
        self.assertEqual(p['unique_followup_questions'],4)
        p=question_plan([record(subject='institution')],selected_notices=['n1'])
        self.assertEqual(p['unique_followup_questions'],0)

    def test_unreviewed_and_unknown_special_conditions_do_not_generate_generic_questions(self):
        r=record();r['semantic_review']='unreviewed'
        p=question_plan([r,record('special','n1',field_id='program_specific.requirement',program_specific_key='some_rule')],selected_notices=['n1'])
        self.assertEqual(p['unique_followup_questions'],0);self.assertEqual(len(p['held_conditions']),2)
        self.assertEqual(p['live_questions_issued'],0)

    def test_benefits_and_job_duties_are_not_user_questions(self):
        rows=[record(field_id='benefit.amount'),record('duty','n1',field_id='program_specific.requirement',program_specific_key='clinic_staffing')]
        lookup={('clinic_staffing','applicant','eligibility_requirement'):'activity.duties'}
        p=question_plan(rows,selected_notices=['n1'],type_lookup=lookup)
        self.assertEqual(p['unique_followup_questions'],0);self.assertEqual(p['non_applicant_information'],2)

    def test_confirmed_facts_only_reused_for_same_person_date_and_unit(self):
        fact={'field_id':'education.gpa','subject':'applicant','subject_id':'student-a','reference_phase':'application','time_key':'current_application_snapshot','unit':'gpa_point','confirmed':True,'value':3.8}
        self.assertEqual(question_plan([record()],selected_notices=['n1'],known_facts=[fact],subject_id='student-a')['unique_followup_questions'],0)
        self.assertEqual(question_plan([record()],selected_notices=['n1'],known_facts=[fact],subject_id='student-b')['unique_followup_questions'],1)
        fact['value']=None
        self.assertEqual(question_plan([record()],selected_notices=['n1'],known_facts=[fact],subject_id='student-a')['unique_followup_questions'],1)
        with self.assertRaises(ValueError):question_plan([record()],known_facts=[fact])

    def test_optional_stays_optional_and_unselected_notices_never_add_followups(self):
        self.assertEqual(question_plan([record()],selected_notices=[])['unique_followup_questions'],0)
        p=question_plan([record(modality='preferred',statement_type='selection_preference')],selected_notices=['n1'])
        q=p['selected_notice_followups'][0]['questions'][0];self.assertTrue(q['optional_only']);self.assertFalse(q['required'])

    def test_earned_and_pursuing_degree_are_not_silently_equated(self):
        p=question_plan([record(field_id='education.degree')],selected_notices=['n1'])
        self.assertEqual(p['unique_followup_questions'],0)
        self.assertIn('degree_earned_vs_pursuing_unknown',p['held_conditions'][0]['reasons'])
        a=record(field_id='education.degree');a['fact_basis']='earned'
        b=record('b','n2',field_id='education.degree');b['fact_basis']='currently_pursuing'
        p=question_plan([a,b],selected_notices=['n1','n2'])
        self.assertEqual(p['unique_followup_questions'],2)

    def test_earned_degree_does_not_hide_current_degree_initial_question(self):
        fact={'field_id':'education.degree','subject_id':'student','confirmed':True,'value':'bachelor','fact_basis':'earned'}
        p=question_plan([],known_facts=[fact],subject_id='student')
        fields=[f['field_id'] for g in p['initial_form'] for f in g['fields']]
        self.assertIn('education.degree',fields)

    def test_semantic_type_result_must_quote_a_real_example_and_fixed_type(self):
        task={'group_id':'g','examples':[{'quote':'The employer provides insurance and retirement savings.'}]}
        r={'group_id':'g','type_id':'notice.benefits','reason':'기관이 제공하는 혜택','evidence_quote':'insurance and retirement savings','limits':[]}
        self.assertEqual(validate_group(r,task,load_contract())['type_id'],'notice.benefits')
        r['evidence_quote']='student income'
        with self.assertRaises(ValueError):validate_group(r,task,load_contract())
        r['type_id']='new invented field'
        with self.assertRaises(Exception):validate_group(r,task,load_contract())

if __name__=='__main__':unittest.main()
