import copy
import importlib.util
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_condition_contract import evaluate,validate_leaf
from benefitlens_end_to_end import SCHEMA,digest,read_projected_source,clean_fragment
from benefitlens_literal_templates import render_sentence,render_template
from benefitlens_bound_draft import BOUND_SCHEMA


def leaf():
    return {'field':'education.study_duration','namespace':'common','operator':'gte',
      'value':1,'value_type':'number','unit':'year','attributes':{'degree_scope':'doctorate'},
      'evidence':[{'document_id':'D','text_sha256':'hash','char_start':0,'char_end':1,'quote':'1'}],
      'subject':'applicant','role':'eligibility','reference_phase':'application','review_status':'reviewed'}


class Conditions(unittest.TestCase):
    def test_unknown_is_not_false(self):self.assertIsNone(evaluate(leaf(),{}))
    def test_unreviewed_cannot_decide(self):
        x=leaf();x['review_status']='unreviewed'
        self.assertIsNone(evaluate(x,{'education.study_duration':{'value':2,'unit':'year','attributes':x['attributes']}}))
    def test_study_duration_is_not_coursework(self):
        self.assertIsNone(evaluate(leaf(),{'education.coursework_completed':{'value':True}}))
    def test_or_survives_unknown(self):
        x=leaf();p={'education.study_duration':{'value':2,'unit':'year','attributes':x['attributes']}}
        missing=copy.deepcopy(x);missing['field']='experience.years'
        self.assertIs(evaluate({'any':[x,missing]},p),True)
        self.assertIsNone(evaluate({'all':[x,missing]},p))
    def test_units_do_not_silently_convert(self):
        x=leaf();self.assertIsNone(evaluate(x,{'education.study_duration':{'value':24,'unit':'month','attributes':x['attributes']}}))
    def test_gpa_scale_required(self):
        x=leaf();x['field']='education.gpa'
        with self.assertRaises(ValueError):validate_leaf(x)
    def test_specific_scope_required(self):
        x=leaf();x['namespace']='specific'
        with self.assertRaises(ValueError):validate_leaf(x)
    def test_benefit_is_not_eligibility(self):
        x=leaf();x['role']='benefit';self.assertIsNone(evaluate(x,{}))


class Storage(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.execute('PRAGMA foreign_keys=ON');self.c.executescript(SCHEMA)
        self.c.executescript(BOUND_SCHEMA)
        self.c.execute("INSERT INTO draft VALUES('draft','target','person','hash','v1','{}','call')")
    def test_public_fact_rejected_by_database(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO applicant_fact VALUES('fact','person','completed','public_source','public achievement')")
    def test_other_subject_cannot_fill_draft(self):
        self.c.execute("INSERT INTO applicant_fact VALUES('fact','other','completed','user_supplied','own achievement')")
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("INSERT INTO draft_fact_binding VALUES('draft',0,'fact')")
    def test_no_fact_id_cannot_fill_draft(self):
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("INSERT INTO draft_fact_binding VALUES('draft',0,'missing')")
    def test_same_subject_fact_allowed(self):
        self.c.execute("INSERT INTO applicant_fact VALUES('fact','person','planned','user_supplied','plan')")
        self.c.execute("INSERT INTO draft_fact_binding VALUES('draft',0,'fact')")
        self.assertEqual(self.c.execute('SELECT count(*) FROM draft_fact_binding').fetchone()[0],1)
    def test_completed_fact_cannot_fill_planned_sentence_slot(self):
        self.c.execute("INSERT INTO applicant_fact VALUES('f3','person','completed','synthetic_fixture','built a structure')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO draft_sentence_binding VALUES('draft',0,'opening','goal','f3','planned')")
    def test_benefit_cannot_be_inserted_as_eligibility(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError,'benefit_or_submission'):
            self.c.execute("INSERT INTO normalized_condition VALUES('n','d','notice.amount','common','eligibility','number','500','USD','{}','span',0)")


class RetrievalQuality(unittest.TestCase):
    def test_navigation_not_writing_evidence(self):
        text='Overview of Role\nSkills Development\nCompany Culture\nOverall Experience'
        self.assertEqual(clean_fragment(text)[1],'navigation_or_table_of_contents')
    def test_one_word_reaction_not_writing_evidence(self):
        self.assertIsNone(clean_fragment('Nice!')[0])
    def test_negative_experience_is_preserved(self):
        text='I did not receive a return offer, but I learned to document decisions and ask for specific feedback before the final review.'
        self.assertEqual(clean_fragment(text)[0],text)
    def test_empty_form_and_interface_excluded(self):
        for text in ['Host Region: ___________','No results found for this query, search code, repositories and users']:
            self.assertIsNone(clean_fragment(text)[0])


class LiteralTemplate(unittest.TestCase):
    def test_particle_with_and_without_final_consonant(self):
        self.assertEqual(render_sentence('{{x}}을 수행했습니다.',{'x':{'value':'검증'}}),'검증을 수행했습니다.')
        self.assertEqual(render_sentence('{{x}}을 수행했습니다.',{'x':{'value':'비교'}}),'비교를 수행했습니다.')
    def test_official_source_cannot_fill_own_action(self):
        t={'slot_contract':{'action':{'provenance':['applicant_record'],'tense':'completed'}},'paragraphs':[]}
        with self.assertRaises(ValueError):render_template(t,{'action':{'value':'수상','provenance':'institution_public','status':'completed'}})
    def test_planned_action_cannot_fill_completed_slot(self):
        t={'slot_contract':{'action':{'provenance':['applicant_record'],'tense':'completed'}},'paragraphs':[]}
        with self.assertRaises(ValueError):render_template(t,{'action':{'value':'검증','provenance':'applicant_record','status':'planned'}})
    def test_empty_optional_sentence_is_dropped(self):
        t={'slot_contract':{},'paragraphs':[{'opening':'첫 문장.','support':'근거.','closing':'마침.',
          'optional_sentences':[{'text':'{{resource}}을 사용합니다.','requires':['resource']}]}]}
        self.assertEqual(render_template(t,{}),['첫 문장. 근거. 마침.'])


if __name__=='__main__':unittest.main()
