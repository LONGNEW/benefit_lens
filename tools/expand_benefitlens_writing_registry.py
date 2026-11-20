"""Build a source-linked writing rule/pattern registry without model calls.

Every preserved evidence-map row receives a capability route, not a semantic
quality score. Actual intent observations, compact writing passages and analyst
templates retain their own evidence and completion boundaries.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import gzip
import json
from pathlib import Path
import re
import shutil
import sqlite3

REPO=Path(__file__).resolve().parents[1]
CACHE=Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109')
BASE=CACHE/'team-production-20261119'
DEFAULT_OUTPUT=BASE/'team-expansion-20261120/writing-validation'


def load(path,default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def file_ref(path):
    path=Path(path);h=sha256()
    with path.open('rb') as f:
        for raw in iter(lambda:f.read(1024*1024),b''):h.update(raw)
    stat=path.stat()
    return {'path':str(path),'sha256':h.hexdigest(),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,'inode':stat.st_ino}


def jsonlines(path):
    if not Path(path).exists():return
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def classify_source(row):
    """Route evidence affordances; never infer quality or success from counts."""
    kind,unit,form=row['evidence_kind'],row['unit'],row['source_form']
    genres=json.loads(row['target_genres'])
    limits=['not_current_applicant_facts','not_causal_selection_gold']
    if kind=='model_predicted_revision_intent':
        route='predicted_revision_label';cap=['model_annotation_candidate'];limits+=['not_human_edit_intent','improvement_unverified']
    elif kind in ('human_revision_intent','human_revision_reference'):
        route='human_edit_intent_reference' if kind=='human_revision_intent' else 'human_revision_reference'
        cap=['revision_context_comparison'];limits+=['intent_or_reference_not_quality_improvement']
    elif unit=='revision_pair':
        route='before_after_pair';cap=['revision_pair_review'];limits+=['improvement_unverified','genre_context_required']
    elif kind=='human_scored_observation':
        route='original_scale_evaluation_reference';cap=['original_human_score_or_discourse_reference']
        limits+=['school_or_language_learner_domain','not_application_admission_score','prior_examined_test_is_development']
    elif kind in ('scholarly_recommendation','scholarly_recommendation_candidate'):
        route='normative_source_candidate';cap=['genre_conditional_rule_review'];limits+=['recommendation_not_experimental_effect']
    elif kind in ('observational_research','experimental_effect'):
        route='bounded_research_finding';cap=['study_context_and_effect_boundary'];limits+=['retain_original_design_population_and_outcome']
    elif kind.startswith('official_requirement'):
        route='official_task_candidate';cap=['prompt_and_guidance_binding'];limits+=['current_program_round_not_verified_here']
    elif kind=='example_text':
        route='writing_observation_candidate';cap=['source_passage_structure_review'];limits+=['submission_and_outcome_not_assumed']
    elif kind=='community_advice':
        route='community_context_candidate';cap=['contextual_advice_review'];limits+=['authority_and_applicability_unreviewed']
    elif form=='scoring_rubric':
        route='original_rubric_reference';cap=['original_evaluation_definition'];limits+=['new_rubric_not_calibrated']
    else:
        route='lookup_or_unresolved_source';cap=['source_lookup_only'];limits+=['not_pattern_evidence_without_content_review']
    if 'literary_fiction' in genres:
        limits+=['fiction_to_application_transfer_requires_separate_review']
        route+=':fiction_transfer_hold'
    if set(genres)&{'undetermined','genre_requires_source_context'}:
        limits+=['genre_unresolved']
        route+=':genre_unresolved'
    return {'route_id':route,'capabilities':cap,'limitations':limits}


SLOT_BY_ROLE={
 'motivation':['own_motivation','goal_or_need'], 'situation':['episode_context','constraint'],
 'personal_role':['own_role'], 'choice':['chosen_action','choice_reason'],
 'action':['own_action','artifact'], 'result':['confirmed_result','result_evidence','contribution_scope'],
 'reflection':['observed_learning','change_in_judgment'],
 'future_use':['declared_goal','planned_action','plan_assumptions'],
 'application_process':['reported_step','round_and_timing'], 'writing_advice':[]}


def pattern_entry(fragment,occurrences):
    if fragment.get('approved_pattern') or fragment.get('quality_score') is not None:
        raise ValueError('Unperformed approval or score in writing fragment')
    observed=fragment['observed_structure'];roles=observed.get('event_roles') or []
    slots=sorted({slot for role in roles for slot in SLOT_BY_ROLE.get(role,[])})
    context_only=(set(roles)=={'application_process'} and observed.get('actor') in ('other','mixed','unclear'))
    if context_only:slots=[]
    source_rows=[occurrences[oid] for oid in fragment.get('occurrence_ids',[]) if oid in occurrences]
    opening=observed.get('opening_or_closing_function') or {}
    return {'registry_id':'observation:'+fragment['group_id'],'record_type':'local_writing_observation',
        'origin':'actual_compact_group_inference','approved_pattern':False,'semantic_review':'unreviewed',
        'source_group_id':fragment['group_id'],'execution_ref':fragment['execution_ref'],
        'source_occurrence_ids':fragment.get('occurrence_ids',[]),
        'source_document_ids':sorted({r['document_id'] for r in source_rows}),
        'genres':sorted({r.get('genre') or 'unknown' for r in source_rows}),
        'source_roles':sorted({r.get('source_role') or 'unknown' for r in source_rows}),
        'paragraph_positions':sorted({r.get('paragraph_position') or 'unknown' for r in source_rows}),
        'intent':{'status':'needs_current_task_binding','axes':[],
            'why':'event roles and local writing moves do not determine an official selection intent'},
        'reuse_mode':'reported_prompt_context_only' if context_only else 'conditional_material_or_move_reference',
        'guidance_transfer_requirements':['current_task_fit','genre_fit','own_material_available'] if 'writing_advice' in roles else [],
        'claim_grounds_warrant':observed.get('claim_evidence_reasoning'),
        'material_selection':{'roles':roles,'actor':observed.get('actor'),'observed_use':observed.get('material_role')},
        'paragraph_contract':{'purpose_candidate':observed.get('writing_move'),
            'opening_function_observed':opening.get('opening'),'closing_function_observed':opening.get('closing'),
            'local_connection_observed':observed.get('local_connection'),
            'scope':'visible_passage_only','whole_paragraph_boundary_verified':False,
            'previous_paragraph_id':None,'next_paragraph_id':None,
            'draft_constraints':['bind current official prompt','one paragraph purpose',
                'support claim with confirmed own evidence','first/last bridge is user style preference',
                'review adjacent paragraph relation before drafting']},
        'personal_fact_slots':[{'slot':slot,'status':'need_info','value':None,
            'mandatory':False,'required_when':'current_task_or_selected_claim_requires_this_material',
            'accepted_source':'confirmed_own_material_or_explicit_own_plan',
            'identity_binding':['subject_id','experience_id_or_plan_id'],'source_person_fact_transfer_allowed':False}
            for slot in slots],
        'supplement_request_gate':'bind current official task and chosen claim before requesting candidate slots',
        'limits':observed.get('limits',[]),'outcome_comparison':{'selection_effect_established':False,
            'independent_case_repetition_established':False},'quality_score':None}


def legacy_template_entry(key,value):
    text=encoded(value)
    slots=sorted(set(re.findall(r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}',text)))
    paragraphs=[]
    for i,p in enumerate(value['paragraphs']):
        paragraphs.append({'paragraph_id':f'p{i+1}','purpose':p['purpose'],
            'opening_intent':p['opening_intent'],'closing_intent':p['closing_intent'],
            'previous_paragraph_id':f'p{i}' if i else None,
            'next_paragraph_id':f'p{i+2}' if i+1<len(value['paragraphs']) else None,
            'handoff_slot':p['bridge_to_next']})
    return {'registry_id':'editorial-template:'+key,'record_type':'editorial_template',
        'approved_pattern':False,'origin':'existing_editorial_template_requires_current_prompt_review',
        'official_task_applicability':value['scope'],'paragraphs':paragraphs,
        'personal_fact_slots':[{'slot':slot,'status':'need_info','value':None} for slot in slots],
        'reference_urls':[r['url'] for r in value.get('references',[])],
        'source_evidence_status':'URL references are not silently promoted to verified current requirements',
        'style_precedence':['official_task_constraints','genre_applicability','user_style_preference']}


def load_intents(root):
    output=[]
    for path in sorted(Path(root).glob('*/reviewed-observations.json')):
        bundle=load(path.parent/'source-bundle.json',{})
        docs={d['document_id']:d for d in bundle.get('documents',[])}
        payload=load(path,{})
        for row in payload.get('observations',[]):
            for evidence in row['source_evidence']:
                doc=docs[evidence['document_id']];text=doc['text']
                if sha256(text.encode()).hexdigest()!=evidence['text_sha256']:
                    raise ValueError('Intent source text hash mismatch')
                if text[evidence['char_start']:evidence['char_end']]!=evidence['exact_quote']:
                    raise ValueError('Intent source span mismatch')
            output.append({'registry_id':'intent:'+row['task_id'],'record_type':'stored_intent_observation',
                'input_observation_ref':file_ref(path),'input_bundle_ref':file_ref(path.parent/'source-bundle.json'),
                'task_id':row['task_id'],'question_text':row['question_text'],'question_kind':row['question_kind'],
                'reader_question':row['reader_question'],'purpose_status':row['purpose_status'],
                'answer_use':row['answer_use'],'axes':row['axes'],'evidence_demands':row['evidence_demands'],
                'answer_strategy':row['answer_strategy'],'source_evidence':row['source_evidence'],
                'source_role':row['source_role'],'source_url':row['source_url'],
                'semantic_review':row['semantic_review'],'approved_pattern':False,
                'new_axis_meanings':payload.get('new_axis_meanings',{}),
                'current_official_task_verified':False,'fact_only_no_essay':row['answer_strategy']['status']=='not_needed'})
    return output


# These are explicit ontology bridges between existing evidence-demand labels
# and material schemas. They retrieve partial structures, not equivalent tasks.
DEMAND_SLOTS={
 'transferable_experience':{'own_role','own_action','artifact'},
 'skill_gap_plan':{'observed_learning','planned_action','plan_assumptions'},
 'initiative':{'own_role','chosen_action','choice_reason','own_action'},
 'influence_mechanism':{'choice_reason','own_action'},
 'tangible_result':{'confirmed_result','result_evidence','contribution_scope'},
 'measurable_outcome':{'confirmed_result','result_evidence'},
 'future_network_use':{'declared_goal','planned_action'},
 'learning_goals':{'declared_goal','observed_learning'},
 'personal_goal_link':{'own_motivation','declared_goal'},
 'staged_plan':{'planned_action','plan_assumptions'},
 'feasibility':{'constraint','plan_assumptions'},
 'intended_change':{'declared_goal','planned_action'},
 'responsibility_scope':{'own_role'},'decision_autonomy':{'chosen_action','choice_reason'},
 'placement_context':{'episode_context'}}


def intent_material_links(intents,patterns):
    links=[]
    for intent in intents:
        if intent['fact_only_no_essay'] or intent['answer_use']=='data_collection':continue
        demands={r['role'] for r in intent['evidence_demands']}
        for pattern in patterns:
            if pattern['reuse_mode']=='reported_prompt_context_only':continue
            slots={r['slot'] for r in pattern['personal_fact_slots']}
            matched=sorted(d for d in demands if DEMAND_SLOTS.get(d,set()) & slots)
            if not matched:continue
            common=sorted({s for d in matched for s in DEMAND_SLOTS[d] if s in slots})
            links.append({'registry_id':f'material-link:{intent["task_id"]}:{pattern["source_group_id"]}',
                'record_type':'intent_material_retrieval_candidate','intent_registry_id':intent['registry_id'],
                'observation_registry_id':pattern['registry_id'],'answer_use':intent['answer_use'],
                'partially_matched_demand_roles':matched,'unmatched_demand_roles':sorted(demands-set(matched)),
                'demand_coverage_verified':False,
                'shared_material_slots':common,'status':'partial_material_structure_overlap_not_task_equivalence',
                'matching_method':'curated typed evidence role to material slot, not surface word similarity',
                'required_before_drafting':['verified current official question','genre and submission support policy',
                    'program and round binding','confirmed own material','unmatched evidence demands'],
                'applicant_facts_transferred':False,'draft_permission_granted':False})
    return links


def outcome_boundary(case):
    raw=case.get('source_outcome') or {}
    claim=raw.get('claim') or 'unknown';basis=raw.get('basis') or 'unspecified'
    verified=raw.get('verification_status')=='verified' and bool(raw.get('evidence_refs'))
    return {'case_key':case['case_key'],'program_id':case.get('program_id'),'round_id':case.get('round_id'),
        'source_family':case.get('source_family'),'case_scope':(case.get('source_roles') or {}).get('case_scope'),
        'outcome_claim':claim,'outcome_basis':basis,'explicit_outcome_verification_refs_present':verified,
        'outcome_use':'context_not_causal_writing_quality_gold',
        'cross_source_identity_verified':(case.get('person_identity') or {}).get('cross_source_identity_verified') is True,
        'production_notice_binding':case.get('production_notice_binding'),
        'production_notice_links':case.get('production_notice_links',[])}


def resolve_legacy_basis(source_map,basis,registry_connection=None):
    alias={'official-chevening-leadership-2023':'official:chevening-reading-committee-feedback-2023'}.get(basis,basis)
    if re.fullmatch(r'qa-[0-9]+',alias):alias='stackexchange-answer:'+alias[3:]
    candidates=[family+':'+alias for family in ('open_guides','question_knowledge','application_corpus','participant_documents')]
    if registry_connection is not None:
        rows=registry_connection.execute('SELECT record_id,body_sha256,source_review AS review_state FROM source_route WHERE record_id IN (?,?,?,?)',candidates).fetchall()
    else:
        with sqlite3.connect('file:'+str(source_map)+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT record_id,body_sha256,review_state FROM evidence_record WHERE record_id IN (?,?,?,?)',candidates).fetchall()
    return [dict(row,source_map_path=str(source_map),basis_alias=alias,
        resolution_scope='stored source identity, not semantic approval') for row in rows]


def build_registry(source_map,output,*,writing_root=None,handoff_root=None,cases_path=None,intent_root=None,experience_root=None,force_routes=False):
    source_map,output=Path(source_map),Path(output)
    output.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(output).free<1024**3:raise ValueError('Shared cache has less than 1 GiB free')
    writing_root=Path(writing_root or BASE/'writing');handoff_root=Path(handoff_root or BASE/'compact/handoff')
    existing=output/'registry.sqlite3';meta={}
    if existing.exists():
        with sqlite3.connect('file:'+str(existing)+'?mode=ro',uri=True) as prior:
            meta=dict(prior.execute('SELECT key,value_json FROM metadata'))
    old_map=json.loads(meta.get('input_manifest','{}')).get('source_map',{})
    stat=source_map.stat()
    unchanged=(old_map.get('path')==str(source_map) and old_map.get('bytes')==stat.st_size
        and old_map.get('mtime_ns')==stat.st_mtime_ns and old_map.get('inode')==stat.st_ino)
    map_ref=old_map if unchanged else file_ref(source_map)
    reviews=load(writing_root/'manual-content-reviews.json',{}).get('reviews',[])
    framework=load(REPO/'pipeline/config/writing_evidence_framework_v2.json')
    rubric=load(REPO/'pipeline/config/writing_quality_rubric_v1.json')
    intents_policy=load(REPO/'pipeline/config/semantic_answer_intents_v1.json')
    policy_sha=file_ref(Path(__file__))['sha256']
    reuse=json.loads(meta.get('static_source_sha','null'))==map_ref['sha256'] and not force_routes
    routing_tool_sha=json.loads(meta.get('route_policy_sha','null')) if reuse else policy_sha
    temp=output/'registry.sqlite3.tmp';temp.unlink(missing_ok=True)
    dest=sqlite3.connect(existing if reuse else temp);dest.row_factory=sqlite3.Row
    if not reuse:dest.executescript('''PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;
      CREATE TABLE capability_route(route_id TEXT PRIMARY KEY,spec_json TEXT);
      CREATE TABLE source_route(record_id TEXT PRIMARY KEY,canonical_id TEXT,family TEXT,unit TEXT,
        source_form TEXT,evidence_kind TEXT,genres_json TEXT,body_sha256 TEXT,route_id TEXT,source_review TEXT);
      CREATE INDEX route_family ON source_route(family,unit,route_id);
      CREATE INDEX route_canonical ON source_route(unit,canonical_id);
      CREATE TABLE registry(registry_id TEXT PRIMARY KEY,record_type TEXT,spec_json TEXT);
      CREATE TABLE case_boundary(case_key TEXT PRIMARY KEY,source_family TEXT,case_scope TEXT,
        outcome_claim TEXT,outcome_basis TEXT,identity_verified INTEGER,notice_binding TEXT,spec_json TEXT);
      CREATE TABLE document_route(document_id TEXT PRIMARY KEY,text_sha256 TEXT,source_url TEXT,
        structural_units INTEGER,semantic_review TEXT,spec_json TEXT);
      CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT);
    ''')
    else:
        dest.execute('PRAGMA journal_mode=WAL');dest.execute('BEGIN IMMEDIATE')
        for table in ('registry','case_boundary','metadata'):dest.execute('DELETE FROM '+table)
        dest.execute('CREATE TABLE IF NOT EXISTS document_route(document_id TEXT PRIMARY KEY,text_sha256 TEXT,source_url TEXT,structural_units INTEGER,semantic_review TEXT,spec_json TEXT)')
        dest.execute('DELETE FROM document_route')
    def add(row):
        dest.execute('INSERT OR REPLACE INTO registry VALUES(?,?,?)',
            (row['registry_id'],row['record_type'],encoded(row)))
    count=0
    with sqlite3.connect('file:'+str(source_map)+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row;source.execute('BEGIN');count=0
        iterator=source.execute('SELECT * FROM evidence_record ORDER BY record_id') if not reuse else []
        for row in iterator:
            row=dict(row);route=classify_source(row)
            dest.execute('INSERT OR IGNORE INTO capability_route VALUES(?,?)',(route['route_id'],encoded(route)))
            dest.execute('INSERT INTO source_route VALUES(?,?,?,?,?,?,?,?,?,?)',(
                row['record_id'],row['canonical_id'],row['family'],row['unit'],row['source_form'],row['evidence_kind'],
                row['target_genres'],row['body_sha256'],route['route_id'],row['review_state']))
            count+=1
            if count%100000==0:dest.commit();print(encoded({'source_rows_routed':count}),flush=True)
    if reuse:count=dest.execute('SELECT count(*) FROM source_route').fetchone()[0]
    for stage in framework['content_review_chain']:
        evidence=[r for r in reviews if stage['id'] in r.get('chain_stages',[r.get('chain_stage')])]
        add({'registry_id':'content-chain:'+stage['id'],'record_type':'content_review_stage',
            **stage,'source_section_reviews':evidence,'status':'source_linked_review_contract_not_quality_measurement'})
    stage_map=framework['legacy_rubric_to_content_chain']
    stage_ids={r['id'] for r in framework['content_review_chain']}
    legacy={p['id']:p for p in load(writing_root/'legacy-eight-audit.json',[]) if p.get('quote_verified')}
    other_basis={b:resolve_legacy_basis(source_map,b,dest) for c in rubric['criteria'] for b in c['basis'] if b not in legacy}
    unresolved_bases=set()
    for criterion in rubric['criteria']:
        stages=stage_map[criterion['id']]
        unresolved_bases.update(b for b in criterion['basis'] if b not in legacy and not other_basis.get(b))
        add({'registry_id':'criterion:'+criterion['id'],'record_type':'proposed_content_criterion',
            'criterion_id':criterion['id'],'name':criterion['name'],'applies':criterion['applies'],
            'review_chain_stages':[s for s in stages if s in stage_ids],
            'additional_applicability_axes':[s for s in stages if s not in stage_ids],
            'source_stage_registry_ids':['content-chain:'+s for s in stages if s in stage_ids],
            'original_anchor_text':{str(k):criterion[str(k)] for k in (0,1,2)},
            'legacy_basis_ids':criterion['basis'],
            'verified_legacy_basis':[legacy[b] for b in criterion['basis'] if b in legacy],
            'resolved_source_identity_refs':[r for b in criterion['basis'] for r in other_basis.get(b,[])],
            'unresolved_legacy_basis_ids':[b for b in criterion['basis'] if b not in legacy and not other_basis.get(b)],
            'basis_status':'source-reviewed chain evidence and explicitly resolved legacy quotes; remaining IDs need resolution',
            'score':None,'score_status':'not_assigned','aggregate_score_defined':False,
            'general_genre_style_layers_separate':True})
    for axis,meaning in intents_policy['initial_axes'].items():
        add({'registry_id':'intent-axis:'+axis,'record_type':'open_intent_axis','axis_id':axis,'meaning':meaning,
            'status':'initial_vocabulary_not_universal_selection_criterion','open_axes':True})
    intent_rows=load_intents(intent_root or CACHE/'semantic-intent-v1')
    for row in intent_rows:add(row)
    extra_axes={axis for row in intent_rows for axis in row['axes'] if axis not in intents_policy['initial_axes']}
    for axis in sorted(extra_axes):
        examples=[r for r in intent_rows if axis in r['axes']]
        meanings=[r['new_axis_meanings'].get(axis) for r in examples if isinstance(r['new_axis_meanings'],dict) and r['new_axis_meanings'].get(axis)]
        add({'registry_id':'intent-axis:'+axis,'record_type':'open_intent_axis','axis_id':axis,
            'meaning':meanings[0] if meanings else None,'status':'observed_new_axis_candidate',
            'source_intent_registry_ids':[r['registry_id'] for r in examples]})
    for key,value in load(REPO/'pipeline/config/paragraph_writing_templates_v1.json')['patterns'].items():
        add(legacy_template_entry(key,value))
    handoff_inputs={name:file_ref(handoff_root/name) for name in (
        'writing-fragments.jsonl','applied-occurrences.jsonl','group-interpretations.jsonl') if (handoff_root/name).exists()}
    occurrences={r['occurrence_id']:r for r in jsonlines(handoff_root/'applied-occurrences.jsonl') if r.get('kind')=='experience'}
    patterns=[]
    for fragment in jsonlines(handoff_root/'writing-fragments.jsonl'):
        row=pattern_entry(fragment,occurrences);patterns.append(row);add(row)
    material_links=intent_material_links(intent_rows,patterns)
    for link in material_links:add(link)
    proposals=load(handoff_root/'proposed-writing-templates-20261120.json',{})
    for row in proposals.get('templates',[]):
        add({'registry_id':'analyst-template:'+row['template_id'],'record_type':'analyst_template_proposal',
            **row,'origin':proposals['origin'],'fact_policy':proposals['fact_policy'],'style':proposals['style'],
            'source_observation_registry_ids':['observation:'+g for g in row['supporting_group_ids']]})
    condition_rows=0
    for row in jsonlines(handoff_root/'group-interpretations.jsonl'):
        if row.get('kind')!='notice':continue
        for i,mapping in enumerate(row['interpretation'].get('mappings',[])):
            add({'registry_id':f'condition-bridge:{row["group_id"]}:{i}','record_type':'notice_condition_reference',
                'source_group_id':row['group_id'],'execution_ref':row['execution_ref'],'mapping':mapping,
                'use':'profile_retrieval_or_followup_reference_not_essay_intent',
                'official_writing_question_provided':False,'production_condition_approved':False})
            condition_rows+=1
    experience_root=Path(experience_root or (Path(cases_path).parent if cases_path else BASE/'experience'))
    case_count=0;cases_input=Path(cases_path or experience_root/'cases.jsonl.gz');doc_cases={}
    cases_ref=file_ref(cases_input) if cases_input.exists() else None
    for case in jsonlines(cases_input):
        row=outcome_boundary(case)
        dest.execute('INSERT INTO case_boundary VALUES(?,?,?,?,?,?,?,?)',(
            row['case_key'],row['source_family'],row['case_scope'],row['outcome_claim'],row['outcome_basis'],
            int(row['cross_source_identity_verified']),row['production_notice_binding'],encoded(row)))
        case_count+=1
        for document in case.get('input_documents',[]):
            doc_cases.setdefault(document['document_id'],[]).append({'case_key':case['case_key'],
                'case_scope':(case.get('source_roles') or {}).get('case_scope')})
    documents_input=experience_root/'documents.jsonl.gz'
    documents_ref=file_ref(documents_input) if documents_input.exists() else None
    document_count=0
    for document in jsonlines(documents_input):
        route={'document_id':document['document_id'],'text_ref':document.get('text_ref'),
            'segments_ref':document.get('segments_ref'),'source_url':document.get('source_url'),
            'source_verification':document.get('source_verification'),'case_bindings':doc_cases.get(document['document_id'],[]),
            'source_role_metadata':document.get('source_role_metadata'),'semantic_review':document.get('semantic_review','unreviewed'),
            'capability':'source_passage_candidate_requires_semantic_review',
            'counting_unit':'source_document_version_not_person_or_completed_experience'}
        dest.execute('INSERT INTO document_route VALUES(?,?,?,?,?,?)',(document['document_id'],
            (document.get('text_ref') or {}).get('sha256'),document.get('source_url'),document.get('segment_count',0),
            route['semantic_review'],encoded(route)))
        document_count+=1
    if documents_ref and file_ref(documents_input)['sha256']!=documents_ref['sha256']:
        raise ValueError('Document input changed during registry refresh; retry the code-only refresh')
    if cases_ref and file_ref(cases_input)['sha256']!=cases_ref['sha256']:
        raise ValueError('Case input changed during registry refresh; retry the code-only refresh')
    if any(file_ref(handoff_root/name)['sha256']!=ref['sha256'] for name,ref in handoff_inputs.items()):
        raise ValueError('Compact handoff changed during registry refresh; retry the code-only refresh')
    dest.commit()
    families=[dict(r) for r in dest.execute('SELECT family,unit,route_id,count(*) source_rows,count(DISTINCT canonical_id) canonical_records FROM source_route GROUP BY family,unit,route_id')]
    genres=[dict(r) for r in dest.execute('SELECT j.value genre,s.unit,s.route_id,count(*) source_rows FROM source_route s,json_each(s.genres_json) j GROUP BY j.value,s.unit,s.route_id')]
    outcome_counts=[dict(r) for r in dest.execute('SELECT outcome_claim,outcome_basis,count(*) cases FROM case_boundary GROUP BY outcome_claim,outcome_basis')]
    record_counts=dict(dest.execute('SELECT record_type,count(*) FROM registry GROUP BY record_type'))
    summary={'version':'writing-registry-expansion-v1','at':datetime.now(timezone.utc).isoformat(),
        'source_map_ref':map_ref,'registry_path':str(output/'registry.sqlite3'),
        'source_rows_routed':count,'source_unit_warning':'Map rows mix documents, essays, annotations, posts, pairs and labels; do not sum as people or complete applications.',
        'static_source_routes_reused':reuse,'source_route_rows_processed_this_run':0 if reuse else count,
        'source_map_fingerprint_reused':unchanged,
        'source_route_generation_tool_sha256':routing_tool_sha,'refresh_tool_sha256':policy_sha,
        'support_distribution_by_family_and_unit':families,'registry_record_counts':record_counts,
        'case_boundaries_processed':case_count,'outcome_claim_distribution':outcome_counts,
        'production_document_routes':document_count,'experience_root':str(experience_root),
        'structural_units_referenced_not_semantically_completed':dest.execute('SELECT coalesce(sum(structural_units),0) FROM document_route').fetchone()[0],
        'outcome_claim_totals':dict(dest.execute('SELECT outcome_claim,count(*) FROM case_boundary GROUP BY outcome_claim')),
        'case_scope_distribution':[dict(r) for r in dest.execute('SELECT case_scope,count(*) cases FROM case_boundary GROUP BY case_scope')],
        'case_notice_binding_distribution':[dict(r) for r in dest.execute('SELECT notice_binding,count(*) cases FROM case_boundary GROUP BY notice_binding')],
        'cross_source_identity_verification':dict(dest.execute('SELECT identity_verified,count(*) FROM case_boundary GROUP BY identity_verified')),
        'scored_unique_essay_texts':dest.execute("SELECT count(DISTINCT canonical_id) FROM source_route WHERE unit='essay'").fetchone()[0],
        'stored_intent_observations':len(intent_rows),'fact_only_intent_observations':sum(r['fact_only_no_essay'] for r in intent_rows),
        'initial_intent_axes':len(intents_policy['initial_axes']),'observed_new_intent_axes':len(extra_axes),
        'intent_material_retrieval_candidates':len(material_links),
        'local_writing_observations':len(patterns),'notice_condition_references':condition_rows,
        'observations_needing_current_prompt_binding':sum(p['intent']['status']=='needs_current_task_binding' for p in patterns),
        'observations_without_whole_paragraph_boundary_verification':sum(not p['paragraph_contract']['whole_paragraph_boundary_verified'] for p in patterns),
        'personal_fact_slots_requested':sum(len(p['personal_fact_slots']) for p in patterns),
        'filled_applicant_fact_slots':0,'quality_scores_assigned':0,'approved_patterns':0,
        'unresolved_legacy_criterion_basis_ids':sorted(unresolved_bases),
        'source_semantic_review_of_all_rows_performed':False,'model_calls_this_run':0,'network_requests':0,
        'independent_human_evaluation_performed':False}
    evaluation={'version':'writing-evaluation-plan-v1','status':'source_linked_plan_not_executed',
        'available_registry_record_counts':record_counts,'source_material_distribution_ref':'support-distribution.jsonl',
        'available_evaluation_material_by_family_and_unit':[r for r in families if r['unit'] in ('essay','annotation','revision_pair','label_observation')],
        'rubric_ids':['criterion:'+r['id'] for r in rubric['criteria']],
        'fact_and_format_gates':rubric['required_checks'],
        'design':[{'stage':'task_and_genre_strata','required':['verified_current_prompt','genre','author_program_round_identity'],
                   'if_missing':'hold task-specific quality decision'},
                  {'stage':'blinded_pairwise_content_review','required':['two independent humans','source-and-fact references','criterion-level reasons'],
                   'measure':['requirement coverage','unsupported inference','claim-ground-warrant links','paragraph and document coherence','time to revise'],
                   'score_assignment':'none until reviewers actually evaluate'},
                  {'stage':'agreement_and_adjudication','required':['independent labels before discussion','report agreement by genre and criterion','adjudicate mismatches']},
                  {'stage':'leakage_control','required':['split by author, program and round','exclude development examples','deduplicate by source and text'],
                   'known_limitation':'Previously analyzed PERSUADE test is development material. Unknown author identities prevent a claimed clean holdout.'},
                  {'stage':'model_judge_calibration','required':['human-reviewed holdout','judge disagreement analysis','factuality and constraint checks'],
                   'promotion_gate':'human-reviewed relevance and coherence, no arbitrary numeric acceptance cutoff'}],
        'outcome_policy':'Reported success/failure is context; matched outcomes do not establish causal writing quality.',
        'execution_counts':{'human_reviews':0,'new_quality_predictions':0,'causal_success_effect_tests':0}}
    manifest={'source_map':map_ref,'writing_framework':file_ref(REPO/'pipeline/config/writing_evidence_framework_v2.json'),
        'rubric':file_ref(REPO/'pipeline/config/writing_quality_rubric_v1.json'),
        'intent_policy':file_ref(REPO/'pipeline/config/semantic_answer_intents_v1.json'),
        'experience_cases':cases_ref,'experience_documents':documents_ref,'compact_input_refs':list(handoff_inputs.values())}
    dest.execute('INSERT INTO metadata VALUES(?,?)',('summary',encoded(summary)))
    dest.execute('INSERT INTO metadata VALUES(?,?)',('input_manifest',encoded(manifest)))
    dest.execute('INSERT INTO metadata VALUES(?,?)',('static_source_sha',encoded(map_ref['sha256'])))
    dest.execute('INSERT INTO metadata VALUES(?,?)',('route_policy_sha',encoded(routing_tool_sha)))
    dest.commit();dest.close()
    if not reuse:temp.replace(output/'registry.sqlite3')
    for name,data in [('summary.json',summary),('evaluation-plan.json',evaluation),('input-manifest.json',manifest)]:
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    for name,rows in [('support-distribution.jsonl',families),('genre-distribution.jsonl',genres)]:
        (output/name).write_text(''.join(encoded(row)+'\n' for row in rows))
    examples={'source_scope':'local observations and analyst proposals, not approved success patterns',
        'observations':[{'registry_id':p['registry_id'],'source_group_id':p['source_group_id'],
            'material_selection':p['material_selection'],'claim_grounds_warrant':p['claim_grounds_warrant'],
            'personal_fact_slots':p['personal_fact_slots'],'paragraph_contract':p['paragraph_contract']} for p in patterns[:2]],
        'retrieval_example':'SELECT spec_json FROM registry WHERE record_type = \'local_writing_observation\';'}
    (output/'examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2)+'\n')
    report=(f'작성 기준과 패턴 registry 인계\n\n'
        f'근거 지도 {count:,}행의 역할 경로를 연결했다. 단위별 분포는 support-distribution.jsonl에 기록했다. '
        f'이번 실행에서 새로 경로를 처리한 행은 {summary["source_route_rows_processed_this_run"]:,}개다. '\
        '이는 보존 자료의 역할 연결 수이며 전량 LLM 의미 검토 수와 구분한다.\n\n'
        f'실제 인계: 내용 검토 단계 6개, 미보정 기준 9개, 초기 의도 축 9개와 새 축 후보 {len(extra_axes)}개, 저장된 문항 의도 관찰 {len(intent_rows)}개, '
        f'국소 작성 관찰 {len(patterns)}개, 공고 조건 참고 {condition_rows}개를 registry.sqlite3에 연결했다. '
        '기존 문단 템플릿 3개와 근거에 기반한 분석자 제안 2개는 별도 유형이다.\n\n'
        f'문항의 증거 요구와 작성 재료 슬롯을 연결한 부분 검색 후보는 {len(material_links)}개다. '
        '행정 사실 문항은 제외하고, 부족한 요구와 현행 문항 및 프로그램 연결을 함께 남겼다.\n\n'
        f'작성 관찰에서 본인 사실 후보 슬롯 {summary["personal_fact_slots_requested"]}개를 도출했고 채워진 개인 사실은 0개다. '
        '슬롯은 공식 문항과 선택한 주장에 필요한 것만 요청한다. '
        f'현행 공식 문항 연결은 {summary["observations_needing_current_prompt_binding"]}개, '
        f'원문 전체 문단 경계 확인은 {summary["observations_without_whole_paragraph_boundary_verification"]}개 관찰에 남아 있다. '
        f'사실 확인형 문항 관찰 {summary["fact_only_intent_observations"]}개에는 설득형 글 구조를 강제하지 않는다.\n\n'
        f'평가 참조에는 고유 채점 글 {summary["scored_unique_essay_texts"]:,}편의 원 척도를 보존한다. '
        f'사례 {case_count:,}건의 결과 표지는 {encoded(summary["outcome_claim_totals"])}이다. '
        '선정과 미선정은 맥락 자료로 사용하고 인과적 글쓰기 품질 정답으로 변환하지 않는다.\n\n'
        f'현재 근거 지도에서 연결되지 않은 기존 기준 출처 ID는 {encoded(summary["unresolved_legacy_criterion_basis_ids"])}이다. '
        '장르 미상, 문학 자료의 지원서 전이, 모델의 수정 의도 예측과 실제 개선 여부는 지원 한계로 구분했다.\n\n'
        '다음 단계는 현행 공식 문항과 프로그램 회차 연결, 본인 사실 입력, 앞뒤 문단 관계 검토다. '
        '평가에는 작성자와 프로그램 및 회차를 분리한 자료와 독립 사람 2명의 가림 비교, 기준별 불일치 조정이 필요하다. '
        'evaluation-plan.json에 자료와 게이트를 연결했으며 새 점수와 성공률은 생성하지 않았다.\n\n'
        '실행: python3 tools/expand_benefitlens_writing_registry.py\n'
        '변경 없는 근거 지도 경로는 재사용하고 새 문항, 관찰과 조건을 코드로 갱신한다. 외부 모델과 네트워크 호출은 0회다.\n')
    (output/'report.txt').write_text(report)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-map',type=Path,default=BASE/'writing/evidence-map.sqlite3')
    parser.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--experience-root',type=Path)
    parser.add_argument('--case-source',type=Path)
    parser.add_argument('--rebuild-source-routes',action='store_true')
    args=parser.parse_args()
    if not args.output_root.resolve().is_relative_to(CACHE.resolve()):raise ValueError('Use the shared dataset cache')
    args.output_root.mkdir(parents=True,exist_ok=True)
    with (args.output_root/'.refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        print(encoded(build_registry(args.source_map,args.output_root,experience_root=args.experience_root,
            cases_path=args.case_source,force_routes=args.rebuild_source_routes)))


if __name__=='__main__':main()
