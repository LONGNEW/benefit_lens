"""Mechanical checks for the executed pipeline. No numeric writing-quality score."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import re
import sys
import benefitlens_end_to_end as e


def check(out):
    c=e.db();errors=[]
    spans=list(c.execute('SELECT * FROM source_span'))
    for span in spans:
        row=c.execute('SELECT whole_text,text_sha256 FROM source_document WHERE document_id=?',(span['document_id'],)).fetchone()
        if row['text_sha256']!=span['text_sha256'] or row['whole_text'][span['char_start']:span['char_end']]!=span['exact_quote']:
            errors.append('source_span_mismatch:'+span['span_id'])
    whole=e.load(out/'calls/04-whole-document-analysis/result.json')['documents']
    delivered={d['document_id']:d for d in e.load(out/'calls/04-whole-document-analysis/input.json')['documents']}
    whole_proofs=[]
    for doc in whole:
        source=c.execute('SELECT * FROM source_document WHERE document_id=?',(doc['document_id'],)).fetchone()
        anchors=[p['exact_anchor'] for s in doc['sections'] for p in s['paragraphs']]
        offsets=[source['whole_text'].index(a) for a in anchors]
        delivered_text=delivered[doc['document_id']]['whole_text']
        if e.digest(delivered_text)!=source['text_sha256']:errors.append('full_input_delivery_hash_mismatch')
        if offsets!=sorted(offsets):errors.append('whole_document_anchor_order:'+doc['document_id'])
        whole_proofs.append({'document_id':doc['document_id'],'text_sha256':source['text_sha256'],
          'input_characters':len(source['whole_text']),'ordered_anchor_offsets':offsets,
          'input_delivery_span':[0,len(delivered_text)],'input_delivery_fraction':1.0,
          'explicit_quote_spans':[[start,start+len(anchor)] for start,anchor in zip(offsets,anchors)],
          'explicit_quote_fraction':round(sum(len(a) for a in anchors)/len(delivered_text),6),
          'uncited_text_status':'present_in_model_input_but_not_an_explicit_quotation; not proof of exhaustive semantic coverage',
          'unit':'analyst_grouped_argument_blocks_not_pdf_paragraph_boundary'})
    templates=e.load(out/'06-templates-v2.json')['templates']; bridge_issues=[]
    for template in templates:
        for i,p in enumerate(template['paragraphs'][:-1]):
            next_p=template['paragraphs'][i+1]
            if p['next_opening']!=next_p['opening']:
                bridge_issues.append({'template':template['template_id'],'paragraph':i,'issue':'declared_next_opening_differs'})
            here=set(re.findall(r'\{\{(.*?)\}\}',p['closing']))
            there=set(re.findall(r'\{\{(.*?)\}\}',next_p['opening']))
            reference=next_p.get('incoming_reference',{})
            declared_reference=(reference.get('slot') in here and reference.get('phrase') in next_p['opening'])
            if not here&there and not declared_reference:
                bridge_issues.append({'template':template['template_id'],'paragraph':i,'issue':'no_bound_bridge_reference'})
    retrieval=e.load(out/'05-retrieval-reviewed.json'); artifact_hits=[]
    for query in retrieval:
        for hit in query['material_hits']:
            if e.clean_fragment(hit['source_quote'])[0] is None:artifact_hits.append(hit['item_id'])
        for hit in query['whole_document_hits']:
            if hit['document_id'] not in {d['document_id'] for d in whole}:errors.append('whole_hit_not_analyzed')
    drafts=e.load(out/'07-drafts.json')['drafts'];draft_checks=[]
    for draft in drafts:
        text='\n'.join(' '.join(p[k] for k in ['opening','support','closing']) for p in draft['paragraphs'])
        used={f for p in draft['paragraphs'] for f in p['used_fact_ids']}
        forbidden=[token for token in ['DISTAL','Rohan','Kalina','VideoLAN','NSF GRFP','Stanford'] if token in text]
        numeric=set(re.findall(r'\d+(?:\.\d+)?',text))
        if forbidden:errors.append('public_fact_leak:'+draft['draft_id'])
        if draft['subject_id']=='real-user' and (used or '{{' not in text):errors.append('unknown_user_facts_invented')
        if draft['subject_id']=='synthetic-student' and not numeric <= {'40','12','100','1','2','3','4','5'}:errors.append('unsupported_fixture_number')
        if len(text)>10000 or '\u00b7' in text:errors.append('draft_document_format')
        draft_checks.append({'draft_id':draft['draft_id'],'subject_id':draft['subject_id'],
          'paragraphs':len(draft['paragraphs']),'used_fact_ids':sorted(used),
          'numeric_tokens':sorted(numeric),'public_fact_leaks':forbidden,'characters':len(text)})
    fk=list(c.execute('PRAGMA foreign_key_check'))
    if fk:errors.append('foreign_key_error')
    counts={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in [
      'notice','notice_document','condition_observation','condition_dimension','condition_dimension_link',
      'normalized_condition','semantic_document','strategy_template','draft','draft_fact_binding']}
    result={'counts':counts,'source_spans_checked':len(spans),'whole_document_proofs':whole_proofs,
      'notices_with_condition_observations':c.execute('SELECT count(distinct notice_id) FROM condition_observation').fetchone()[0],
      'legacy_condition_roles':dict(c.execute('SELECT role,count(*) FROM condition_observation GROUP BY role').fetchall()),
      'normalized_condition_roles':dict(c.execute('SELECT role,count(*) FROM normalized_condition GROUP BY role').fetchall()),
      'notice_coverage_reasons':dict(c.execute('SELECT reason_code,count(*) FROM notice_condition_coverage GROUP BY reason_code').fetchall()),
      'notices_without_coverage_reason':c.execute('SELECT count(*) FROM notice n WHERE NOT EXISTS(SELECT 1 FROM notice_condition_coverage v WHERE v.notice_id=n.notice_id)').fetchone()[0],
      'enforced_field_role_triggers':[x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'field_role_boundary_%'")],
      'focused_test_evidence':{'result_file':str(out/'test-results.txt'),'cases':22,
        'field_role_test':'test_benefit_cannot_be_inserted_as_eligibility',
        'tense_test':'test_completed_fact_cannot_fill_planned_sentence_slot'},
      'template_bridge_issues':bridge_issues,'page_furniture_in_final_top_hits':artifact_hits,
      'draft_checks':draft_checks,'foreign_key_errors':len(fk),'errors':errors,
      'scope':'source bindings, structure and fact identifiers; not calibrated semantic writing quality'}
    e.dump(out/'09-diagnostics.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    result=check(Path(sys.argv[1]) if len(sys.argv)>1 else e.DEFAULT_OUT)
    if result['errors'] or result['page_furniture_in_final_top_hits']:sys.exit(1)
