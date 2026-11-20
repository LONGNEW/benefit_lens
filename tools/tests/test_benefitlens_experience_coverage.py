import copy
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_experience_coverage import coverage_report, POLICY

POLICY_DATA=json.loads(POLICY.read_text())


def case(key,program='p',name=None,cycle=None,**payload):
    return {'case_key':key,'program_id':program,'person_name':name,
            'application_round':cycle,'evidence_status':'public_original_collected',
            'payload':{'active':True,'document_ids':['d'],'case_scope':'application_process',**payload}}


def test_many_documents_and_known_person_alias_records_are_not_many_episodes():
    records=[case('one',name='Alice Smith',cycle='2025',document_ids=['d1','d2','d3']),
             case('two',name='Alice-Smith',cycle='2025'),case('three',name='Alice Smith',cycle='2026')]
    r=coverage_report([],records,10,2,POLICY_DATA)
    assert r['counted_experience_episodes']==2
    assert r['excluded']['same_person_program_round']==1


def test_missing_round_merges_conservatively_without_fabricating_round():
    r=coverage_report([],[case('a',name='Alice'),case('b',name='Alice',cycle='2025')],1,0,POLICY_DATA)
    assert r['counted_experience_episodes']==1


def test_global_volume_cannot_replace_program_linked_coverage():
    policy=copy.deepcopy(POLICY_DATA);policy['minimum_total_experiences']=0
    r=coverage_report([{'opportunity_id':'n','program_id':'p'}],
                      [case(str(i),program='unrelated') for i in range(6)],2,0,policy)
    assert r['global_ratio_met'] is True
    assert r['registered_notice_coverage'][0]['missing']==3
    assert r['per_notice_coverage_met'] is False and r['completion_met'] is False


def test_unresolved_attribution_and_duplicates_and_guides_do_not_fill_ratio():
    records=[case('good',text_key='same'),case('copy',text_key='same'),
             case('unresolved',quality_audit={'unresolved_document_ids':['d']}),
             case('guide',case_scope='reference_material'),case('absent',document_ids=[])]
    r=coverage_report([],records,36856,0,POLICY_DATA)
    assert r['target_experience_episodes']==110568
    assert r['counted_experience_episodes']==1
    assert r['current_open_verified_discovery_records']==0
    assert r['planning_notice_candidates']==36856


def test_same_program_historical_episodes_count_but_are_marked_for_prompt_review():
    r=coverage_report([{'opportunity_id':'n','program_id':'fulbright-korea-gsp'}],
        [case(str(i),'fulbright-graduate-student-program',name='Person '+str(i),cycle='2021') for i in range(3)],
        1,0,POLICY_DATA)
    row=r['registered_notice_coverage'][0]
    assert row['experience_count']==3 and row['missing']==0
    assert 'historical' in row['round_scope']
