"""Bind source-backed values first, then render sentences from the literal template.

The free-form LLM draft remains an immutable candidate in calls/. Final text
cannot omit required unknown slots or turn completed material into planned work.
"""
from __future__ import annotations
import json
import re
import benefitlens_end_to_end as e

ENGLISH_FORMS=[
 {'opening':'In {{문제맥락}}, {{원하는상태}} matters.',
  'support':'Yet {{기존접근}} leaves {{해결하려는문제}} unresolved. I {{사전활동}} and observed {{관찰결과}}.',
  'closing':'Addressing this gap requires {{해결요구}}.'},
 {'opening':'A solution must satisfy {{성공조건}}.',
  'support':'The obstacle is {{핵심장애}}, which leaves {{기존접근의한계}}.',
  'closing':'The first task is therefore {{핵심과제}}.'},
 {'opening':'For this task, the requirement is {{충족할요구}}. I chose {{선택한접근}} because {{접근선택이유}}.',
  'support':'I {{본인행동}}, obtaining {{기여결과}}. The evidence in {{확인자료}} establishes {{확인된범위}}.',
  'closing':'This contributes {{기여의의}}, but the remaining question is {{남은질문}}.'},
 {'opening':'This remains unresolved because {{미해결이유}}.',
  'support':'My completed {{관련작업}} provides a basis for {{다음연구에쓰는방식}}.',
  'closing':'This leads to the next research question: {{연구질문}}.'},
 {'opening':'To answer this question, I plan to {{계획방법}}.',
  'support':'I will examine {{평가기준}} under {{비교조건}}, using {{판정기준}} to judge the answer.',
  'closing':'The findings will provide {{남길결과물}} on {{해결하려는문제}}, allowing {{기여대상}} to assess {{기대되는변화}}.'}]

BOUND_SCHEMA='''
CREATE TABLE IF NOT EXISTS draft_slot_value(
 draft_id TEXT REFERENCES draft(draft_id),slot_name TEXT,value_text TEXT,
 value_kind TEXT CHECK(value_kind IN ('source_fact','plan','editorial_question','unknown')),
 slot_tense TEXT,source_ids_json TEXT CHECK(json_valid(source_ids_json)),
 PRIMARY KEY(draft_id,slot_name));
CREATE TABLE IF NOT EXISTS draft_sentence_binding(
 draft_id TEXT REFERENCES draft(draft_id),paragraph_index INTEGER,
 sentence_role TEXT CHECK(sentence_role IN ('opening','support','closing')),
 slot_name TEXT,fact_id TEXT REFERENCES applicant_fact(fact_id),slot_tense TEXT,
 PRIMARY KEY(draft_id,paragraph_index,sentence_role,slot_name,fact_id));
CREATE TRIGGER IF NOT EXISTS bound_sentence_status BEFORE INSERT ON draft_sentence_binding
 WHEN NEW.slot_tense IN ('completed','ongoing','planned') AND NEW.slot_tense !=
      (SELECT status FROM applicant_fact WHERE fact_id=NEW.fact_id)
 BEGIN SELECT RAISE(ABORT,'fact_status_does_not_match_slot_tense'); END;
CREATE TRIGGER IF NOT EXISTS bound_sentence_subject BEFORE INSERT ON draft_sentence_binding
 WHEN (SELECT subject_id FROM draft WHERE draft_id=NEW.draft_id) !=
      (SELECT subject_id FROM applicant_fact WHERE fact_id=NEW.fact_id)
 BEGIN SELECT RAISE(ABORT,'fact_subject_does_not_match_draft'); END;
'''


def bound_draft(out):
    payload=e.load(out/'07-draft-input.json');template=payload['template'];contracts=template['slot_contract']
    if len(template['paragraphs']) != len(ENGLISH_FORMS):
        raise ValueError('English forms must match the selected narrative; compile a matching form before binding')
    names=sorted({s for p in ENGLISH_FORMS for part in p.values() for s in re.findall(r'\{\{(.*?)\}\}',part)})
    slots=[{'name':n,**contracts[n],
      'english_grammar':'past_tense_verb_phrase_without_I' if n in {'사전활동','본인행동'} else
         'infinitive_verb_phrase_without_to' if n=='계획방법' else
         'finite_clause_with_subject_and_verb_without_because' if n in {'미해결이유','접근선택이유'} else
         'noun_phrase_or_question_without_punctuation'} for n in names]
    schema=e.obj({'slots':e.arr(e.obj({'name':{'type':'string','enum':names},
      'value_en':e.S,'kind':{'type':'string','enum':['source_fact','plan','editorial_question','unknown']},
      'fact_ids':e.arr(e.S),'reason':e.S}))})
    bindings=e.invoke(out,'07b-fact-to-template-slots-v3-narrative-r2',
      'Fill the provided literal English template slots using ONLY the supplied applicant facts. '
      'Return EVERY slot once. Unknown must have empty value_en and no fact IDs. Do not infer '
      'an artifact, institution, beneficiary, success threshold, comparison output or measured result '
      'not supplied. Completed slot can use only completed facts; planned slot can use only planned facts. '
      'A research question may be an explicitly marked editorial_question based on the facts, '
      'never a new autobiographical assertion. Use exact English grammar required by each slot. '
      'F3 structure and stored 40 notices are already completed; F4/F5 are plans. '
      'The comparison output being compared WITH the two independent reviewers is not supplied: '
      'leave comparison conditions unknown if that output is needed. Decision criteria, beneficiary '
      'and final deliverable are not supplied. Public sources are not applicant facts. '
      'Keep each value concise, usually under 15 English words; action slots may use up to 22 words. '
      'Do not repeat counts or complete sentences across different slot values. '
      'For related work choose the completed representation artifact (F3), not the initial comparison already introduced in paragraph 1. '
      'Interpretive problem statements and research questions may be editorial_question, grounded in supplied facts. '
      'The requirement must follow from the observed problem; explain how the contribution addresses it and why the next question remains. '
      'No U+00B7.',{'applicant_facts':payload['applicant_facts'],'slot_contract':slots,
        'forms':ENGLISH_FORMS},schema)
    if {b['name'] for b in bindings['slots']}!=set(names) or len(bindings['slots'])!=len(names):
        raise ValueError('missing or duplicate slot binding')
    facts={f['fact_id']:f for f in payload['applicant_facts']}
    by_name={b['name']:b for b in bindings['slots']}
    overrides_path=out/'reviewed-slot-overrides.json'
    if overrides_path.exists():
        overrides=e.load(overrides_path)
        if overrides.get('source_template_id')==template['template_id']:
            for name,change in overrides['overrides'].items():
                by_name[name].update(change)
    for name,b in by_name.items():
        if b['kind']=='unknown':
            if b['value_en'] or b['fact_ids']:raise ValueError('unknown slot has invented content')
            continue
        if not b['fact_ids'] or not set(b['fact_ids'])<=facts.keys():raise ValueError('slot source ID')
        expected=contracts[name]['tense']
        if expected in {'completed','ongoing','planned'} and any(facts[f]['status']!=expected for f in b['fact_ids']):
            raise ValueError('slot tense mismatch:'+name)
    method_sources=set(by_name['계획방법']['fact_ids'])
    next_question_sources=set(by_name['연구질문']['fact_ids'])
    if next_question_sources and method_sources and not next_question_sources&method_sources:
        raise ValueError('Next question and proposed method use disjoint plans; review the argument connection')
    original_call=out/'calls/07-draft-v3-official-target/result.json'
    candidates=e.load(original_call if original_call.exists() else out/'07-drafts.json')
    e.dump(out/'07-free-draft-before-slot-gate.json',candidates)
    c=e.db();c.executescript(BOUND_SCHEMA)
    for draft in candidates['drafts']:
        c.execute('DELETE FROM draft_sentence_binding WHERE draft_id=?',(draft['draft_id'],))
        c.execute('DELETE FROM draft_slot_value WHERE draft_id=?',(draft['draft_id'],))
        is_fixture=draft['subject_id']=='synthetic-student'
        chosen=by_name if is_fixture else {n:{'name':n,'kind':'unknown','value_en':'','fact_ids':[]} for n in names}
        paragraphs=[]
        for i,form in enumerate(ENGLISH_FORMS):
            record={'used_fact_ids':[],'bridge_to_next':template['paragraphs'][i]['bridge_meaning'] if i<len(ENGLISH_FORMS)-1 else ''}
            used=set()
            for role,text in form.items():
                role_slots=re.findall(r'\{\{(.*?)\}\}',text)
                def substitute(match):
                    b=chosen[match.group(1)];return b['value_en'] or match.group(0)
                record[role]=re.sub(r'\{\{(.*?)\}\}',substitute,text)
                for name in role_slots:
                    b=chosen[name]
                    for fid in b['fact_ids']:
                        used.add(fid)
                        c.execute('INSERT OR REPLACE INTO draft_sentence_binding VALUES(?,?,?,?,?,?)',
                          (draft['draft_id'],i,role,name,fid,contracts[name]['tense']))
            record['used_fact_ids']=sorted(used);paragraphs.append(record)
        draft['paragraphs']=paragraphs
        unknown=[n for n,b in chosen.items() if b['kind']=='unknown']
        draft['status']='incomplete_required_slots' if unknown else 'initial_draft_ready'
        draft['unbound_required_slots']=unknown
        draft['questions']=[{'slot':n,'question':n+'에 넣을 본인 기록이나 확인 근거는 무엇인가요?'} for n in unknown]
        draft['generation_method']='LLM slot selection + typed fact binding + deterministic English sentence rendering'
        for name,b in chosen.items():
            c.execute('INSERT OR REPLACE INTO draft_slot_value VALUES(?,?,?,?,?,?)',
              (draft['draft_id'],name,b['value_en'] or None,b['kind'],contracts[name]['tense'],json.dumps(b['fact_ids'])))
        c.execute('UPDATE draft SET draft_json=?,prompt_version=? WHERE draft_id=?',
          (json.dumps(draft,ensure_ascii=False),'bound-literal-english-v3-narrative',draft['draft_id']))
        c.execute('DELETE FROM draft_fact_binding WHERE draft_id=?',(draft['draft_id'],))
        for i,p in enumerate(paragraphs):
            for fid in p['used_fact_ids']:c.execute('INSERT INTO draft_fact_binding VALUES(?,?,?)',(draft['draft_id'],i,fid))
    c.commit();e.dump(out/'07-drafts.json',candidates)
    e.dump(out/'07-slot-bindings.json',bindings)
    target=payload['target'];source=c.execute('SELECT * FROM source_document WHERE document_id=?',(target['document_id'],)).fetchone()
    criteria='Research proposals are evaluated for innovative concepts that are relevant to Google’s research areas, as well as aspects of robustness and potential impact to the field.'
    if criteria in source['whole_text']:
        start=source['whole_text'].index(criteria)
        target['reader_judgment_kind']='interpretation_of_official_review_criteria'
        target['review_criteria_ref']={'document_id':source['document_id'],'text_sha256':source['text_sha256'],
          'char_start':start,'char_end':start+len(criteria),'quote':criteria}
    for did in template['source_document_ids']:
        if not any(r['item_id']=='whole:'+did for r in payload['reference_trace']):
            d=c.execute('SELECT * FROM source_document WHERE document_id=?',(did,)).fetchone()
            payload['reference_trace'].append({'item_id':'whole:'+did,'source_url':d['source_url'],
              'source_text_sha256':d['text_sha256'],'use':'inherited_editorial_template_source_not_a_retrieval_hit'})
    e.dump(out/'07-final-provenance.json',payload)
    proof_refs=[{'kind':'official_writing_request','document_id':target['document_id'],
      'source_url':source['source_url'],'source_text_sha256':source['text_sha256'],
      'span':target['source_ref'],'quote':target['official_quote']},
      {'kind':'official_review_criteria','interpretation_label':target['reader_judgment_kind'],
       'source_url':source['source_url'],**target['review_criteria_ref']}]
    for quote in ['All application materials should be submitted in English.',
                  'Research/dissertation proposal (maximum 3 pages, excluding references)']:
        if quote in source['whole_text']:
            start=source['whole_text'].index(quote)
            proof_refs.append({'kind':'official_language_or_length','source_url':source['source_url'],
              'document_id':source['document_id'],'text_sha256':source['text_sha256'],
              'char_start':start,'char_end':start+len(quote),'quote':quote})
    e.dump(out/'07-provenance-index.json',{'official_target':proof_refs,
      'writing_reference_lineage':payload['reference_trace'],
      'other_analyzed_source':{'document_id':'D02','role':'NSF contribution-statement source for the other template; not the Google notice and not used in this research draft'}})
    e.dump(out/'07-active-english-template.json',{'forms':ENGLISH_FORMS,
      'slot_contract':{n:contracts[n] for n in names},'source_template_id':template['template_id'],
      'adaptation':'Context and problem -> requirements -> own task/contribution/scope -> remaining gap and preparation -> next research.'})
    lines=['실행한 초기 초안','합성 사례의 값은 실제 사용자 이력이 아닙니다. 필요한 값이 없으면 빈칸을 남깁니다.','']
    for d in candidates['drafts']:
        lines.extend([d['subject_id']+' / '+d['status'],''])
        for p in d['paragraphs']:
            lines.extend([' '.join(p[k] for k in ['opening','support','closing']),
                          '근거: '+','.join(p['used_fact_ids']),'다음 연결: '+p['bridge_to_next'],''])
        lines.append('미입력 필수값: '+', '.join(d['unbound_required_slots']))
    text='\n'.join(lines);assert len(text)<10000 and '\u00b7' not in text
    (out/'실행초안.txt').write_text(text)
    print('Rendered two draft paths through typed sentence slots',flush=True)


if __name__=='__main__':bound_draft(e.DEFAULT_OUT)
