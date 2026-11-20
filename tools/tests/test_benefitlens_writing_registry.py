from copy import deepcopy
from hashlib import sha256
import json
import gzip
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_writing_registry import (
    classify_source,pattern_entry,legacy_template_entry,outcome_boundary,load_intents,build_registry,intent_material_links)


def source_row(**kwargs):
    row={'record_id':'source-1','canonical_id':'canonical-1','family':'test','unit':'source_document',
        'source_form':'community_answer','evidence_kind':'community_advice',
        'target_genres':json.dumps(['application']),'review_state':'auto_source_role_classified',
        'body_sha256':sha256(b'fixture').hexdigest()}
    row.update(kwargs);return row


def fragment():
    return {'group_id':'g1','approved_pattern':False,'quality_score':None,'occurrence_ids':['o1'],
        'execution_ref':{'provider':'synthetic_fixture_only','receipt_path':'fixture'},
        'observed_structure':{'event_roles':['personal_role','action','result'], 'actor':'self',
          'writing_move':'choice to reported result','material_role':'reported project',
          'claim_evidence_reasoning':{'claim':'contribution','evidence':'artifact','reasoning':None},
          'opening_or_closing_function':{'opening':'state responsibility','closing':None},
          'local_connection':{'kind':'not_observed','description':None},'limits':['local passage']}}


def test_model_predicted_labels_are_not_human_normative_or_improvement_gold():
    row=source_row(unit='label_observation',evidence_kind='model_predicted_revision_intent')
    result=classify_source(row)
    assert result['route_id']=='predicted_revision_label'
    assert 'not_human_edit_intent' in result['limitations']
    assert 'improvement_unverified' in result['limitations']
    intent=classify_source(source_row(unit='label_observation',evidence_kind='human_revision_intent'))
    reference=classify_source(source_row(unit='label_observation',evidence_kind='human_revision_reference'))
    assert intent['route_id']!=reference['route_id']


def test_fiction_and_unknown_genre_do_not_silently_become_application_evidence():
    route=classify_source(source_row(target_genres=json.dumps(['literary_fiction','undetermined'])))
    assert 'fiction_to_application_transfer_requires_separate_review' in route['limitations']
    assert 'genre_unresolved' in route['limitations']


def test_original_scores_and_revisions_keep_distinct_evaluation_uses():
    score=classify_source(source_row(unit='essay',evidence_kind='human_scored_observation'))
    pair=classify_source(source_row(unit='revision_pair',evidence_kind='revision_pair'))
    assert score['route_id']=='original_scale_evaluation_reference'
    assert 'not_application_admission_score' in score['limitations']
    assert pair['route_id']=='before_after_pair' and 'improvement_unverified' in pair['limitations']


def test_writing_observation_requests_own_facts_and_does_not_invent_prompt_intent_or_paragraph_links():
    observed=fragment();before=deepcopy(observed)
    p=pattern_entry(observed,{'o1':{'document_id':'doc1','genre':'application',
        'source_role':'public_application','paragraph_position':'unknown'}})
    assert observed==before and not p['approved_pattern']
    assert p['intent']['axes']==[] and p['intent']['status']=='needs_current_task_binding'
    assert all(s['value'] is None and s['status']=='need_info' and not s['source_person_fact_transfer_allowed'] for s in p['personal_fact_slots'])
    assert not p['paragraph_contract']['whole_paragraph_boundary_verified']
    assert p['paragraph_contract']['previous_paragraph_id'] is p['paragraph_contract']['next_paragraph_id'] is None
    assert not p['outcome_comparison']['selection_effect_established']


def test_reported_question_context_is_not_forced_into_a_personal_story():
    f=fragment();f['observed_structure'].update(event_roles=['application_process'],actor='other')
    p=pattern_entry(f,{})
    assert p['reuse_mode']=='reported_prompt_context_only' and p['personal_fact_slots']==[]


def test_intent_material_bridge_preserves_missing_program_evidence_and_never_grants_drafting():
    p=pattern_entry(fragment(),{})
    intent={'registry_id':'intent:q1','task_id':'q1','fact_only_no_essay':False,'answer_use':'selection',
        'evidence_demands':[{'role':'initiative'},{'role':'program_resource_link'}]}
    link=intent_material_links([intent],[p])[0]
    assert link['partially_matched_demand_roles']==['initiative'] and not link['demand_coverage_verified']
    assert link['unmatched_demand_roles']==['program_resource_link']
    assert not link['applicant_facts_transferred'] and not link['draft_permission_granted']
    assert 'not_task_equivalence' in link['status']


def test_statistical_or_fact_only_question_does_not_acquire_an_essay_from_similar_material_roles():
    p=pattern_entry(fragment(),{})
    intent={'registry_id':'intent:q1','task_id':'q1','fact_only_no_essay':True,'answer_use':'data_collection',
        'evidence_demands':[{'role':'initiative'}]}
    assert intent_material_links([intent],[p])==[]


@pytest.mark.parametrize('key,value',[('approved_pattern',True),('quality_score',2)])
def test_unperformed_approval_or_score_is_rejected(key,value):
    f=fragment();f[key]=value
    with pytest.raises(ValueError,match='Unperformed'):
        pattern_entry(f,{})


def test_real_legacy_template_exposes_handoff_slots_without_filling_foreign_facts():
    repo=Path(__file__).resolve().parents[2]
    template=json.loads((repo/'pipeline/config/paragraph_writing_templates_v1.json').read_text())['patterns']['experience_evidence']
    p=legacy_template_entry('experience_evidence',template)
    assert p['paragraphs'][0]['handoff_slot']=='chosen_change'
    assert p['paragraphs'][1]['previous_paragraph_id']=='p1'
    assert p['paragraphs'][1]['next_paragraph_id'] is None
    assert all(s['value'] is None for s in p['personal_fact_slots'])


def test_selected_outcome_with_official_basis_is_not_automatically_verified_or_causal():
    case={'case_key':'case1','source_outcome':{'claim':'selected','basis':'official_university_recipient_list'}}
    boundary=outcome_boundary(case)
    assert not boundary['explicit_outcome_verification_refs_present']
    assert boundary['outcome_use']=='context_not_causal_writing_quality_gold'


def test_intent_source_hash_and_exact_span_are_checked(tmp_path):
    folder=tmp_path/'run';folder.mkdir();text='How did you hear?'
    (folder/'source-bundle.json').write_text(json.dumps({'documents':[{'document_id':'d1','text':text}]}))
    row={'task_id':'q1','question_text':text,'question_kind':'public_question','reader_question':'discovery',
        'purpose_status':'unclear','answer_use':'unspecified','axes':[],'evidence_demands':[],
        'answer_strategy':{'status':'not_needed'},'source_role':'public_application_material',
        'source_url':'https://example.test','semantic_review':'unreviewed',
        'source_evidence':[{'document_id':'d1','text_sha256':sha256(text.encode()).hexdigest(),
                            'char_start':0,'char_end':len(text),'exact_quote':text}]}
    p=folder/'reviewed-observations.json';p.write_text(json.dumps({'observations':[row]}))
    assert load_intents(tmp_path)[0]['fact_only_no_essay']
    row['source_evidence'][0]['char_end']-=1;p.write_text(json.dumps({'observations':[row]}))
    with pytest.raises(ValueError,match='span mismatch'):load_intents(tmp_path)


def test_full_source_routing_keeps_units_and_duplicate_canonical_counts_separate(tmp_path):
    rows=[source_row(),source_row(record_id='source-2'),source_row(record_id='essay-1',canonical_id='essay-c',
        family='persuade',unit='essay',source_form='human_scored_student_essay',evidence_kind='human_scored_observation')]
    source=tmp_path/'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence_record('+','.join(k+' TEXT' for k in rows[0])+')')
        db.executemany('INSERT INTO evidence_record VALUES('+','.join('?' for _ in rows[0])+')',[tuple(r.values()) for r in rows])
    summary=build_registry(source,tmp_path/'out',writing_root=tmp_path/'missing',
        handoff_root=tmp_path/'missing',cases_path=tmp_path/'missing',intent_root=tmp_path/'missing')
    assert summary['source_rows_routed']==3 and not summary['source_semantic_review_of_all_rows_performed']
    community=next(r for r in summary['support_distribution_by_family_and_unit'] if r['family']=='test')
    assert community['source_rows']==2 and community['canonical_records']==1
    assert summary['quality_scores_assigned']==summary['filled_applicant_fact_slots']==0
    with sqlite3.connect(tmp_path/'out/registry.sqlite3') as db:
        entries={r[0]:json.loads(r[1]) for r in db.execute('SELECT registry_id,spec_json FROM registry')}
        for row in entries.values():
            for reference in row.get('source_stage_registry_ids',[]):assert reference in entries
    again=build_registry(source,tmp_path/'out',writing_root=tmp_path/'missing',
        handoff_root=tmp_path/'missing',cases_path=tmp_path/'missing',intent_root=tmp_path/'missing')
    assert again['static_source_routes_reused'] and again['source_route_rows_processed_this_run']==0
    assert again['source_rows_routed']==3
    experience=tmp_path/'new-experience';experience.mkdir()
    with gzip.open(experience/'cases.jsonl.gz','wt') as f:
        f.write(json.dumps({'case_key':'new-case','input_documents':[{'document_id':'d1'}]})+'\n')
    with gzip.open(experience/'documents.jsonl.gz','wt') as f:
        f.write(json.dumps({'document_id':'d1','text_ref':{'path':'fixture','sha256':'fixture'},
            'segment_count':2,'semantic_review':'unreviewed'})+'\n')
    refreshed=build_registry(source,tmp_path/'out',writing_root=tmp_path/'missing',handoff_root=tmp_path/'missing',
        experience_root=experience,intent_root=tmp_path/'missing')
    assert refreshed['case_boundaries_processed']==refreshed['production_document_routes']==1
    assert refreshed['structural_units_referenced_not_semantically_completed']==2
    assert refreshed['static_source_routes_reused'] and refreshed['source_map_fingerprint_reused']
