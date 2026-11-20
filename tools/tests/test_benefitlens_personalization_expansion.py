from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_personalization import route_mapping,document_materials,question_projection,rebound_representative,CONFIG,REPO


def mapping(**changes):
    result={'field_id':'education.status','subject':'applicant','modality':'required',
        'statement_type':'eligibility_requirement','reference_phase':'application','operator':'eq',
        'value_extraction_method':'literal_text','value_quote':'enrolled','evidence_quote':'You must be enrolled.',
        'logic_cues':[],'ambiguities':[],'unit':None,'program_specific_key':None}
    result.update(changes)
    return result


class PersonalizationExpansionTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads(CONFIG.read_text())
        self.catalog=json.loads((REPO/self.config['catalog']).read_text())

    def test_institution_faculty_count_is_not_student_basic_input(self):
        m=mapping(field_id='program_specific.requirement',subject='institution',program_specific_key='faculty_count')
        q=question_projection(m,'m',self.catalog,self.config)
        self.assertEqual(q['route'],'external_or_program_information')
        self.assertEqual(q['recipient'],'institution')
        self.assertIsNone(q['proposed_question'])
        self.assertFalse(q['required_user_input'])

    def test_preference_does_not_become_required_eligibility(self):
        m=mapping(modality='preferred',statement_type='selection_preference')
        self.assertEqual(route_mapping(m,self.catalog),'optional_followup')
        q=question_projection(m,'m',self.catalog,self.config)
        self.assertEqual(q['modality'],'preferred')
        self.assertFalse(q['required_user_input'])

    def test_activity_duration_is_not_replaced_by_current_enrolment(self):
        q=question_projection(mapping(reference_phase='throughout_activity'),'m',self.catalog,self.config)
        self.assertIn('기간 전체',q['proposed_question'])
        self.assertEqual(q['reference_phase'],'throughout_activity')
        self.assertFalse(q['live_question_issued'])

    def test_start_date_age_stays_unknown_without_calendar_start(self):
        q=question_projection(mapping(field_id='person.age',reference_phase='benefit_start',value_quote='18 years'),'m',self.catalog,self.config)
        self.assertIn('exact_program_start_date_needed_for_age',q['blocked_reasons'])
        self.assertEqual(q['question_state'],'source_clarification_required')
        self.assertEqual(q['eligibility_decision'],'unknown')

    def test_official_transcript_is_not_student_authored_drafting(self):
        m=mapping(field_id='application.document_requirement',statement_type='submission_requirement',value_quote='resume and academic transcript in PDF')
        documents=document_materials(m,self.config)
        by_kind={d['document_kind']:d for d in documents}
        self.assertTrue(by_kind['resume']['student_authored_draft'])
        self.assertFalse(by_kind['academic_transcript']['student_authored_draft'])
        self.assertEqual(by_kind['academic_transcript']['authorship_candidate'],'institution')
        self.assertEqual(by_kind['academic_transcript']['submission_subject'],'applicant')

    def test_transcript_or_degree_audit_is_not_silently_converted_to_all_required(self):
        m=mapping(field_id='application.document_requirement',statement_type='submission_requirement',
            value_quote='resume and academic transcript/degree audit in PDF',
            ambiguities=['대체 관계가 불명확하다.'])
        docs=document_materials(m,self.config)
        self.assertEqual({d['document_kind'] for d in docs},{'resume','academic_transcript','degree_audit'})
        self.assertTrue(all(d['individual_document_required'] is None for d in docs))
        self.assertTrue(all(d['bundle_ambiguities']==m['ambiguities'] for d in docs))

    def test_referee_material_is_not_an_applicant_writing_fact(self):
        m=mapping(field_id='application.document_requirement',subject='referee',statement_type='submission_requirement',value_quote='recommendation letter')
        self.assertEqual(route_mapping(m,self.catalog),'external_or_program_information')
        d=document_materials(m,self.config)[0]
        self.assertEqual(d['authorship_candidate'],'referee')
        self.assertFalse(d['student_authored_draft'])

    def test_institution_proposal_is_not_rerouted_as_student_draft(self):
        m=mapping(field_id='application.document_requirement',subject='institution',
                  statement_type='submission_requirement',value_quote='research proposal')
        d=document_materials(m,self.config)[0]
        self.assertEqual(d['authorship_candidate'],'institution')
        self.assertFalse(d['student_authored_draft'])
        self.assertFalse(d['confirmed_applicant_facts_required'])
        self.assertEqual(d['route'],'external_document_collection')

    def test_benefit_and_description_do_not_become_student_questions(self):
        for m in (mapping(field_id='benefit.amount',subject='program',modality='informational',statement_type='benefit_description'),
                  mapping(subject='program',modality='informational',statement_type='description')):
            self.assertIsNone(question_projection(m,'m',self.catalog,self.config)['proposed_question'])

    def test_reused_heading_is_relocated_in_actual_target_document(self):
        text='A different preface.\nRequired qualifications\nYou must be enrolled.'
        a=text.index('You must');ca=text.index('Required')
        rep={'text':'You must be enrolled.','context_text':'Required qualifications\nYou must be enrolled.',
            'char_start':24,'char_end':45,'context_start':0,'context_end':45,
            'heading':{'text':'Required qualifications','char_start':0,'char_end':23}}
        occurrence={'char_start':a,'char_end':len(text),'context_start':ca,'context_end':len(text),
                    'document_id':'target','text_sha256':'sha','source_url':'https://example.test'}
        result=rebound_representative(occurrence,rep,text)
        self.assertEqual(result['heading']['char_start'],ca)
        self.assertNotEqual(result['heading']['char_start'],rep['heading']['char_start'])

    def test_new_catalog_field_is_read_without_separate_field_list(self):
        catalog=deepcopy(self.catalog);catalog['fields']['future.field']={'applicant_input_stage':'common_profile'}
        self.assertEqual(route_mapping(mapping(field_id='future.field'),catalog),'common_profile')


if __name__=='__main__':unittest.main()
