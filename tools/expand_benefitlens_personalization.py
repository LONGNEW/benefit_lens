"""Project every real notice into provenance- and subject-aware input candidates.

Consumes the shared catalog and actual compact mappings. Never turns a lexical
mention or an unreviewed extraction recipe into a personal eligibility decision.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from audit_benefitlens_experience_production import JsonLines, jsonl
from benefitlens_profile_questions import load_contract,load_type_lookup,question_plan
from benefitlens_compact_notice import _quote_binding, _locate, validate_group
from expand_benefitlens_experience_quality import readonly, verified_bytes, normalize, encoded, digest
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer

REPO=Path(__file__).resolve().parents[1]
CONFIG=REPO/'pipeline/config/personalization_expansion_v1.json'


def route_mapping(mapping, catalog):
    field=mapping['field_id']
    if field not in catalog['fields']:
        return 'source_clarification'
    if mapping['subject'] in ('institution','referee','employer','program'):
        return 'external_or_program_information'
    if mapping['subject']!='applicant':
        return 'source_clarification'
    stage=catalog['fields'][field]['applicant_input_stage']
    if stage=='not_applicant_input' or mapping['statement_type'] in ('description','benefit_description') or mapping['modality']=='informational':
        return 'program_information'
    if mapping['statement_type']=='unknown' or mapping['modality']=='unknown':
        return 'source_clarification'
    if mapping['modality'] in ('preferred','optional'):
        return 'optional_followup'
    if mapping['statement_type'] not in ('eligibility_requirement','submission_requirement','participation_obligation'):
        return 'source_clarification'
    return stage


def document_materials(mapping, config):
    if mapping['field_id']!='application.document_requirement':
        return []
    text=mapping.get('value_quote') or mapping['evidence_quote']
    output=[]
    for hint in config['document_hints']:
        for match in re.finditer(hint['regex'],text,re.I):
            student_draft=hint['drafting_material'] and mapping['subject']=='applicant'
            author=hint['author']
            if mapping['subject'] in ('institution','referee','employer'):
                author=mapping['subject']
            elif mapping['subject'] in ('program','unknown'):
                author='unknown'
            output.append({'document_kind':hint['kind'],'literal_name':match[0],
                'authorship_candidate':author,'submission_subject':mapping['subject'],
                'student_authored_draft':student_draft,
                'modality':mapping['modality'],'reference_phase':mapping['reference_phase'],
                'modality_applies_to':'source_document_bundle_not_individual_item',
                'individual_document_required':None,
                'source_bundle_quote':text,'bundle_logic_cues':mapping['logic_cues'],
                'bundle_ambiguities':mapping['ambiguities'],
                'status':'literal_document_type_candidate_unreviewed',
                'route':'applicant_writing_material' if student_draft else 'source_clarification' if author=='unknown' else 'external_document_collection',
                'confirmed_applicant_facts_required':student_draft})
    if not output:
        output.append({'document_kind':'unknown','literal_name':text,'authorship_candidate':'unknown',
            'submission_subject':mapping['subject'],'modality':mapping['modality'],
            'reference_phase':mapping['reference_phase'],'status':'document_authorship_needs_clarification',
            'individual_document_required':None,'source_bundle_quote':text,
            'bundle_logic_cues':mapping['logic_cues'],'bundle_ambiguities':mapping['ambiguities'],
            'student_authored_draft':False,'route':'source_clarification'})
    return output


def question_projection(mapping, mapping_id, catalog, config):
    route=route_mapping(mapping,catalog)
    phase=mapping['reference_phase']
    label=config['field_labels'].get(mapping['field_id'],mapping['field_id'])
    phase_label=config['phase_labels'].get(phase,phase)
    reasons=[]
    if phase=='unknown':reasons.append('reference_time_unknown')
    if mapping['ambiguities']:reasons.append('source_ambiguity_not_resolved')
    if mapping['operator']=='unknown' or mapping['value_extraction_method']=='unresolved':reasons.append('condition_value_or_operator_unknown')
    if mapping['subject']=='unknown':reasons.append('required_subject_unknown')
    if mapping['field_id']=='person.age' and phase=='benefit_start':reasons.append('exact_program_start_date_needed_for_age')
    target='applicant' if route in ('common_profile','program_followup','optional_followup') else 'source_reviewer'
    if mapping['subject'] in ('institution','referee'):
        target=mapping['subject']
    question=None
    if target=='applicant':
        question=f"{phase_label}을 기준으로 {label} 정보를 확인할 수 있나요?"
        if mapping.get('value_quote'):
            question+=f" 원문에는 '{mapping['value_quote']}'가 제시되어 있습니다."
    return {'mapping_id':mapping_id,'field_id':mapping['field_id'],
        'catalog_input_stage':catalog['fields'].get(mapping['field_id'],{}).get('applicant_input_stage'),
        'route':route,'recipient':target,'subject':mapping['subject'],'modality':mapping['modality'],
        'statement_type':mapping['statement_type'],'reference_phase':phase,'reference_date':None,
        'operator':mapping['operator'],'unit':mapping['unit'],
        'value_extraction_method':mapping['value_extraction_method'],'value_quote':mapping['value_quote'],
        'logic_cues':mapping['logic_cues'],'ambiguities':mapping['ambiguities'],
        'proposed_question':question,'question_state':'source_clarification_required' if reasons or route=='source_clarification' else 'candidate_not_issued',
        'blocked_reasons':reasons,'required_user_input':False,'live_question_issued':False,
        'original_modality_retained':True,'eligibility_decision':'unknown',
        'extraction_recipe_executed_as_personal_predicate':False}


def rebound_representative(occurrence, representative, text):
    rep=deepcopy(representative)
    a,b,ca,cb=(occurrence[key] for key in ('char_start','char_end','context_start','context_end'))
    if not 0<=ca<=a<b<=cb<=len(text):raise ValueError('Invalid notice source offsets')
    if normalize(text[a:b])!=normalize(rep['text']) or normalize(text[ca:cb])!=normalize(rep['context_text']):
        raise ValueError('Notice expression or protected context differs')
    rep.update(text=text[a:b],context_text=text[ca:cb],char_start=a,char_end=b,context_start=ca,context_end=cb,
        document_id=occurrence['document_id'],text_sha256=occurrence['text_sha256'],source_url=occurrence['source_url'])
    heading=representative.get('heading')
    if heading:
        # A representative heading's old document coordinates are never copied
        # into another source. Rebind only a unique nearby preceding occurrence.
        left=max(0,a-2000)
        found=_locate(heading['text'],text[left:a],left)
        rep['heading']={'text':found['exact_source_quote'],'char_start':found['char_start'],'char_end':found['char_end']} if found else None
    return rep


def run(args):
    base=checked_root(args.root);root=base/'personalization';root.mkdir(parents=True,exist_ok=True)
    config=json.loads(CONFIG.read_text());catalog_path=REPO/config['catalog'];catalog=json.loads(catalog_path.read_text())
    catalog_sha=digest(catalog_path.read_bytes())
    input_contract=load_contract()
    type_lookup=load_type_lookup(base.parent/'semantic-connections/profile/profile.sqlite',input_contract)
    with single_writer(root):
        db=sqlite3.connect(root/'requirements.sqlite')
        db.executescript('DROP TABLE IF EXISTS mappings;DROP TABLE IF EXISTS requirements;'
            'CREATE TABLE mappings(mapping_id TEXT PRIMARY KEY,candidate_key TEXT,group_id TEXT,field_id TEXT,route TEXT,subject TEXT,payload TEXT);'
            'CREATE INDEX mapping_candidate ON mappings(candidate_key);'
            'CREATE TABLE requirements(candidate_key TEXT PRIMARY KEY,payload TEXT);')
        notices={}
        for notice in jsonl(args.notice_root/'notices.jsonl.gz'):
            # Keep one inventory row per real candidate, including missing originals.
            notices[notice['candidate_key']]={key:notice.get(key) for key in ('candidate_key','title','source_url','current_open_verified','variant_count','unknown_reasons','original_ref','conditions','projection_state','unresolved_fields')}
        document_refs={n['original_ref']['document_id']:n['original_ref'] for n in notices.values() if n['original_ref']}
        cx=readonly(args.compact_db)
        group_refs={}
        for row in cx.execute("SELECT group_id,representative_json,result_json,receipt_path FROM groups WHERE kind='notice' AND state='analyzed'"):
            annotation=json.loads(row['result_json'])
            group_refs[row['group_id']]={'representative':json.loads(row['representative_json']),
                'annotation':annotation,'receipt_path':row['receipt_path']}
        @lru_cache(maxsize=12)
        def text_for(did):
            ref=document_refs[did]
            return verified_bytes({'path':ref['text_path'],'sha256':ref['text_sha256']})[0].decode('utf8')
        stats=defaultdict(Counter);notice_occurrence_counts=Counter();interpreted_occurrence_counts=Counter()
        for candidate,count in cx.execute("SELECT record_key,count(*) FROM occurrences WHERE kind='notice' GROUP BY record_key"):
            notice_occurrence_counts[candidate]=count
        query="SELECT * FROM occurrences WHERE kind='notice' AND group_id IN (SELECT group_id FROM groups WHERE kind='notice' AND state='analyzed') ORDER BY rowid"
        with JsonLines(root/'mapping-applications.jsonl.gz') as output:
            for row in cx.execute(query):
                ref=group_refs[row['group_id']]
                try:
                    source=text_for(row['document_id'])
                    if digest(source.encode())!=row['text_sha256']:raise ValueError('Notice document version differs')
                    representative=rebound_representative(row,ref['representative'],source)
                    raw={'group_id':row['group_id'],'mappings':ref['annotation']['mappings'],'skip_reason':ref['annotation']['skip_reason']}
                    bound=validate_group(raw,{'group_id':row['group_id'],'representative':representative,'occurrence_count':1})
                    interpreted_occurrence_counts[row['record_key']]+=1
                    stats['mapping_occurrences']['source_verified']+=1
                    for index,mapping in enumerate(bound['mappings']):
                        mapping_id='requirement-'+digest(encoded([row['occurrence_id'],index,mapping]))[:28]
                        question=question_projection(mapping,mapping_id,catalog,config)
                        record={'mapping_id':mapping_id,'candidate_key':row['record_key'],'group_id':row['group_id'],
                            'occurrence_id':row['occurrence_id'],'document_id':row['document_id'],'text_sha256':row['text_sha256'],
                            'source_spans':{key:row[key] for key in ('char_start','char_end','context_start','context_end','sentence_index')},
                            'mapping':mapping,'bindings':[x for x in bound['source_bindings'] if x['mapping_index']==index],
                            'input_requirement':question,'document_materials':document_materials(mapping,config),
                            'catalog_sha256':catalog_sha,'reference_receipt':ref['receipt_path'],
                            'interpretation_status':'code_application_of_unreviewed_semantic_reference',
                            'semantic_review':'unreviewed','personal_eligibility_decision':'unknown'}
                        output.write(record)
                        db.execute('INSERT INTO mappings VALUES(?,?,?,?,?,?,?)',(mapping_id,row['record_key'],row['group_id'],mapping['field_id'],question['route'],mapping['subject'],encoded(record)))
                        stats['field_mapping_instances'][mapping['field_id']]+=1
                        stats['requirement_routes'][question['route']]+=1
                        stats['subjects'][mapping['subject']]+=1
                        stats['modalities'][mapping['modality']]+=1
                        stats['reference_phases'][mapping['reference_phase']]+=1
                except (ValueError,KeyError,OSError,UnicodeError) as error:
                    output.write({'occurrence_id':row['occurrence_id'],'group_id':row['group_id'],'candidate_key':row['record_key'],
                        'source_binding_verified':False,'state':'source_clarification_required','error':type(error).__name__+': '+str(error)[:250]})
                    stats['mapping_occurrences']['binding_unresolved']+=1
        db.commit()
        material_pool=[]
        quality_groups=base/'quality/groups.jsonl.gz'
        if quality_groups.exists():
            for group in jsonl(quality_groups):
                annotation=group.get('semantic_reference') or {}
                if annotation.get('relevance')=='relevant' and group['auto_excluded_occurrences']<group['occurrences']:
                    material_pool.append({'group_id':group['group_id'],'event_roles':annotation['event_roles'],
                        'writing_move':annotation['writing_move'],'material_role':annotation['material_role'],
                        'representative_source_ref':group['representative_source_ref'],
                        'use':'source_writing_reference_not_applicant_fact','semantic_review':'unreviewed'})
        atomic_json(root/'writing-material-reference-pool.json',{'references':material_pool,'program_round_equivalence_established':False})
        with JsonLines(root/'requirements.jsonl.gz') as output:
            for candidate,notice in notices.items():
                applications=[json.loads(row[0]) for row in db.execute('SELECT payload FROM mappings WHERE candidate_key=?',(candidate,))]
                channels={key:[] for key in ('common_profile','program_followup','optional_followup','source_clarification','program_information','external_or_program_information')}
                writing=[]
                for application in applications:
                    requirement=application['input_requirement'];channels[requirement['route']].append(requirement)
                    writing.extend(dict(item,mapping_id=application['mapping_id']) for item in application['document_materials'])
                raw_fields=Counter(condition['field_id'] for condition in notice['conditions'] or [])
                compact_count=notice_occurrence_counts[candidate]
                record={'candidate_key':candidate,'title':notice['title'],'source_url':notice['source_url'],
                    'catalog_sha256':catalog_sha,'current_open_verified':notice['current_open_verified'] is True,
                    'variant_count':notice['variant_count'],'source_projection_state':notice['projection_state'],
                    'input_channels':channels,'writing_material_requests':writing,
                    'raw_unreviewed_condition_mentions_by_field':dict(raw_fields),
                    'coverage':{'compact_source_occurrences':compact_count,
                        'source_verified_interpreted_occurrences':interpreted_occurrence_counts[candidate],
                        'pending_or_unresolved_compact_occurrences':compact_count-interpreted_occurrence_counts[candidate],
                        'derived_mapping_instances':len(applications)},
                    'unknown_reasons':list(notice['unknown_reasons'] or [])+
                        ['condition_logic_and_current_open_status_require_review','no_provenance_bound_applicant_profile'],
                    'unresolved_fields':notice['unresolved_fields'],
                    'writing_reference_pool':{'path':str(root/'writing-material-reference-pool.json'),
                        'candidate_passage_groups':len(material_pool),'same_program_or_success_effect_claimed':False},
                    'personal_eligibility_decision':'unknown','live_questions_issued':0,
                    'user_input_plan':question_plan(applications,selected_notices=[candidate],type_lookup=type_lookup,contract=input_contract),
                    'official_document_fact_policy':{'extract_only_explicit_fields':True,'gpa_does_not_imply_coursework_completion':True,
                        'country_of_institution_does_not_imply_nationality':True,'public_reference_cases_are_not_applicant_facts':True}}
                output.write(record);db.execute('INSERT INTO requirements VALUES(?,?)',(candidate,encoded(record)))
                stats['notice_projection']['all_notices']+=1
                stats['notice_projection']['with_new_source_verified_mapping' if applications else 'without_new_source_verified_mapping']+=1
                stats['writing_material_candidates'].update(item['document_kind'] for item in writing)
        db.commit();cx.close()
        field_profile={field:{'catalog_input_stage':definition['applicant_input_stage'],
            'label':config['field_labels'].get(field,field),'mandatory_default':False,
            'reuse_rule':'same_subject_reference_time_and_confirmed_fact_source_required'} for field,definition in catalog['fields'].items()}
        atomic_json(root/'profile-field-catalog.json',{'catalog_sha256':catalog_sha,'fields':field_profile})
        summary={'version':config['version'],'at_kst':datetime.now(KST).isoformat(),
            'reference_semantic_notice_groups':len(group_refs),'new_manual_meaning_interpretations':0,
            'catalog':{'path':str(catalog_path),'sha256':catalog_sha,'fields':len(catalog['fields'])},
            'user_input_contract':{'version':input_contract['version'],'shared_field_types':len(catalog['fields']),
                'reusable_user_fact_field_types':len(input_contract['user_fact_field_ids']),'initial_fields':len(input_contract['initial_fields']),
                'initial_chunks':2,'question_chunks':len(input_contract['groups']),'condition_type_cap':input_contract['max_condition_types'],
                'configured_condition_types':len(input_contract['condition_types']),'configured_input_field_types':len(input_contract['input_field_types']),'initial_mandatory_fields':0,
                'raw_mapping_rows_are_user_field_types':False},
            'counters':{key:dict(value) for key,value in stats.items()},
            'writing_reference_passage_groups':len(material_pool),'quality_reference_available':quality_groups.exists(),
            'source_database_writes':0,'canonical_database_writes':0,'model_calls':0,'external_requests':0,
            'live_questions_issued':0,'personal_eligibility_decisions_issued':0,
            'derived_database':str(root/'requirements.sqlite')}
        atomic_json(root/'summary.json',summary);db.close();print(encoded(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=CACHE/'team-production-20261119/team-expansion-20261120/experience-personalization')
    parser.add_argument('--notice-root',type=Path,default=CACHE/'team-production-20261119/notice')
    parser.add_argument('--compact-db',type=Path,default=CACHE/'team-production-20261119/compact/queue.sqlite')
    run(parser.parse_args())
