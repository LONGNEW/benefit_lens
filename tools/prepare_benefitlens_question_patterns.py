"""Prepare question-scoped analysis inputs without requiring a whole application."""
import argparse
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json

from benefitlens_question_evidence import (
    QUESTION_INTENTS, normalize_source_text, validate_question_evidence,
)
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json

RUBRIC=Path(__file__).resolve().parents[1]/'pipeline/config/operational_writing_pattern_prompt_v5.txt'


def evidence_context(text,row):
    """Keep short source bodies whole; otherwise include full adjacent paragraphs."""
    text=normalize_source_text(text)
    start,end=row['locator']['char_start'],row['locator']['char_end']
    if len(text)<=12000:
        left,right=0,len(text)
    else:
        left=text.rfind('\n\n',0,start)
        left=0 if left<0 else text.rfind('\n\n',0,left)
        left=0 if left<0 else left+2
        right=text.find('\n\n',end)
        right=len(text) if right<0 else text.find('\n\n',right+2)
        right=len(text) if right<0 else right
    return {'document_id':row['document_id'],'source_char_start':left,'source_char_end':right,
            'context_scope':'complete_captured_body' if left==0 and right==len(text) else 'quote_and_adjacent_paragraphs',
            'text':text[left:right]}


def prepare_inputs(bundle,document_texts,*,max_characters=100000):
    validation=validate_question_evidence(bundle,document_texts)
    documents={d['document_id']:d for d in bundle['source_documents']}
    result=[]
    for intent in QUESTION_INTENTS:
        selected=[r for r in bundle['question_evidence'] if r['question_intent']==intent
                  and r['semantic_review']['state']!='rejected']
        current=[]
        def payload(rows):
            ids={r['document_id'] for r in rows}
            ids|={r['exact_prompt']['document_id'] for r in rows if r['exact_prompt']}
            contexts={}
            for row in rows:
                context=evidence_context(document_texts[row['document_id']],row)
                key=(context['document_id'],context['source_char_start'],context['source_char_end'])
                contexts[key]=context
                if row['exact_prompt']:
                    prompt=row['exact_prompt']
                    context=evidence_context(document_texts[prompt['document_id']],prompt)
                    key=(context['document_id'],context['source_char_start'],context['source_char_end'])
                    contexts[key]=context
            return {'version':'question-pattern-input-v1','question_intent':intent,
              'source_documents':[documents[i] for i in sorted(ids)],
              'question_evidence':deepcopy(rows),'source_contexts':list(contexts.values()),
              'whole_application_required':False,'known_outcome_required':False,
              'source_analysis_permitted':True,'live_application_drafting_permitted':False,
              'instruction_scope':'question-level source observations and candidate structure only',
              'model_called':False}
        for row in selected:
            candidate=payload(current+[row])
            if len(json.dumps(candidate,ensure_ascii=False))>max_characters:
                if not current:
                    raise ValueError('A complete evidence context exceeds batch size; explicitly split that source context before retrying.')
                result.append(payload(current));current=[]
                candidate=payload([row])
                if len(json.dumps(candidate,ensure_ascii=False))>max_characters:
                    raise ValueError('A complete evidence context exceeds batch size; explicitly split that source context before retrying.')
            current.append(row)
        if current:result.append(payload(current))
    return result,validation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=CACHE/'question-pattern-inputs-v1')
    parser.add_argument('--max-characters',type=int,default=100000)
    args=parser.parse_args()
    if not 4000<=args.max_characters<=500000:parser.error('Batch size must be 4000..500000 characters')
    args.root.resolve().relative_to(CACHE.resolve())
    bundle=json.loads(args.bundle.read_text());texts={}
    for doc in bundle['source_documents']:
        path=Path(doc['text_ref']);path.resolve().relative_to(CACHE.resolve())
        texts[doc['document_id']]=read_source_bytes(path).decode('utf8')
    batches,validation=prepare_inputs(bundle,texts,max_characters=args.max_characters)
    args.root.mkdir(parents=True,exist_ok=True)
    rubric=RUBRIC.read_text()
    outputs=[]
    for i,batch in enumerate(batches,1):
        path=args.root/(f'{i:03d}-'+batch['question_intent']+'.json')
        atomic_json(path,{'rubric':rubric,'input':batch})
        outputs.append({'path':str(path),'question_intent':batch['question_intent'],
                        'evidence_records':len(batch['question_evidence'])})
    summary={'at_kst':datetime.now(KST).isoformat(),'validation':validation,
             'batches':outputs,'model_called':False,'draft_generation_ready':False,
             'collection_unit':'question_evidence','whole_application_required':False}
    atomic_json(args.root/'status.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
