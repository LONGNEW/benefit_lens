"""Bound the user vocabulary and group reusable facts, preserving source logic."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]
CONTRACT=REPO/'pipeline/config/profile_question_contract_v1.json'


def load_contract(path=CONTRACT):
    c=json.loads(Path(path).read_text())
    if len(c['condition_types'])>min(100,c['max_condition_types']):raise ValueError('Condition type cap exceeded')
    if len(c.get('input_field_types',c['user_fact_field_ids']))>min(100,c['max_profile_fields']):raise ValueError('Profile field cap exceeded')
    if len(c['initial_fields'])>c['max_initial_fields']:raise ValueError('Initial question cap exceeded')
    if len({x['field_id'] for x in c['initial_fields']})!=len(c['initial_fields']):raise ValueError('Duplicate initial field')
    if not set(c['legacy_field_types'].values())<=set(c['condition_types']):raise ValueError('Unknown condition type')
    return c


def initial_form(contract=None,known_fields=()):
    c=contract or load_contract();known=set(known_fields)
    return [{'group_id':g,'label':definition['label'],'fields':[deepcopy(f) for f in c['initial_fields'] if f['group']==g and f['field_id'] not in known]}
        for g,definition in c['groups'].items() if definition['initial'] and any(f['group']==g and f['field_id'] not in known for f in c['initial_fields'])]


def requirement_type_group(mapping):
    """A stored semantic key is a grouping candidate, never a field definition."""
    return [mapping.get('program_specific_key'),mapping['subject'],mapping['statement_type']]


def question_plan(records,*,selected_notices=(),known_facts=(),type_lookup=None,contract=None,subject_id=None):
    c=contract or load_contract();selected=set(selected_notices);type_lookup=type_lookup or {}
    if known_facts and not subject_id:raise ValueError('Confirmed facts need a scoped applicant identity')
    grouped={};held=[];not_questions=[]
    for record in records:
        m=record.get('mapping',record);mid=record.get('mapping_id');notice=record.get('candidate_key')
        tid=c['legacy_field_types'].get(m['field_id'])
        if m['field_id']=='program_specific.requirement':tid=type_lookup.get(tuple(requirement_type_group(m)),'unresolved')
        definition=c['condition_types'][tid]
        if m['subject']!='applicant' or definition['question_group']=='notice_only' or m['statement_type'] in ('description','benefit_description'):
            not_questions.append(mid);continue
        if not selected or notice not in selected:continue
        reasons=[]
        if tid in ('unresolved','program.special'):reasons.append('semantic_type_requires_review')
        if record.get('semantic_review') not in ('reviewed','approved','accepted'):reasons.append('source_condition_not_reviewed')
        if m.get('reference_phase')=='unknown':reasons.append('reference_time_unknown')
        if m.get('ambiguities'):reasons.append('source_ambiguity')
        if m['field_id']=='education.degree' and record.get('fact_basis') not in ('currently_pursuing','earned'):reasons.append('degree_earned_vs_pursuing_unknown')
        if m.get('modality')=='unknown':reasons.append('requiredness_unknown')
        if reasons:
            held.append({'mapping_id':mid,'candidate_key':notice,'type_id':tid,'reasons':reasons});continue
        # A fact for one program/date must not silently answer a different date.
        date=record.get('reference_date')
        phase=m.get('reference_phase')
        time_key=date or ('unresolved-date:'+str(notice) if phase in ('benefit_start','throughout_activity') or m['field_id']=='person.age' else 'current_application_snapshot')
        field=definition.get('legacy_profile_field') or tid
        basis=record.get('fact_basis')
        key=(field,m['subject'],phase,time_key,m.get('unit'),basis)
        reusable=any(f.get('subject_id')==subject_id and f.get('value') is not None and f.get('fact_basis')==basis and f.get('field_id')==field and f.get('subject')==m['subject'] and f.get('reference_phase')==phase and f.get('time_key')==time_key and f.get('unit')==m.get('unit') and f.get('confirmed') is True for f in known_facts)
        if reusable:continue
        item=grouped.setdefault(key,{'field_id':field,'type_id':tid,'label':definition['label'],'group_id':definition['question_group'],
            'subject':m['subject'],'reference_phase':phase,'time_key':time_key,'unit':m.get('unit'),'fact_basis':basis,
            'required':False,'optional_only':True,'source_conditions':[],'issued':False})
        item['optional_only'] &= m['modality'] in ('preferred','optional')
        item['required'] |= m['modality'] in ('required','prohibited')
        item['source_conditions'].append({'mapping_id':mid,'candidate_key':notice,'mapping':deepcopy(m),
            'bindings':deepcopy(record.get('bindings',[]))})
    chunks=defaultdict(list)
    for item in grouped.values():chunks[item['group_id']].append(item)
    return {'version':c['version'],'initial_form':initial_form(c,known_fields={f['field_id'] for f in known_facts if f.get('subject_id')==subject_id and f.get('confirmed') is True and f.get('value') is not None and (f['field_id']!='education.degree' or f.get('fact_basis')=='currently_pursuing')}),
        'selected_notice_followups':[{'group_id':g,'label':c['groups'][g]['label'],'questions':questions} for g,questions in chunks.items()],
        'unique_followup_questions':len(grouped),'held_conditions':held,'non_applicant_information':len(not_questions),
        'live_questions_issued':0,'condition_logic_collapsed':False}


def load_type_lookup(path,contract=None):
    import sqlite3
    from hashlib import sha256
    c=contract or load_contract();result={}
    if not Path(path).exists():return result
    serialized=json.dumps(c['condition_types'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    expected=sha256(serialized.encode()).hexdigest()
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True,timeout=10)
    try:
        for group_json,payload,tid in db.execute('SELECT t.group_json,t.payload,r.type_id FROM type_tasks t JOIN type_results r USING(task_id) WHERE t.active=1'):
            if json.loads(payload).get('taxonomy_sha256')==expected and tid in c['condition_types']:result[tuple(json.loads(group_json))]=tid
    finally:db.close()
    return result
